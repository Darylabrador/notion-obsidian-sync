"""Command-line interface."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import click

from .config import Config, load_config
from .logging_config import get_logger, setup_logging
from .notion_client import NotionAPIError, NotionClient
from .state import StateStore
from .sync import ProgressCallback, SyncResult, run_sync

SUCCESS, FAILURE = 0, 1

_PHASE_LABELS = {"resolve": "Resolving pages", "sync": "Syncing"}


class ProgressReporter:
    """Renders a single self-overwriting progress line to stderr while a sync
    runs — the discovery/decision phase (`resolve`) can take a while on a
    large workspace since it's rate-limited by the Notion API, with nothing
    else printed in the meantime otherwise. Silently disabled (no writes) if
    stderr isn't a terminal, since a `\\r`-based line is meaningless once
    redirected to a file/log.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled and sys.stderr.isatty()
        self._phase_start: dict[str, float] = {}
        self._last_len = 0

    def callback(self) -> ProgressCallback:
        def _update(phase: str, index: int, total: int) -> None:
            if not self.enabled or total == 0:
                return
            now = time.monotonic()
            start = self._phase_start.setdefault(phase, now)
            elapsed = now - start
            eta = (elapsed / index) * (total - index) if index else 0.0
            pct = index / total
            bar_width = 20
            filled = int(bar_width * pct)
            bar = "#" * filled + "-" * (bar_width - filled)
            label = _PHASE_LABELS.get(phase, phase)
            line = f"{label}: [{bar}] {index}/{total} ({pct:.0%}) ETA {_format_duration(eta)}"
            width = shutil.get_terminal_size((80, 20)).columns - 1
            line = line[:width]
            pad = max(0, self._last_len - len(line))
            sys.stderr.write("\r" + line + (" " * pad))
            sys.stderr.flush()
            self._last_len = len(line)

        return _update

    def clear_line(self) -> None:
        """Blank out the current progress line so a log message can print
        cleanly above it; the next `callback()` update redraws the bar.
        """
        if self.enabled and self._last_len:
            sys.stderr.write("\r" + " " * self._last_len + "\r")
            sys.stderr.flush()
        self._last_len = 0

    def finish(self) -> None:
        self.clear_line()


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def _load_and_validate(verbose: bool, progress: ProgressReporter | None = None) -> Config:
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_dir=config.log_dir,
        verbose=verbose,
        pre_emit_hook=progress.clear_line if progress and progress.enabled else None,
    )
    problems = config.validate()
    if problems:
        click.echo("Configuration problems found:", err=True)
        for p in problems:
            click.echo(f"  - {p}", err=True)
        raise SystemExit(FAILURE)
    return config


def _make_client(config: Config) -> NotionClient:
    return NotionClient(token=config.notion_token, api_version=config.notion_api_version)


def _print_summary(result: SyncResult) -> None:
    logger = get_logger()
    label = "Dry run" if result.dry_run else "Sync"
    parts = [
        f"{result.created} created",
        f"{result.updated} updated",
        f"{result.skipped} skipped",
    ]
    if result.archived:
        parts.append(f"{result.archived} archived")
    if result.deleted:
        parts.append(f"{result.deleted} deleted")
    if result.orphan_kept:
        parts.append(f"{result.orphan_kept} orphaned (kept)")
    if result.conflicts:
        parts.append(f"{result.conflicts} conflicts")
    if result.errors:
        parts.append(f"{result.errors} errors")
    logger.info("%s completed: %s", label, ", ".join(parts))


@click.group()
@click.version_option(package_name="notion-obsidian-sync")
def main() -> None:
    """Synchronize Notion content into an Obsidian vault (Notion -> Obsidian, read-only)."""


@main.command()
@click.option("--full", is_flag=True, help="Re-verify every page's content even if unchanged.")
@click.option("--page", "page_id", default=None, help="Sync only this single Notion page ID.")
@click.option("--verbose", is_flag=True, help="Enable debug-level logging.")
@click.option("--dry-run", is_flag=True, help="Compute actions without writing anything.")
def sync(full: bool, page_id: str | None, verbose: bool, dry_run: bool) -> None:
    """Run a synchronization pass."""
    progress = ProgressReporter(enabled=not verbose)
    config = _load_and_validate(verbose, progress)
    client = _make_client(config)
    with StateStore(config.state_db_path) as state:
        try:
            result = run_sync(
                config,
                client,
                state,
                dry_run=dry_run,
                single_page_id=page_id,
                force=full,
                on_progress=progress.callback(),
            )
        except NotionAPIError as exc:
            click.echo(f"Notion API error: {exc}", err=True)
            raise SystemExit(FAILURE) from exc
        finally:
            progress.finish()
    _print_summary(result)
    raise SystemExit(SUCCESS if result.ok else FAILURE)


@main.command(name="dry-run")
@click.option("--verbose", is_flag=True, help="Enable debug-level logging.")
def dry_run_cmd(verbose: bool) -> None:
    """Show what would change without writing or downloading anything."""
    progress = ProgressReporter(enabled=not verbose)
    config = _load_and_validate(verbose, progress)
    client = _make_client(config)
    with StateStore(config.state_db_path) as state:
        try:
            result = run_sync(
                config, client, state, dry_run=True, on_progress=progress.callback()
            )
        except NotionAPIError as exc:
            click.echo(f"Notion API error: {exc}", err=True)
            raise SystemExit(FAILURE) from exc
        finally:
            progress.finish()
    _print_summary(result)
    raise SystemExit(SUCCESS if result.ok else FAILURE)


@main.command()
def status() -> None:
    """Show the current local sync state."""
    config = load_config()
    if not config.state_db_path.exists():
        click.echo("No sync state found yet. Run `notion-obsidian-sync sync` first.")
        return
    with StateStore(config.state_db_path) as state:
        records = state.all_records()
    click.echo(f"Tracked pages: {len(records)}")
    click.echo(f"State database: {config.state_db_path}")
    click.echo(f"Sync folder: {config.sync_root}")
    if records:
        click.echo("\nMost recently synced:")
        for r in sorted(records, key=lambda r: r.last_synced_at, reverse=True)[:10]:
            click.echo(f"  {r.last_synced_at}  {r.file_path}")


@main.command()
def doctor() -> None:
    """Diagnose configuration, connectivity, and filesystem permissions."""
    ok = True

    click.echo(f"Python version: {sys.version.split()[0]}", nl=False)
    if sys.version_info >= (3, 11):  # noqa: UP036 - runtime check, not a static min-version guard
        click.echo("  [OK]")
    else:
        click.echo("  [FAIL] Python 3.11+ is required")
        ok = False

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        click.echo(f".env loading: [FAIL] {exc}")
        raise SystemExit(FAILURE) from exc
    click.echo(".env loaded: [OK]")

    problems = config.validate()
    if problems:
        for p in problems:
            click.echo(f"Config: [FAIL] {p}")
        ok = False
    else:
        click.echo("Config: [OK]")

    if config.notion_token:
        try:
            client = _make_client(config)
            me = client.whoami()
            name = me.get("name") or me.get("bot", {}).get("owner", {}).get("type", "integration")
            click.echo(f"Notion token: [OK] authenticated as '{name}'")
        except NotionAPIError as exc:
            click.echo(f"Notion token: [FAIL] {exc}")
            ok = False
        except Exception as exc:  # noqa: BLE001
            click.echo(f"Notion token: [FAIL] could not reach Notion API: {exc}")
            ok = False
        else:
            try:
                pages = client.search(filter_={"property": "object", "value": "page"})
                data_sources = client.search(
                    filter_={"property": "object", "value": "data_source"}
                )
                click.echo(
                    f"Accessible content: {len(pages)} page(s), "
                    f"{len(data_sources)} data source(s)"
                )
                if not pages and not data_sources:
                    click.echo(
                        "  [WARN] Nothing is shared with this integration yet — sync will "
                        "find nothing to do."
                    )
                    click.echo(
                        "         In Notion, open a page or database → \"...\" menu → "
                        "Connections → add your integration."
                    )
                    click.echo(
                        "         (Or, as a workspace owner: integration settings → "
                        "Access tab → connect it to the whole workspace.)"
                    )
            except NotionAPIError as exc:
                click.echo(f"Accessible content: [FAIL] {exc}")
                ok = False
    else:
        click.echo("Notion token: [SKIPPED] (not configured)")
        ok = False

    vault = config.obsidian_vault_path
    if str(vault) and vault.exists() and vault.is_dir():
        click.echo(f"Obsidian vault: [OK] {vault}")
        try:
            config.sync_root.mkdir(parents=True, exist_ok=True)
            probe = config.sync_root / ".notion-obsidian-sync-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            click.echo(f"Sync folder writable: [OK] {config.sync_root}")
        except OSError as exc:
            click.echo(f"Sync folder writable: [FAIL] {exc}")
            ok = False
    else:
        click.echo(f"Obsidian vault: [FAIL] not found at {vault}")
        ok = False

    if config.state_db_path.exists():
        click.echo(f"State database: [OK] {config.state_db_path}")
    else:
        click.echo(f"State database: [INFO] not created yet ({config.state_db_path})")

    raise SystemExit(SUCCESS if ok else FAILURE)


@main.command(name="reset-state")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def reset_state(yes: bool) -> None:
    """Forget local sync state. Does NOT delete any Markdown files.

    The next sync will re-verify every page's content against Notion; files
    already on disk with a matching `notion_id` in their frontmatter are
    recognized automatically and will not be duplicated.
    """
    config = load_config()
    if not config.state_db_path.exists():
        click.echo("No state database to reset.")
        return
    if not yes and not click.confirm(
        f"This will delete {config.state_db_path} and forget all sync history. Continue?"
    ):
        click.echo("Aborted.")
        return
    config.state_db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(config.state_db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    click.echo("State database removed.")


if __name__ == "__main__":
    main()
