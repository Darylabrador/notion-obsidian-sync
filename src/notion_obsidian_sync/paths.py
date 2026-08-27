"""Filesystem safety: cross-platform filename sanitization, sandboxed path
resolution, and atomic file writes.

Every write performed by this project must be confined to the configured
sync folder inside the Obsidian vault. Functions in this module are the
single choke point that enforces that guarantee.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_DOTS_SPACES = re.compile(r"[ .]+$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_COMPONENT_BYTES = 200  # leaves headroom under the 255-byte filesystem limit


class PathSecurityError(Exception):
    """Raised when a resolved path would escape the sandboxed sync folder."""


def sanitize_filename(title: str, fallback: str = "Untitled") -> str:
    """Turn an arbitrary Notion title into a filename safe on Linux and Windows.

    Preserves Unicode/accents. Strips characters invalid on Windows, trailing
    dots/spaces (invalid on Windows), and renames reserved device names.
    """
    name = (title or "").strip()
    name = _INVALID_CHARS.sub("", name)
    name = _TRAILING_DOTS_SPACES.sub("", name)
    name = name.strip()

    if not name:
        name = fallback

    if name.upper() in _WINDOWS_RESERVED:
        name = f"_{name}"

    # Truncate on a byte boundary so we don't exceed filesystem limits, while
    # keeping the string valid UTF-8.
    encoded = name.encode("utf-8")
    if len(encoded) > _MAX_COMPONENT_BYTES:
        encoded = encoded[:_MAX_COMPONENT_BYTES]
        name = encoded.decode("utf-8", errors="ignore").strip()
        name = _TRAILING_DOTS_SPACES.sub("", name) or fallback

    return name


def sanitize_path_parts(parts: list[str]) -> list[str]:
    """Sanitize each segment of a relative path independently."""
    return [sanitize_filename(p) for p in parts if p]


def resolve_within(base: Path, relative: Path | str) -> Path:
    """Resolve `relative` against `base` and guarantee the result stays inside it.

    Raises PathSecurityError on any attempted path traversal (e.g. `../../etc`).
    """
    base_resolved = base.resolve()
    candidate = (base_resolved / relative).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise PathSecurityError(
            f"Refusing to touch path outside sandbox: {candidate} (base={base_resolved})"
        ) from exc
    return candidate


def ensure_within(base: Path, path: Path) -> None:
    """Assert an already-resolved path is inside `base`, raising otherwise."""
    base_resolved = base.resolve()
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise PathSecurityError(
            f"Refusing to touch path outside sandbox: {path_resolved} (base={base_resolved})"
        ) from exc


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically: write to a sibling temp file, then
    os.replace() so a crash mid-write never leaves a corrupted or partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
