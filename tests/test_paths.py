from pathlib import Path

import pytest

from notion_obsidian_sync.paths import (
    PathSecurityError,
    atomic_write_text,
    resolve_within,
    sanitize_filename,
)


def test_sanitize_strips_invalid_windows_chars():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"


def test_sanitize_strips_trailing_dots_and_spaces():
    assert sanitize_filename("Project Alpha.   ") == "Project Alpha"


def test_sanitize_reserved_windows_names():
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("com1") == "_com1"
    assert sanitize_filename("LPT9") == "_LPT9"
    assert sanitize_filename("Console") == "Console"  # not an exact match, must survive


def test_sanitize_empty_title_uses_fallback():
    assert sanitize_filename("") == "Untitled"
    assert sanitize_filename("   ") == "Untitled"
    assert sanitize_filename("...") == "Untitled"


def test_sanitize_preserves_unicode():
    assert sanitize_filename("Étude énergétique — café") == "Étude énergétique — café"


def test_sanitize_long_title_truncated_safely():
    long_title = "a" * 500
    result = sanitize_filename(long_title)
    assert len(result.encode("utf-8")) <= 200
    assert result  # not empty


def test_resolve_within_blocks_path_traversal(tmp_path: Path):
    base = tmp_path / "vault" / "Notion"
    base.mkdir(parents=True)
    with pytest.raises(PathSecurityError):
        resolve_within(base, "../../etc/passwd")


def test_resolve_within_allows_nested_path(tmp_path: Path):
    base = tmp_path / "vault" / "Notion"
    base.mkdir(parents=True)
    result = resolve_within(base, "Projects/Alpha.md")
    assert result == (base / "Projects" / "Alpha.md").resolve()


def test_atomic_write_creates_file_and_no_leftover_tmp(tmp_path: Path):
    target = tmp_path / "note.md"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_overwrites_existing_file(tmp_path: Path):
    target = tmp_path / "note.md"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text(encoding="utf-8") == "v2"
