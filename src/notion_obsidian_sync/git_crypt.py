"""Optional helper to set up `git-crypt` on the synced notes folder, so the
content can be version-controlled and pushed to a remote while staying
encrypted at rest — useful since synced Notion content can be sensitive.

This module only ever shells out to `git` and `git-crypt` themselves (never
`shell=True`, always an explicit argument list) and only touches the target
directory passed in — it does not know about `OBSIDIAN_VAULT_PATH` sandboxing,
that's enforced by the caller (`cli.py`) choosing the default `--path`.

Nothing here creates a commit on your behalf, except `git-crypt add-gpg-user`,
which is git-crypt's own inherent behavior (it commits the newly-wrapped key
under `.git-crypt/keys/`) — not something this module adds on top.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .paths import atomic_write_text

GITATTRIBUTES_LINE = "* filter=git-crypt diff=git-crypt\n"


class GitCryptError(Exception):
    """Raised when a setup step fails or a prerequisite is missing."""


@dataclass
class GitCryptSetupResult:
    repo_path: Path
    created_git_repo: bool = False
    wrote_gitattributes: bool = False
    already_initialized: bool = False
    ran_git_crypt_init: bool = False
    added_gpg_users: list[str] = field(default_factory=list)
    exported_key_path: Path | None = None


def check_tools() -> tuple[bool, bool]:
    """Return (git_available, git_crypt_available)."""
    return shutil.which("git") is not None, shutil.which("git-crypt") is not None


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def is_git_crypt_initialized(path: Path) -> bool:
    return (path / ".git" / "git-crypt").exists()


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)


def setup(
    path: Path,
    *,
    gpg_users: list[str] | None = None,
    export_key_path: Path | None = None,
) -> GitCryptSetupResult:
    """Initialize git (if needed), enable git-crypt on every file via
    `.gitattributes`, and optionally grant GPG collaborators and/or export a
    symmetric key. Idempotent: safe to re-run on an already-set-up directory.
    """
    git_ok, git_crypt_ok = check_tools()
    if not git_ok:
        raise GitCryptError(
            "`git` was not found on PATH. Install it first "
            "(e.g. `sudo apt install git`, `brew install git`, or "
            "https://git-scm.com/downloads)."
        )
    if not git_crypt_ok:
        raise GitCryptError(
            "`git-crypt` was not found on PATH. Install it first "
            "(e.g. `sudo apt install git-crypt`, `brew install git-crypt`, "
            "or see https://github.com/AGWA/git-crypt#installing)."
        )
    if not path.exists() or not path.is_dir():
        raise GitCryptError(f"Path does not exist or is not a directory: {path}")

    result = GitCryptSetupResult(repo_path=path)

    if not is_git_repo(path):
        proc = _run(["git", "init"], cwd=path)
        if proc.returncode != 0:
            raise GitCryptError(f"`git init` failed: {proc.stderr.strip()}")
        result.created_git_repo = True

    gitattributes = path / ".gitattributes"
    existing = gitattributes.read_text(encoding="utf-8") if gitattributes.exists() else ""
    if "filter=git-crypt" not in existing:
        new_content = existing
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        new_content += GITATTRIBUTES_LINE
        atomic_write_text(gitattributes, new_content)
        result.wrote_gitattributes = True

    if is_git_crypt_initialized(path):
        result.already_initialized = True
    else:
        proc = _run(["git-crypt", "init"], cwd=path)
        if proc.returncode != 0:
            raise GitCryptError(f"`git-crypt init` failed: {proc.stderr.strip()}")
        result.ran_git_crypt_init = True

    for user in gpg_users or []:
        proc = _run(["git-crypt", "add-gpg-user", user], cwd=path)
        if proc.returncode != 0:
            raise GitCryptError(
                f"`git-crypt add-gpg-user {user}` failed: {proc.stderr.strip()}"
            )
        result.added_gpg_users.append(user)

    if export_key_path is not None:
        export_key_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(["git-crypt", "export-key", str(export_key_path)], cwd=path)
        if proc.returncode != 0:
            raise GitCryptError(f"`git-crypt export-key` failed: {proc.stderr.strip()}")
        result.exported_key_path = export_key_path

    return result
