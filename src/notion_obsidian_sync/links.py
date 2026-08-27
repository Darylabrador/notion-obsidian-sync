"""Two-pass Notion-ID <-> Obsidian-note index used to rewrite internal Notion
links/mentions into `[[wikilinks]]`.

Pass 1 (done by sync.py while walking the page tree): every page that will
exist in the vault after this run is registered here with its notion id and
the Obsidian link target (vault-relative path, without extension).

Pass 2 (done while rendering Markdown): `PageIndex.resolve()` turns a Notion
page id into that link target, so `markdown.py` never has to know how paths
were chosen.
"""

from __future__ import annotations

import re

_ID_RE = re.compile(
    r"[0-9a-fA-F]{32}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def normalize_id(notion_id: str) -> str:
    """Canonical form: lowercase, no dashes. Used as the index's internal key."""
    return notion_id.replace("-", "").lower()


def extract_page_id_from_url(url: str) -> str | None:
    """Pull a Notion page ID out of a notion.so URL, or return None."""
    if "notion.so" not in url and "notion.site" not in url:
        return None
    match = _ID_RE.search(url)
    if not match:
        return None
    return normalize_id(match.group(0))


class PageIndex:
    """notion_id -> (title, obsidian link target) with collision-safe targets."""

    def __init__(self) -> None:
        self._by_id: dict[str, str] = {}
        self._targets_seen: dict[str, str] = {}  # link target -> owning notion_id

    def register(self, notion_id: str, link_target: str) -> None:
        """Register the (already collision-resolved) link target for a page."""
        self._by_id[normalize_id(notion_id)] = link_target

    def resolve(self, notion_id: str | None) -> str | None:
        if not notion_id:
            return None
        return self._by_id.get(normalize_id(notion_id))

    def __contains__(self, notion_id: str) -> bool:
        return normalize_id(notion_id) in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)


def resolve_collision(
    desired_target: str, notion_id: str, taken: dict[str, str], disambiguator: str
) -> str:
    """If `desired_target` is already used by a different page, fall back to a
    path that includes `disambiguator` (typically the parent folder chain) to
    keep links unambiguous.
    """
    owner = taken.get(desired_target)
    if owner is None or owner == notion_id:
        taken[desired_target] = notion_id
        return desired_target
    disambiguated = f"{disambiguator}"
    taken[disambiguated] = notion_id
    return disambiguated
