"""Tests for git_crypt.py — subprocess calls are mocked so these never need
the real `git`/`git-crypt` binaries installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from notion_obsidian_sync.git_crypt import (
    GitCryptError,
    check_tools,
    is_git_crypt_initialized,
    is_git_repo,
    setup,
)


def _ok(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")


def _fail(message: str):
    def _run(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=message)

    return _run


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault-notion"
    d.mkdir()
    return d


def test_check_tools_reflects_which(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git" if name == "git" else None)
    assert check_tools() == (True, False)


def test_setup_raises_when_git_missing(repo_dir):
    with patch("shutil.which", side_effect=lambda name: None):
        with pytest.raises(GitCryptError, match="git.*not found"):
            setup(repo_dir)


def test_setup_raises_when_git_crypt_missing(repo_dir):
    def which(name: str) -> str | None:
        return "/usr/bin/git" if name == "git" else None

    with patch("shutil.which", side_effect=which):
        with pytest.raises(GitCryptError, match="git-crypt.*not found"):
            setup(repo_dir)


def test_setup_raises_for_missing_path(tmp_path):
    with patch("shutil.which", return_value="/usr/bin/x"):
        with pytest.raises(GitCryptError, match="does not exist"):
            setup(tmp_path / "does-not-exist")


def test_setup_full_flow_calls_git_init_and_git_crypt_init(repo_dir):
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(args)
        return _ok(args)

    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run", side_effect=lambda args, cwd: run(args)
    ):
        result = setup(repo_dir)

    assert result.created_git_repo is True
    assert result.ran_git_crypt_init is True
    assert result.wrote_gitattributes is True
    assert calls == [["git", "init"], ["git-crypt", "init"]]

    content = (repo_dir / ".gitattributes").read_text(encoding="utf-8")
    assert "filter=git-crypt" in content


def test_setup_is_idempotent_on_existing_repo(repo_dir):
    (repo_dir / ".git").mkdir()
    (repo_dir / ".git" / "git-crypt").mkdir()
    (repo_dir / ".gitattributes").write_text(
        "* filter=git-crypt diff=git-crypt\n", encoding="utf-8"
    )

    calls: list[list[str]] = []

    def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(args)
        return _ok(args)

    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run", side_effect=lambda args, cwd: run(args)
    ):
        result = setup(repo_dir)

    assert result.created_git_repo is False
    assert result.wrote_gitattributes is False
    assert result.already_initialized is True
    assert result.ran_git_crypt_init is False
    assert calls == []  # nothing needed to run


def test_setup_appends_to_existing_gitattributes_without_duplicating(repo_dir):
    (repo_dir / ".git").mkdir()
    (repo_dir / ".gitattributes").write_text("*.png binary\n", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run", side_effect=lambda args, cwd: _ok(args)
    ):
        result = setup(repo_dir)

    assert result.wrote_gitattributes is True
    content = (repo_dir / ".gitattributes").read_text(encoding="utf-8")
    assert content == "*.png binary\n* filter=git-crypt diff=git-crypt\n"

    # Re-running must not duplicate the line.
    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run", side_effect=lambda args, cwd: _ok(args)
    ):
        setup(repo_dir)
    content2 = (repo_dir / ".gitattributes").read_text(encoding="utf-8")
    assert content2.count("filter=git-crypt") == 1


def test_setup_adds_gpg_users_and_exports_key(repo_dir, tmp_path):
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(args)
        return _ok(args)

    key_path = tmp_path / "keys" / "vault.key"

    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run", side_effect=lambda args, cwd: run(args)
    ):
        result = setup(
            repo_dir, gpg_users=["alice@example.com", "bob@example.com"], export_key_path=key_path
        )

    assert result.added_gpg_users == ["alice@example.com", "bob@example.com"]
    assert result.exported_key_path == key_path
    assert ["git-crypt", "add-gpg-user", "alice@example.com"] in calls
    assert ["git-crypt", "add-gpg-user", "bob@example.com"] in calls
    assert ["git-crypt", "export-key", str(key_path)] in calls


def test_setup_raises_on_git_init_failure(repo_dir):
    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run",
        side_effect=lambda args, cwd: _fail("permission denied")(args),
    ):
        with pytest.raises(GitCryptError, match="git init.*failed"):
            setup(repo_dir)


def test_setup_raises_on_add_gpg_user_failure(repo_dir):
    (repo_dir / ".git").mkdir()
    (repo_dir / ".git" / "git-crypt").mkdir()

    with patch("shutil.which", return_value="/usr/bin/x"), patch(
        "notion_obsidian_sync.git_crypt._run",
        side_effect=lambda args, cwd: _fail("gpg: no such key")(args),
    ):
        with pytest.raises(GitCryptError, match="add-gpg-user"):
            setup(repo_dir, gpg_users=["missing@example.com"])


def test_is_git_repo_and_is_git_crypt_initialized(repo_dir):
    assert is_git_repo(repo_dir) is False
    assert is_git_crypt_initialized(repo_dir) is False

    (repo_dir / ".git").mkdir()
    assert is_git_repo(repo_dir) is True
    assert is_git_crypt_initialized(repo_dir) is False

    (repo_dir / ".git" / "git-crypt").mkdir()
    assert is_git_crypt_initialized(repo_dir) is True
