from pathlib import Path

from notion_obsidian_sync.state import PageRecord, StateStore, now_iso


def _record(
    notion_id="page-1", file_path="Note.md", last_edited="2026-01-01T00:00:00.000Z"
) -> PageRecord:
    return PageRecord(
        notion_id=notion_id,
        title="Note",
        file_path=file_path,
        parent_id=None,
        notion_url="https://notion.so/page-1",
        notion_last_edited_time=last_edited,
        last_synced_at=now_iso(),
        content_checksum="abc123",
    )


def test_upsert_and_get(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert(_record())
    fetched = store.get("page-1")
    assert fetched is not None
    assert fetched.file_path == "Note.md"
    store.close()


def test_upsert_is_idempotent_update(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert(_record(last_edited="2026-01-01T00:00:00.000Z"))
    store.upsert(_record(last_edited="2026-02-01T00:00:00.000Z"))
    assert store.count() == 1
    assert store.get("page-1").notion_last_edited_time == "2026-02-01T00:00:00.000Z"
    store.close()


def test_delete_removes_record(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert(_record())
    store.delete("page-1")
    assert store.get("page-1") is None
    store.close()


def test_all_ids_and_all_records(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert(_record(notion_id="a", file_path="A.md"))
    store.upsert(_record(notion_id="b", file_path="B.md"))
    assert store.all_ids() == {"a", "b"}
    assert {r.file_path for r in store.all_records()} == {"A.md", "B.md"}
    store.close()


def test_state_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "state.sqlite"
    store1 = StateStore(db_path)
    store1.upsert(_record())
    store1.close()

    store2 = StateStore(db_path)
    assert store2.get("page-1") is not None
    store2.close()
