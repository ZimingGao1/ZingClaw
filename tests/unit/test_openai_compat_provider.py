from __future__ import annotations

from types import SimpleNamespace

import pytest

from zingclaw.providers.openai_compat import OpenAICompatProvider


class FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None, stream_chunks=None):
        self.response = response
        self.error = error
        self.stream_chunks = stream_chunks
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return FakeStream(self.stream_chunks or [])
        return self.response


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:  # pragma: no cover - protocol glue
            raise StopAsyncIteration from exc


def _fake_client(completions: FakeCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


@pytest.mark.asyncio
async def test_openai_compat_provider_parses_text_response() -> None:
    completions = FakeCompletions(
        response=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="hello",
                        reasoning_content="thoughts",
                        tool_calls=[],
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4, total_tokens=16),
        )
    )
    provider = OpenAICompatProvider(api_key="key", api_base="https://example.com/v1")
    provider._client = _fake_client(completions)

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "hello"
    assert response.reasoning_content == "thoughts"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 16
    assert completions.last_kwargs["model"] == provider.get_default_model()


@pytest.mark.asyncio
async def test_openai_compat_provider_parses_tool_calls() -> None:
    completions = FakeCompletions(
        response=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                function=SimpleNamespace(
                                    name="list_dir",
                                    arguments='{"path": "."}',
                                ),
                            )
                        ],
                    ),
                )
            ],
            usage=None,
        )
    )
    provider = OpenAICompatProvider(api_key="key", api_base="https://example.com/v1")
    provider._client = _fake_client(completions)

    response = await provider.chat(messages=[{"role": "user", "content": "inspect"}])

    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "list_dir"
    assert response.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_openai_compat_provider_preserves_reasoning_messages_for_follow_up_turns() -> None:
    completions = FakeCompletions(
        response=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="done", tool_calls=[]),
                )
            ],
            usage=None,
        )
    )
    provider = OpenAICompatProvider(api_key="key", api_base="https://api.moonshot.cn/v1")
    provider._client = _fake_client(completions)

    await provider.chat(
        messages=[
            {
                "role": "assistant",
                "content": "final answer",
                "reasoning_content": "internal reasoning",
                "partial": True,
            }
        ]
    )

    assert completions.last_kwargs is not None
    assert completions.last_kwargs["messages"][0]["reasoning_content"] == "internal reasoning"
    assert completions.last_kwargs["messages"][0]["partial"] is True


@pytest.mark.asyncio
async def test_openai_compat_provider_omits_temperature_for_kimi_fixed_temperature_models() -> None:
    completions = FakeCompletions(
        response=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", tool_calls=[]),
                )
            ],
            usage=None,
        )
    )
    provider = OpenAICompatProvider(
        api_key="key",
        api_base="https://api.moonshot.cn/v1",
        default_model="kimi-k2.5",
    )
    provider._client = _fake_client(completions)

    await provider.chat(messages=[{"role": "user", "content": "hi"}], temperature=0.2)

    assert completions.last_kwargs is not None
    assert "temperature" not in completions.last_kwargs


@pytest.mark.asyncio
async def test_openai_compat_provider_returns_error_response_on_exception() -> None:
    completions = FakeCompletions(error=RuntimeError("boom"))
    provider = OpenAICompatProvider(api_key="key", api_base="https://example.com/v1")
    provider._client = _fake_client(completions)

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.finish_reason == "error"
    assert "boom" in (response.content or "")


@pytest.mark.asyncio
async def test_openai_compat_provider_streams_text_deltas() -> None:
    completions = FakeCompletions(
        stream_chunks=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="hel"),
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        delta=SimpleNamespace(content="lo"),
                    )
                ],
                usage=None,
            ),
        ]
    )
    provider = OpenAICompatProvider(api_key="key", api_base="https://example.com/v1")
    provider._client = _fake_client(completions)
    chunks: list[str] = []

    async def on_text_delta(delta: str) -> None:
        chunks.append(delta)

    response = await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        on_text_delta=on_text_delta,
    )

    assert completions.last_kwargs is not None
    assert completions.last_kwargs["stream"] is True
    assert response.content == "hello"
    assert "".join(chunks) == "hello"


@pytest.mark.asyncio
async def test_openai_compat_provider_streams_tool_calls() -> None:
    completions = FakeCompletions(
        stream_chunks=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(name="list_dir", arguments='{"path"'),
                                )
                            ]
                        ),
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    function=SimpleNamespace(arguments=': "."}'),
                                )
                            ]
                        ),
                    )
                ],
                usage=None,
            ),
        ]
    )
    provider = OpenAICompatProvider(api_key="key", api_base="https://example.com/v1")
    provider._client = _fake_client(completions)

    response = await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        on_text_delta=lambda delta: None,
    )

    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "list_dir"
    assert response.tool_calls[0].arguments == {"path": "."}
