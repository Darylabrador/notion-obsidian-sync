"""Sync orchestration: discovery, incremental diffing, rendering, and safe
writes. This is the only module that ties Notion reads to Obsidian writes
together; every write goes through `paths.py`'s sandboxing helpers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assets import AssetManager
from .config import Config
from .links import PageIndex, normalize_id
from .logging_config import log_action
from .markdown import RenderContext, blocks_to_markdown
from .metadata import (
    compose_note,
    get_title,
    parse_frontmatter,
    properties_to_frontmatter,
    relation_property_ids,
    rich_text_plain,
)
from .notion_client import NotionAPIError, NotionClient
from .paths import (
    PathSecurityError,
    atomic_write_text,
    resolve_within,
    sanitize_filename,
)
from .state import PageRecord, StateStore, now_iso
from .utils import sha256_text

logger = logging.getLogger("notion_obsidian_sync")

MANAGED_BY = "notion-obsidian-sync"

# phase ("resolve" | "sync"), current index (1-based), total
ProgressCallback = Callable[[str, int, int], None]


class SyncAbort(Exception):
    """Raised for configuration/connectivity problems that stop the whole run."""


@dataclass
class DiscoveredPage:
    notion_id: str
    title: str
    folder_chain: tuple[str, ...]
    page_object: dict[str, Any] | None = None  # pre-fetched, e.g. from a DB query


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    archived: int = 0
    deleted: int = 0
    orphan_kept: int = 0
    conflicts: int = 0
    errors: int = 0
    dry_run: bool = False
    pages_seen: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.errors == 0


# -- block tree fetching ----------------------------------------------------------


_NON_RECURSING_BLOCK_TYPES = {"child_page", "child_database"}


def fetch_block_tree(client: NotionClient, block_id: str) -> list[dict[str, Any]]:
    """Recursively fetch a block's children (and their children, ...), attaching
    each block's children under `"_children"`.

    `child_page` and `child_database` blocks are never recursed into: they
    reference separate Notion objects that get discovered and rendered as
    their own notes independently. Descending into them here would mean
    rendering just one page silently re-fetches the entire nested subtree
    beneath it — on a deep workspace that turns a single page's render into
    a near-complete crawl of everything under it.
    """
    children = client.get_block_children(block_id)
    for block in children:
        if block.get("has_children") and block.get("type") not in _NON_RECURSING_BLOCK_TYPES:
            block["_children"] = fetch_block_tree(client, block["id"])
    return children


# -- discovery ----------------------------------------------------------------------


def _walk_child_pages(
    client: NotionClient,
    block_id: str,
    folder_chain: tuple[str, ...],
    out: list[DiscoveredPage],
    seen: set[str],
) -> None:
    for block in client.get_block_children(block_id):
        btype = block.get("type")
        if btype == "child_page":
            page_id = normalize_id(block["id"])
            title = block.get("child_page", {}).get("title", "Untitled")
            if page_id not in seen:
                seen.add(page_id)
                out.append(DiscoveredPage(block["id"], title, folder_chain))
            _walk_child_pages(
                client, block["id"], (*folder_chain, sanitize_filename(title)), out, seen
            )
        elif block.get("has_children"):
            # Look inside containers (toggles, columns, synced blocks, list items, ...)
            # for further nested pages without treating the container itself as a page.
            _walk_child_pages(client, block["id"], folder_chain, out, seen)


def discover_root_tree(client: NotionClient, root_page_id: str) -> list[DiscoveredPage]:
    root = client.get_page(root_page_id)
    root_title = get_title(root.get("properties", {})) or "Untitled"
    out = [DiscoveredPage(root_page_id, root_title, (), page_object=root)]
    seen = {normalize_id(root_page_id)}
    _walk_child_pages(client, root_page_id, (sanitize_filename(root_title),), out, seen)
    return out


def discover_database(
    client: NotionClient, database_id: str, sync_property: str
) -> list[DiscoveredPage]:
    db = client.get_database(database_id)
    db_title = rich_text_plain(db.get("title", [])) or "Database"
    db_folder = (sanitize_filename(db_title),)

    out: list[DiscoveredPage] = []
    seen: set[str] = set()

    for data_source_id in client.resolve_data_source_ids(database_id):
        _discover_data_source_rows(client, data_source_id, db_folder, sync_property, out, seen)

    return out


def _discover_data_source_rows(
    client: NotionClient,
    data_source_id: str,
    folder: tuple[str, ...],
    sync_property: str,
    out: list[DiscoveredPage],
    seen: set[str],
) -> None:
    for row in client.query_data_source(data_source_id):
        if row.get("archived") or row.get("in_trash"):
            continue
        properties = row.get("properties", {})
        if sync_property and not _sync_property_enabled(properties, sync_property):
            continue
        title = get_title(properties) or "Untitled"
        page_id = normalize_id(row["id"])
        if page_id in seen:
            continue
        seen.add(page_id)
        out.append(DiscoveredPage(row["id"], title, folder, page_object=row))
        _walk_child_pages(client, row["id"], (*folder, sanitize_filename(title)), out, seen)


def _sync_property_enabled(properties: dict[str, Any], property_name: str) -> bool:
    prop = properties.get(property_name)
    if prop is None:
        return False
    if prop.get("type") == "checkbox":
        return bool(prop.get("checkbox"))
    return True


def discover_workspace(client: NotionClient, sync_property: str) -> list[DiscoveredPage]:
    """Discover every page and database the integration currently has access
    to, workspace-wide, without requiring a specific root page or database ID.

    Notion's `/v1/search` returns every page/database that has been shared
    with the integration (directly, or via a shared ancestor), regardless of
    nesting depth — this includes every *descendant* page too, not just
    top-level ones. That means the whole hierarchy can be reconstructed
    purely in memory from each page's `parent` pointer, without walking any
    block tree (`get_block_children`) to "find" nested pages: doing so would
    mean fetching a large fraction of the block structure of every page in
    the workspace just to build the discovery list, before even knowing
    which pages changed. On a workspace with hundreds of deeply-nested
    pages that made discovery itself take many minutes; this makes it a
    handful of paginated search calls total.
    """
    data_sources = client.search(filter_={"property": "object", "value": "data_source"})
    ds_titles = {
        normalize_id(ds["id"]): rich_text_plain(ds.get("title", [])) or "Database"
        for ds in data_sources
    }

    all_pages = client.search(filter_={"property": "object", "value": "page"})
    pages_by_id = {normalize_id(p["id"]): p for p in all_pages}
    block_cache: dict[str, dict[str, Any]] = {}

    out: list[DiscoveredPage] = []
    for page in all_pages:
        if page.get("archived") or page.get("in_trash"):
            continue
        parent = page.get("parent", {})
        if parent.get("type") in ("data_source_id", "database_id"):
            properties = page.get("properties", {})
            if sync_property and not _sync_property_enabled(properties, sync_property):
                continue
        title = get_title(page.get("properties", {})) or "Untitled"
        chain = _resolve_workspace_chain(client, page, pages_by_id, ds_titles, block_cache)
        out.append(DiscoveredPage(page["id"], title, chain, page_object=page))

    return out


def _resolve_workspace_chain(
    client: NotionClient,
    page: dict[str, Any],
    pages_by_id: dict[str, dict[str, Any]],
    ds_titles: dict[str, str],
    block_cache: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Walk `page`'s `parent` pointers up to the workspace root, using only
    the already-fetched `pages_by_id`/`ds_titles` caches — the one exception
    is a `block_id` parent (a page nested inside e.g. a toggle/column, not
    directly inside another page), which needs one `GET /v1/blocks/{id}`
    call per distinct block encountered (cached, and rare in practice).
    """
    chain: list[str] = []
    parent = page.get("parent", {})
    guard = 0
    while guard < 50:
        guard += 1
        ptype = parent.get("type")

        if ptype == "page_id":
            ancestor = pages_by_id.get(normalize_id(parent["page_id"]))
            if ancestor is None:
                logger.warning(
                    "'%s' is accessible but one of its ancestor pages isn't; placing it "
                    "using whatever ancestors could be resolved.",
                    get_title(page.get("properties", {})) or "Untitled",
                )
                break
            ancestor_title = get_title(ancestor.get("properties", {})) or "Untitled"
            chain.insert(0, sanitize_filename(ancestor_title))
            parent = ancestor.get("parent", {})
            continue

        if ptype == "data_source_id":
            ds_id = normalize_id(parent["data_source_id"])
            chain.insert(0, sanitize_filename(ds_titles.get(ds_id, "Database")))
            break  # rows live in a flat folder, like Mode B — no deeper nesting

        if ptype == "database_id":  # defensive: older API versions/shapes
            ds_id = normalize_id(parent["database_id"])
            chain.insert(0, sanitize_filename(ds_titles.get(ds_id, "Database")))
            break

        if ptype == "block_id":
            block_id = normalize_id(parent["block_id"])
            block = block_cache.get(block_id)
            if block is None:
                try:
                    block = client.get_block(parent["block_id"])
                except NotionAPIError:
                    break
                block_cache[block_id] = block
            parent = block.get("parent", {})
            continue

        break  # "workspace", or anything unrecognized: stop here

    return tuple(chain)


def discover_single_page(client: NotionClient, page_id: str) -> DiscoveredPage:
    page = client.get_page(page_id)
    title = get_title(page.get("properties", {})) or "Untitled"
    chain: list[str] = []
    parent = page.get("parent", {})
    guard = 0
    while guard < 50:
        guard += 1
        ptype = parent.get("type")
        if ptype == "page_id":
            ancestor = client.get_page(parent["page_id"])
            ancestor_title = get_title(ancestor.get("properties", {})) or "Untitled"
            chain.insert(0, sanitize_filename(ancestor_title))
            parent = ancestor.get("parent", {})
        elif ptype == "block_id":
            block = client.get_block(parent["block_id"])
            parent = block.get("parent", {})
        elif ptype == "data_source_id":
            ds = client.get_data_source(parent["data_source_id"])
            ds_title = rich_text_plain(ds.get("title", [])) or "Database"
            chain.insert(0, sanitize_filename(ds_title))
            break  # rows live in a flat folder, like Mode B — no deeper nesting
        elif ptype == "database_id":  # defensive: older API versions/shapes
            db = client.get_database(parent["database_id"])
            db_title = rich_text_plain(db.get("title", [])) or "Database"
            chain.insert(0, sanitize_filename(db_title))
            break
        else:
            break
    return DiscoveredPage(page_id, title, tuple(chain), page_object=page)


# -- path assignment ----------------------------------------------------------------


def compute_target_path(
    taken: dict[str, str], notion_id: str, folder_chain: tuple[str, ...], title: str
) -> str:
    normalized = normalize_id(notion_id)
    filename = sanitize_filename(title)
    parts = [*folder_chain, f"{filename}.md"]
    candidate = "/".join(parts)

    owner = taken.get(candidate)
    if owner is None or owner == normalized:
        taken[candidate] = normalized
        return candidate

    short_id = normalized[:8]
    disambiguated = "/".join([*folder_chain, f"{filename} ({short_id}).md"])
    taken[disambiguated] = normalized
    return disambiguated


# -- rendering ------------------------------------------------------------------------


def render_page_content(
    client: NotionClient,
    page: dict[str, Any],
    title: str,
    page_index: PageIndex,
    asset_manager: AssetManager | None,
) -> str:
    blocks = fetch_block_tree(client, page["id"])

    def download_asset(block: dict[str, Any], url: str) -> str | None:
        if asset_manager is None:
            return None
        return asset_manager.download(page["id"], block["id"], url)

    ctx = RenderContext(
        resolve_link=page_index.resolve,
        download_asset=download_asset if asset_manager else None,
        page_id=page["id"],
    )
    return blocks_to_markdown(blocks, ctx)


def build_frontmatter(page: dict[str, Any], page_index: PageIndex) -> dict[str, Any]:
    properties = page.get("properties", {})
    fm: dict[str, Any] = {
        "notion_id": page["id"],
        "notion_url": page.get("url"),
        "notion_last_edited_time": page.get("last_edited_time"),
        "last_sync": now_iso(),
        "managed_by": MANAGED_BY,
    }
    extra = properties_to_frontmatter(properties)
    relations = relation_property_ids(properties)
    for name, ids in relations.items():
        if name not in extra:
            continue
        resolved = [page_index.resolve(i) for i in ids]
        extra[name] = [f"[[{r}]]" if r else i for r, i in zip(resolved, ids, strict=True)]
    fm.update(extra)
    return fm


# -- conflict detection ---------------------------------------------------------------


@dataclass
class ConflictCheck:
    can_write: bool
    is_conflict: bool
    backup_needed: bool


def check_existing_file(path: Path, notion_id: str, expected_checksum: str | None) -> ConflictCheck:
    if not path.exists():
        return ConflictCheck(can_write=True, is_conflict=False, backup_needed=False)

    try:
        existing_text = path.read_text(encoding="utf-8")
    except OSError:
        return ConflictCheck(can_write=True, is_conflict=False, backup_needed=False)

    fm, _ = parse_frontmatter(existing_text)
    fm_notion_id = normalize_id(str(fm.get("notion_id", "")))
    if fm.get("managed_by") != MANAGED_BY or fm_notion_id != normalize_id(notion_id):
        return ConflictCheck(can_write=False, is_conflict=False, backup_needed=False)

    if expected_checksum is not None and sha256_text(existing_text) != expected_checksum:
        return ConflictCheck(can_write=True, is_conflict=True, backup_needed=True)

    return ConflictCheck(can_write=True, is_conflict=False, backup_needed=False)


_RESERVED_DIRS = {"_assets", "_Archive", "_Conflicts"}


def rehydrate_state(config: Config, state: StateStore) -> int:
    """Rebuild minimal state entries by scanning the sync folder for notes this
    tool previously wrote (identified by the `managed_by` frontmatter marker).

    Used to recover from a missing/reset state database without re-creating
    duplicate files for pages that are already on disk. Recovered pages are
    marked so the next sync re-verifies their content against Notion once.
    """
    if not config.sync_root.exists():
        return 0

    recovered = 0
    for path in config.sync_root.rglob("*.md"):
        relative = path.relative_to(config.sync_root)
        if relative.parts and relative.parts[0] in _RESERVED_DIRS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = parse_frontmatter(text)
        if fm.get("managed_by") != MANAGED_BY:
            continue
        notion_id = fm.get("notion_id")
        if not notion_id:
            continue
        notion_id = normalize_id(str(notion_id))
        if state.get(notion_id) is not None:
            continue
        state.upsert(
            PageRecord(
                notion_id=notion_id,
                title=fm.get("title") or path.stem,
                file_path=relative.as_posix(),
                parent_id=None,
                notion_url=fm.get("notion_url"),
                notion_last_edited_time="",  # force re-verification on next sync
                last_synced_at=fm.get("last_sync") or now_iso(),
                content_checksum=sha256_text(text),
            )
        )
        recovered += 1
    return recovered


# -- main orchestration ---------------------------------------------------------------


def run_sync(
    config: Config,
    client: NotionClient,
    state: StateStore,
    *,
    dry_run: bool = False,
    single_page_id: str | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> SyncResult:
    result = SyncResult(dry_run=dry_run)
    full_discovery = single_page_id is None

    logger.info(
        "Starting %s (%s) -> %s",
        "dry run" if dry_run else "sync",
        _describe_selection(config, single_page_id),
        config.sync_root,
    )

    if state.count() == 0 and not dry_run:
        recovered = rehydrate_state(config, state)
        if recovered:
            logger.info(
                "Recovered %d existing note(s) from disk into a fresh state database.", recovered
            )

    discovered = _discover_all(client, config, single_page_id)
    result.pages_seen = [normalize_id(p.notion_id) for p in discovered]

    logger.info("Found %d page(s) matching your configuration.", len(discovered))
    if not discovered:
        logger.warning(
            "No pages found. Run `notion-obsidian-sync doctor` to check how much content "
            "the integration can actually see (Content Access tab in Notion) — a 0-page "
            "result almost always means nothing has been shared with it yet."
        )

    taken_paths: dict[str, str] = {
        r.file_path: normalize_id(r.notion_id) for r in state.all_records()
    }
    page_index = PageIndex()

    plans: list[tuple[DiscoveredPage, str, str]] = []  # (page, action, target_path)
    total_discovered = len(discovered)
    for idx, dp in enumerate(discovered, start=1):
        if on_progress:
            on_progress("resolve", idx, total_discovered)
        existing = state.get(normalize_id(dp.notion_id))

        # A `child_page` block's own last_edited_time is not a reliable proxy for
        # deep content changes inside that page, so always fetch the authoritative
        # page object here. This is a lightweight properties-only call — the
        # expensive part (block tree + assets) is still skipped for SKIP pages.
        if dp.page_object is None:
            try:
                dp.page_object = client.get_page(dp.notion_id)
            except NotionAPIError as exc:
                result.errors += 1
                logger.error("Failed to fetch page %s (%s): %s", dp.title, dp.notion_id, exc)
                continue
        remote_last_edited = dp.page_object.get("last_edited_time")

        if existing is None:
            action = "CREATE"
        elif existing.notion_last_edited_time != remote_last_edited:
            action = "UPDATE"
        else:
            action = "SKIP"

        if action == "SKIP" and force and existing is not None:
            action = "UPDATE"

        if action == "SKIP" and existing is not None:
            target_path = existing.file_path
            taken_paths[target_path] = normalize_id(dp.notion_id)
        else:
            target_path = compute_target_path(taken_paths, dp.notion_id, dp.folder_chain, dp.title)

        without_ext = target_path[:-3] if target_path.endswith(".md") else target_path
        page_index.register(dp.notion_id, f"{config.obsidian_sync_folder}/{without_ext}")
        plans.append((dp, action, target_path))

    total_plans = len(plans)
    for idx, (dp, action, target_path) in enumerate(plans, start=1):
        if on_progress:
            on_progress("sync", idx, total_plans)
        try:
            if action == "SKIP":
                result.skipped += 1
                log_action(logger, "SKIP", dp.title)
                continue
            _process_page(
                client, config, state, page_index, dp, action, target_path, dry_run, result
            )
        except NotionAPIError as exc:
            result.errors += 1
            logger.error("Failed to sync page %s (%s): %s", dp.title, dp.notion_id, exc)
        except PathSecurityError as exc:
            result.errors += 1
            logger.error("Refusing unsafe path for page %s: %s", dp.title, exc)
        except Exception as exc:  # noqa: BLE001 - one bad page must not abort the run
            result.errors += 1
            logger.error("Unexpected error syncing page %s (%s): %s", dp.title, dp.notion_id, exc)

    if full_discovery:
        seen_ids = {normalize_id(i) for i in result.pages_seen}
        _handle_orphans(config, state, seen_ids, dry_run, result)

    return result


def _describe_selection(config: Config, single_page_id: str | None) -> str:
    if single_page_id:
        return f"page {single_page_id}"
    parts = []
    if config.notion_root_page_id:
        parts.append(f"root page {config.notion_root_page_id}")
    if config.notion_database_ids:
        parts.append(f"{len(config.notion_database_ids)} database(s)")
    if config.notion_sync_workspace:
        parts.append("whole workspace")
    return " + ".join(parts) if parts else "nothing configured"


def _discover_all(
    client: NotionClient, config: Config, single_page_id: str | None
) -> list[DiscoveredPage]:
    if single_page_id:
        return [discover_single_page(client, single_page_id)]

    out: list[DiscoveredPage] = []
    seen: set[str] = set()

    if config.notion_root_page_id:
        before = len(out)
        for dp in discover_root_tree(client, config.notion_root_page_id):
            if normalize_id(dp.notion_id) not in seen:
                seen.add(normalize_id(dp.notion_id))
                out.append(dp)
        logger.info(
            "Root page %s: found %d page(s).", config.notion_root_page_id, len(out) - before
        )

    for database_id in config.notion_database_ids:
        before = len(out)
        for dp in discover_database(client, database_id, config.notion_sync_property):
            if normalize_id(dp.notion_id) not in seen:
                seen.add(normalize_id(dp.notion_id))
                out.append(dp)
        logger.info("Database %s: found %d page(s).", database_id, len(out) - before)

    if config.notion_sync_workspace:
        before = len(out)
        for dp in discover_workspace(client, config.notion_sync_property):
            if normalize_id(dp.notion_id) not in seen:
                seen.add(normalize_id(dp.notion_id))
                out.append(dp)
        logger.info("Workspace-wide search: found %d page(s).", len(out) - before)

    return out


def _process_page(
    client: NotionClient,
    config: Config,
    state: StateStore,
    page_index: PageIndex,
    dp: DiscoveredPage,
    action: str,
    target_path: str,
    dry_run: bool,
    result: SyncResult,
) -> None:
    page = dp.page_object or client.get_page(dp.notion_id)
    title = get_title(page.get("properties", {})) or dp.title

    existing = state.get(normalize_id(dp.notion_id))
    abs_target = resolve_within(config.sync_root, target_path)

    if dry_run:
        log_action(logger, action, title)
        if action == "CREATE":
            result.created += 1
        else:
            result.updated += 1
        return

    check = check_existing_file(
        abs_target, dp.notion_id, existing.content_checksum if existing else None
    )
    if not check.can_write:
        result.conflicts += 1
        log_action(
            logger,
            "WARN",
            f"Refusing to overwrite unmanaged file at {target_path} for page '{title}'",
        )
        return

    if check.is_conflict:
        backup_path = resolve_within(config.conflicts_dir.parent, Path("_Conflicts") / target_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(abs_target.read_text(encoding="utf-8"), encoding="utf-8")
        result.conflicts += 1
        log_action(
            logger,
            "WARN",
            f"Local modification detected in '{title}' — backed up to _Conflicts/ before overwrite",
        )

    asset_manager = AssetManager(config.assets_dir) if config.download_assets else None
    body = render_page_content(client, page, title, page_index, asset_manager)
    frontmatter = build_frontmatter(page, page_index)
    content = compose_note(frontmatter, title, body)

    atomic_write_text(abs_target, content)

    # Rename: remove the old file if this page previously lived elsewhere.
    if existing and existing.file_path != target_path:
        old_abs = resolve_within(config.sync_root, existing.file_path)
        if old_abs.exists():
            old_abs.unlink()

    state.upsert(
        PageRecord(
            notion_id=normalize_id(dp.notion_id),
            title=title,
            file_path=target_path,
            parent_id=_parent_id(page),
            notion_url=page.get("url"),
            notion_last_edited_time=page.get("last_edited_time", ""),
            last_synced_at=now_iso(),
            content_checksum=sha256_text(content),
        )
    )

    log_action(logger, action, title)
    if action == "CREATE":
        result.created += 1
    else:
        result.updated += 1


def _parent_id(page: dict[str, Any]) -> str | None:
    parent = page.get("parent", {})
    if parent.get("type") == "page_id":
        return normalize_id(parent["page_id"])
    if parent.get("type") == "database_id":
        return normalize_id(parent["database_id"])
    return None


def _handle_orphans(
    config: Config, state: StateStore, seen_ids: set[str], dry_run: bool, result: SyncResult
) -> None:
    orphan_ids = state.all_ids() - seen_ids
    for notion_id in orphan_ids:
        record = state.get(notion_id)
        if record is None:
            continue

        if config.orphan_policy == "keep":
            result.orphan_kept += 1
            log_action(
                logger,
                "WARN",
                f"'{record.title}' is no longer accessible in Notion "
                f"(ORPHAN_POLICY=keep, file untouched: {record.file_path})",
            )
            continue

        if dry_run:
            action = "ARCHIVE" if config.orphan_policy == "archive" else "DELETE"
            log_action(logger, action, f"{record.title} (would {config.orphan_policy})")
            if config.orphan_policy == "archive":
                result.archived += 1
            else:
                result.deleted += 1
            continue

        try:
            src = resolve_within(config.sync_root, record.file_path)
        except PathSecurityError:
            logger.error("Refusing unsafe orphan path: %s", record.file_path)
            continue

        if config.orphan_policy == "archive":
            dst = resolve_within(config.sync_root, Path("_Archive") / record.file_path)
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
            state.delete(notion_id)
            result.archived += 1
            log_action(logger, "ARCHIVE", record.title)
        elif config.orphan_policy == "delete":
            if src.exists():
                src.unlink()
            state.delete(notion_id)
            result.deleted += 1
            log_action(logger, "WARN", f"Deleted orphaned file: {record.title}")
