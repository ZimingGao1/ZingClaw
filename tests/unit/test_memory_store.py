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


def test_memory_store_compacts_history_jsonl_to_recent_entries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=2)

    store.append_history("entry-1")
    store.append_history("entry-2")
    store.append_history("entry-3")

    entries = store.read_history_entries()

    assert [entry["content"] for entry in entries] == ["entry-2", "entry-3"]
