"""End-to-end sync tests against a fake, in-memory Notion client (no real HTTP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from notion_obsidian_sync.config import Config
from notion_obsidian_sync.links import normalize_id
from notion_obsidian_sync.state import StateStore
from notion_obsidian_sync.sync import discover_workspace, fetch_block_tree, run_sync


class FakeNotionClient:
    """Duck-typed stand-in for NotionClient covering only what sync.py calls."""

    def __init__(
        self,
        pages: dict,
        children: dict,
        databases: dict | None = None,
        data_sources: dict | None = None,
        rows: dict | None = None,
        data_source_objects: dict | None = None,
        blocks: dict | None = None,
        forbid_block_children: bool = False,
    ) -> None:
        self.pages = pages
        self.children = children
        self.databases = databases or {}
        self.data_sources = data_sources or {}  # database_id -> [data_source_id, ...]
        self.rows = rows or {}  # data_source_id -> [row page dict, ...]
        # data_source_id -> searchable data_source object ({id, title, ...}),
        # as returned by /v1/search with filter value "data_source".
        self.data_source_objects = data_source_objects or {}
        self.blocks = blocks or {}  # block_id -> block dict (for GET /v1/blocks/{id})
        # When True, get_block_children raises instead of returning results —
        # used to assert a discovery path never walks the block tree.
        self.forbid_block_children = forbid_block_children
        self.block_children_calls: list[str] = []

    def get_page(self, page_id: str) -> dict:
        return self.pages[page_id]

    def get_block(self, block_id: str) -> dict:
        return self.blocks[block_id]

    def get_block_children(self, block_id: str) -> list[dict]:
        self.block_children_calls.append(block_id)
        if self.forbid_block_children:
            raise AssertionError(
                f"get_block_children({block_id!r}) should not have been called"
            )
        return self.children.get(block_id, [])

    def get_database(self, database_id: str) -> dict:
        return self.databases[database_id]

    def resolve_data_source_ids(self, database_id: str) -> list[str]:
        return self.data_sources.get(database_id, [])

    def query_data_source(self, data_source_id: str, filter_=None) -> list[dict]:
        return self.rows.get(data_source_id, [])

    def search(self, query: str = "", filter_: dict | None = None) -> list[dict]:
        value = (filter_ or {}).get("value")
        if value == "data_source":
            return list(self.data_source_objects.values())
        if value == "page":
            return list(self.pages.values())
        return [*self.pages.values(), *self.data_source_objects.values()]


def _title_prop(text: str) -> dict:
    return {"title": {"type": "title", "title": [{"type": "text", "plain_text": text}]}}


def _page(page_id: str, title: str, last_edited: str, parent: dict | None = None) -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "last_edited_time": last_edited,
        "parent": parent or {"type": "workspace"},
        "properties": _title_prop(title),
    }


def _child_page_block(page_id: str, title: str) -> dict:
    return {
        "id": page_id,
        "type": "child_page",
        "has_children": False,
        "child_page": {"title": title},
    }


def _paragraph_block(text: str) -> dict:
    return {
        "id": f"para-{text[:8]}",
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": [{"type": "text", "plain_text": text, "href": None,
                                      "annotations": {"bold": False, "italic": False,
                                                       "strikethrough": False, "underline": False,
                                                       "code": False}}]},
    }


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _make_config(tmp_path: Path, vault: Path, orphan_policy: str = "keep") -> Config:
    return Config(
        notion_token="fake-token",
        obsidian_vault_path=vault,
        obsidian_sync_folder="Notion",
        notion_root_page_id="r1",
        orphan_policy=orphan_policy,
        project_dir=tmp_path,
    )


def _basic_fixture(root_edited="2026-01-01T00:00:00.000Z", alpha_edited="2026-01-01T00:00:00.000Z"):
    pages = {
        "r1": _page("r1", "Root", root_edited),
        "a1": _page("a1", "Alpha", alpha_edited, parent={"type": "page_id", "page_id": "r1"}),
        "b1": _page(
            "b1", "Beta", "2026-01-01T00:00:00.000Z", parent={"type": "page_id", "page_id": "r1"}
        ),
    }
    children = {
        "r1": [_child_page_block("a1", "Alpha"), _child_page_block("b1", "Beta")],
        "a1": [_paragraph_block("Alpha content")],
        "b1": [_paragraph_block("Beta content")],
    }
    return pages, children


def test_first_sync_creates_all_pages(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.created == 3
    assert result.updated == 0
    assert result.skipped == 0
    assert result.errors == 0

    root_md = (config.sync_root / "Root.md").read_text(encoding="utf-8")
    assert "managed_by: notion-obsidian-sync" in root_md
    assert "notion_id: r1" in root_md
    # child pages should be rewritten as Obsidian wikilinks
    assert "[[Notion/Root/Alpha]]" in root_md
    assert "[[Notion/Root/Beta]]" in root_md

    alpha_md = (config.sync_root / "Root" / "Alpha.md").read_text(encoding="utf-8")
    assert "Alpha content" in alpha_md


def test_second_sync_with_no_changes_skips_everything(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)
        result2 = run_sync(config, client, state)

    assert result2.created == 0
    assert result2.updated == 0
    assert result2.skipped == 3


def test_idempotence_does_not_touch_file_mtimes(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)
        root_path = config.sync_root / "Root.md"
        mtime_before = root_path.stat().st_mtime_ns
        run_sync(config, client, state)
        mtime_after = root_path.stat().st_mtime_ns

    assert mtime_before == mtime_after


def test_dry_run_writes_nothing(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state, dry_run=True)

    assert result.created == 3
    assert not config.sync_root.exists() or list(config.sync_root.rglob("*.md")) == []
    with StateStore(config.state_db_path) as state:
        assert state.count() == 0


def test_changed_last_edited_time_triggers_update(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)

        pages["a1"] = _page(
            "a1", "Alpha", "2026-02-01T00:00:00.000Z", parent={"type": "page_id", "page_id": "r1"}
        )
        result2 = run_sync(config, client, state)

    assert result2.updated == 1
    assert result2.skipped == 2


def test_title_rename_moves_file_without_duplicate(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)

        pages["a1"] = _page(
            "a1",
            "Alpha Renamed",
            "2026-02-01T00:00:00.000Z",
            parent={"type": "page_id", "page_id": "r1"},
        )
        children["r1"] = [
            _child_page_block("a1", "Alpha Renamed"),
            _child_page_block("b1", "Beta"),
        ]
        run_sync(config, client, state)

    assert not (config.sync_root / "Root" / "Alpha.md").exists()
    assert (config.sync_root / "Root" / "Alpha Renamed.md").exists()


def test_orphan_policy_keep_leaves_file_untouched(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault, orphan_policy="keep")

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)
        children["r1"] = [_child_page_block("a1", "Alpha")]  # Beta disappears
        result2 = run_sync(config, client, state)

    assert result2.orphan_kept == 1
    assert (config.sync_root / "Root" / "Beta.md").exists()


def test_orphan_policy_archive_moves_file(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault, orphan_policy="archive")

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)
        children["r1"] = [_child_page_block("a1", "Alpha")]
        result2 = run_sync(config, client, state)

    assert result2.archived == 1
    assert not (config.sync_root / "Root" / "Beta.md").exists()
    assert (config.sync_root / "_Archive" / "Root" / "Beta.md").exists()


def test_orphan_policy_delete_removes_file(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault, orphan_policy="delete")

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)
        children["r1"] = [_child_page_block("a1", "Alpha")]
        result2 = run_sync(config, client, state)

    assert result2.deleted == 1
    assert not (config.sync_root / "Root" / "Beta.md").exists()


def test_local_modification_is_backed_up_before_overwrite(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)

        alpha_path = config.sync_root / "Root" / "Alpha.md"
        original = alpha_path.read_text(encoding="utf-8")
        alpha_path.write_text(original + "\nHand-edited line.\n", encoding="utf-8")

        pages["a1"] = _page(
            "a1", "Alpha", "2026-02-01T00:00:00.000Z", parent={"type": "page_id", "page_id": "r1"}
        )
        result2 = run_sync(config, client, state)

    assert result2.conflicts == 1
    backup = config.sync_root / "_Conflicts" / "Root" / "Alpha.md"
    assert backup.exists()
    assert "Hand-edited line." in backup.read_text(encoding="utf-8")
    assert "Hand-edited line." not in alpha_path.read_text(encoding="utf-8")


def test_unmanaged_file_is_not_overwritten(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    config.sync_root.mkdir(parents=True)
    (config.sync_root / "Root.md").write_text(
        "# My own notes\nNot managed by the tool.\n", encoding="utf-8"
    )

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.conflicts == 1
    assert "My own notes" in (config.sync_root / "Root.md").read_text(encoding="utf-8")


def test_reset_state_recovers_existing_managed_files(tmp_path, vault):
    pages, children = _basic_fixture()
    client = FakeNotionClient(pages, children)
    config = _make_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        run_sync(config, client, state)

    config.state_db_path.unlink()

    with StateStore(config.state_db_path) as state:
        assert state.count() == 0
        result = run_sync(config, client, state)
        # rehydrated from disk, so pages are recognized (UPDATE) rather than duplicated
        assert result.created == 0
        assert result.updated == 3
        assert state.count() == 3

    assert list((config.sync_root / "Root").glob("*.md"))  # no duplicate files created
    assert not list(config.sync_root.glob("Root (*.md"))


def _db(db_id: str, title: str) -> dict:
    return {"id": db_id, "title": [{"type": "text", "plain_text": title}]}


def _make_workspace_config(tmp_path: Path, vault: Path) -> Config:
    return Config(
        notion_token="fake-token",
        obsidian_vault_path=vault,
        obsidian_sync_folder="Notion",
        notion_sync_workspace=True,
        project_dir=tmp_path,
    )


def test_workspace_mode_covers_top_level_pages_and_databases(tmp_path, vault):
    # A top-level page (and its nested child) plus a database with one row,
    # both accessible to the integration but never explicitly configured.
    # Under the 2025-09-03 API, a database row's parent is a data_source_id
    # (not a database_id) — /v1/search(filter=page) returns it directly, no
    # block-tree walk or query_data_source call needed to discover it.
    pages = {
        "p1": _page("p1", "Standalone", "2026-01-01T00:00:00.000Z", parent={"type": "workspace"}),
        "p2": _page(
            "p2", "Nested Child", "2026-01-01T00:00:00.000Z",
            parent={"type": "page_id", "page_id": "p1"},
        ),
        "row1": _page(
            "row1", "Row One", "2026-01-01T00:00:00.000Z",
            parent={"type": "data_source_id", "data_source_id": "ds1"},
        ),
    }
    data_source_objects = {"ds1": _db("ds1", "My Database")}

    client = FakeNotionClient(pages, {}, data_source_objects=data_source_objects)
    config = _make_workspace_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.created == 3
    assert (config.sync_root / "Standalone.md").exists()
    assert (config.sync_root / "Standalone" / "Nested Child.md").exists()
    assert (config.sync_root / "My Database" / "Row One.md").exists()


def test_workspace_mode_places_page_with_inaccessible_parent_at_top(tmp_path, vault):
    # "orphan" is accessible but its parent "missing-parent" was never shared
    # with the integration, so it never shows up in search results either.
    pages = {
        "orphan": _page(
            "orphan", "Orphan Page", "2026-01-01T00:00:00.000Z",
            parent={"type": "page_id", "page_id": "missing-parent"},
        ),
    }
    client = FakeNotionClient(pages, {})
    config = _make_workspace_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.created == 1
    assert (config.sync_root / "Orphan Page.md").exists()


def test_workspace_mode_resolves_page_nested_inside_a_block(tmp_path, vault):
    # A page whose parent is a block (e.g. nested inside a toggle/column on
    # another page), not a page directly — needs one GET /v1/blocks/{id} hop.
    pages = {
        "p1": _page("p1", "Container", "2026-01-01T00:00:00.000Z", parent={"type": "workspace"}),
        "nested": _page(
            "nested", "Deep Page", "2026-01-01T00:00:00.000Z",
            parent={"type": "block_id", "block_id": "toggle-block"},
        ),
    }
    blocks = {
        "toggle-block": {"id": "toggle-block", "parent": {"type": "page_id", "page_id": "p1"}}
    }

    client = FakeNotionClient(pages, {}, blocks=blocks)
    config = _make_workspace_config(tmp_path, vault)

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.created == 2
    assert (config.sync_root / "Container" / "Deep Page.md").exists()


def test_workspace_mode_respects_sync_property_filter(tmp_path, vault):
    checked_row = _page("row-yes", "Include Me", "2026-01-01T00:00:00.000Z",
                         parent={"type": "data_source_id", "data_source_id": "ds1"})
    checked_row["properties"]["Sync Obsidian"] = {"type": "checkbox", "checkbox": True}
    unchecked_row = _page("row-no", "Exclude Me", "2026-01-01T00:00:00.000Z",
                           parent={"type": "data_source_id", "data_source_id": "ds1"})
    unchecked_row["properties"]["Sync Obsidian"] = {"type": "checkbox", "checkbox": False}

    data_source_objects = {"ds1": _db("ds1", "Filtered DB")}
    pages = {"row-yes": checked_row, "row-no": unchecked_row}

    client = FakeNotionClient(pages, {}, data_source_objects=data_source_objects)
    config = Config(
        notion_token="fake-token",
        obsidian_vault_path=vault,
        obsidian_sync_folder="Notion",
        notion_sync_workspace=True,
        notion_sync_property="Sync Obsidian",
        project_dir=tmp_path,
    )

    with StateStore(config.state_db_path) as state:
        result = run_sync(config, client, state)

    assert result.created == 1
    assert (config.sync_root / "Filtered DB" / "Include Me.md").exists()
    assert not (config.sync_root / "Filtered DB" / "Exclude Me.md").exists()


def test_workspace_discovery_never_walks_the_block_tree():
    # Regression guard: discover_workspace() must reconstruct the whole
    # hierarchy from /v1/search results alone (parent pointers), never by
    # calling get_block_children — on a real workspace with hundreds of
    # deeply-nested pages, walking every block tree just to *find* pages
    # made discovery itself take many minutes before any syncing even began.
    pages = {
        "p1": _page("p1", "Root", "2026-01-01T00:00:00.000Z", parent={"type": "workspace"}),
        "p2": _page(
            "p2", "Child", "2026-01-01T00:00:00.000Z",
            parent={"type": "page_id", "page_id": "p1"},
        ),
        "row1": _page(
            "row1", "Row", "2026-01-01T00:00:00.000Z",
            parent={"type": "data_source_id", "data_source_id": "ds1"},
        ),
        "deep": _page(
            "deep", "Deep", "2026-01-01T00:00:00.000Z",
            parent={"type": "block_id", "block_id": "toggle-block"},
        ),
    }
    blocks = {
        "toggle-block": {"id": "toggle-block", "parent": {"type": "page_id", "page_id": "p2"}}
    }
    data_source_objects = {"ds1": _db("ds1", "My Database")}

    client = FakeNotionClient(
        pages,
        {},
        blocks=blocks,
        data_source_objects=data_source_objects,
        forbid_block_children=True,
    )

    discovered = discover_workspace(client, sync_property="")

    by_id = {normalize_id(dp.notion_id): dp for dp in discovered}
    assert set(by_id) == {"p1", "p2", "row1", "deep"}
    assert by_id["p1"].folder_chain == ()
    assert by_id["p2"].folder_chain == ("Root",)
    assert by_id["row1"].folder_chain == ("My Database",)
    assert by_id["deep"].folder_chain == ("Root", "Child")


def test_fetch_block_tree_does_not_recurse_into_child_pages():
    # Regression guard: a child_page block referenced from a parent page's
    # content must not be recursively fetched — it's a separate Notion page,
    # rendered and synced on its own. Recursing into it here meant rendering
    # a single top-level page could silently re-fetch the entire nested
    # subtree beneath it.
    child_block = _child_page_block("child", "Child")
    child_block["has_children"] = True
    children = {
        "parent": [child_block],
        "child": [_paragraph_block("should never be fetched")],
    }
    client = FakeNotionClient({}, children)

    tree = fetch_block_tree(client, "parent")

    assert "child" not in client.block_children_calls
    assert "_children" not in tree[0]
