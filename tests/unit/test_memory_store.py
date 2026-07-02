from __future__ import annotations

from pathlib import Path

from zingclaw.memory import MemoryStore


def test_memory_store_reads_writes_and_appends_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=10)

    assert store.read_memory() == ""

    store.write_memory("- user prefers concise replies")
    assert "concise replies" in store.read_memory()
    assert "Long-term Memory" in store.get_memory_context()

    store.save_memory("project is migrating to a bus-based architecture")
    assert "bus-based architecture" in store.read_memory()

    first = store.append_history("general archived summary")
    second = store.append_history("session archived summary", session_key="cli:demo")
    third = store.append_history("other session archived summary", session_key="cli:other")

    all_entries = store.read_history_entries()
    demo_entries = store.read_history_entries(session_key="cli:demo")
    recent_demo = store.read_recent_history_for_prompt(session_key="cli:demo")

    assert [entry["cursor"] for entry in all_entries] == [first, second, third]
    assert [entry["content"] for entry in demo_entries] == ["session archived summary"]
    assert [entry["content"] for entry in recent_demo] == ["session archived summary"]
    assert "Recent Archived History" in store.render_recent_history_section(session_key="cli:demo")
    history_markdown = store.history_markdown_file.read_text(encoding="utf-8")
    assert "### " in history_markdown
    assert "Session: `cli:demo`" in history_markdown
    assert "session archived summary" in history_markdown

    store.set_last_dream_cursor(second)
    assert store.get_last_dream_cursor() == second
    assert [entry["cursor"] for entry in store.read_unprocessed_history(since_cursor=second)] == [third]


def test_memory_store_compacts_history_jsonl_to_recent_entries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=2)

    store.append_history("entry-1")
    store.append_history("entry-2")
    store.append_history("entry-3")

    entries = store.read_history_entries()
    history_markdown = store.history_markdown_file.read_text(encoding="utf-8")

    assert [entry["content"] for entry in entries] == ["entry-2", "entry-3"]
    assert "entry-1" not in history_markdown
    assert "entry-2" in history_markdown
    assert "entry-3" in history_markdown


def test_memory_store_rebuilds_history_markdown_from_existing_jsonl(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    history_file = memory_dir / "history.jsonl"
    history_file.write_text(
        '{"cursor": 1, "timestamp": "2026-07-01 10:00", "content": "summary one", "session_key": "cli:demo"}\n'
        '{"cursor": 2, "timestamp": "2026-07-01 10:05", "content": "summary two"}\n',
        encoding="utf-8",
    )

    store = MemoryStore(memory_dir, max_history_entries=10)

    history_markdown = store.history_markdown_file.read_text(encoding="utf-8")
    assert "summary one" in history_markdown
    assert "Session: `cli:demo`" in history_markdown
    assert "summary two" in history_markdown
