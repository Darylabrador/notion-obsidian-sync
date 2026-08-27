"""Configuration loading and validation from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_NOTION_API_VERSION = "2025-09-03"
VALID_ORPHAN_POLICIES = {"keep", "archive", "delete"}


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _split_ids(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _bool(raw: str, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    notion_token: str
    obsidian_vault_path: Path
    obsidian_sync_folder: str = "Notion"

    notion_root_page_id: str | None = None
    notion_database_ids: list[str] = field(default_factory=list)
    notion_sync_property: str = ""
    notion_sync_workspace: bool = False

    orphan_policy: str = "keep"

    download_assets: bool = True
    convert_notion_links: bool = True

    sync_interval_minutes: int = 10
    log_level: str = "INFO"

    notion_api_version: str = DEFAULT_NOTION_API_VERSION

    project_dir: Path = field(default_factory=Path.cwd)

    @property
    def sync_root(self) -> Path:
        """Absolute path to the sandboxed folder this tool is allowed to write to."""
        return (self.obsidian_vault_path / self.obsidian_sync_folder).resolve()

    @property
    def assets_dir(self) -> Path:
        return self.sync_root / "_assets"

    @property
    def archive_dir(self) -> Path:
        return self.sync_root / "_Archive"

    @property
    def conflicts_dir(self) -> Path:
        return self.sync_root / "_Conflicts"

    @property
    def state_db_path(self) -> Path:
        return self.project_dir / ".sync-state.sqlite"

    @property
    def log_dir(self) -> Path:
        return self.project_dir / "logs"

    def has_selection(self) -> bool:
        return bool(
            self.notion_root_page_id or self.notion_database_ids or self.notion_sync_workspace
        )

    def validate(self) -> list[str]:
        """Return a list of human-readable problems, empty if config is usable."""
        problems = []
        if not self.notion_token:
            problems.append("NOTION_TOKEN is not set.")
        if not str(self.obsidian_vault_path):
            problems.append("OBSIDIAN_VAULT_PATH is not set.")
        elif not self.obsidian_vault_path.exists():
            problems.append(f"OBSIDIAN_VAULT_PATH does not exist: {self.obsidian_vault_path}")
        elif not self.obsidian_vault_path.is_dir():
            problems.append(f"OBSIDIAN_VAULT_PATH is not a directory: {self.obsidian_vault_path}")
        if not self.obsidian_sync_folder:
            problems.append("OBSIDIAN_SYNC_FOLDER must not be empty.")
        if self.orphan_policy not in VALID_ORPHAN_POLICIES:
            problems.append(
                f"ORPHAN_POLICY must be one of {sorted(VALID_ORPHAN_POLICIES)}, "
                f"got {self.orphan_policy!r}."
            )
        if not self.has_selection():
            problems.append(
                "Set at least one of NOTION_ROOT_PAGE_ID, NOTION_DATABASE_IDS, "
                "or NOTION_SYNC_WORKSPACE=true."
            )
        return problems


def load_config(env_path: Path | None = None, project_dir: Path | None = None) -> Config:
    """Load configuration from a `.env` file (if present) and the environment.

    Environment variables already set take precedence over `.env` contents.
    """
    if env_path is not None:
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)

    vault_raw = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    vault_path = Path(os.path.expanduser(os.path.expandvars(vault_raw))) if vault_raw else Path()

    return Config(
        notion_token=os.environ.get("NOTION_TOKEN", "").strip(),
        obsidian_vault_path=vault_path,
        obsidian_sync_folder=os.environ.get("OBSIDIAN_SYNC_FOLDER", "Notion").strip() or "Notion",
        notion_root_page_id=os.environ.get("NOTION_ROOT_PAGE_ID", "").strip() or None,
        notion_database_ids=_split_ids(os.environ.get("NOTION_DATABASE_IDS", "")),
        notion_sync_property=os.environ.get("NOTION_SYNC_PROPERTY", "").strip(),
        notion_sync_workspace=_bool(os.environ.get("NOTION_SYNC_WORKSPACE", ""), False),
        orphan_policy=os.environ.get("ORPHAN_POLICY", "keep").strip().lower() or "keep",
        download_assets=_bool(os.environ.get("DOWNLOAD_ASSETS", ""), True),
        convert_notion_links=_bool(os.environ.get("CONVERT_NOTION_LINKS", ""), True),
        sync_interval_minutes=int(os.environ.get("SYNC_INTERVAL_MINUTES", "10") or 10),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        notion_api_version=os.environ.get("NOTION_API_VERSION", "").strip()
        or DEFAULT_NOTION_API_VERSION,
        project_dir=project_dir or Path.cwd(),
    )
