from zingclaw.providers import find_provider_spec, list_provider_specs, provider_names, require_provider_spec


def test_provider_registry_lists_builtin_openai_compat_provider() -> None:
    specs = list_provider_specs()

    assert len(specs) == 1
    assert specs[0].name == "openai_compat"
    assert specs[0].backend == "openai_compat"
    assert specs[0].supports_streaming is True
    assert specs[0].supports_tools is True
    assert specs[0].supports_reasoning is True
    assert provider_names() == ("openai_compat",)


def test_provider_registry_accepts_hyphenated_alias_for_openai_compat() -> None:
    spec = find_provider_spec("openai-compat")

    assert spec is not None
    assert spec.name == "openai_compat"
    assert spec.label == "OpenAI-Compatible"


def test_provider_registry_raises_clear_error_for_unknown_provider() -> None:
    try:
        require_provider_spec("not-real")
    except ValueError as exc:
        assert str(exc) == "Unknown provider 'not-real'. Available providers: openai_compat"
    else:  # pragma: no cover - defensive branch
        raise AssertionError("require_provider_spec() should reject unknown providers")
