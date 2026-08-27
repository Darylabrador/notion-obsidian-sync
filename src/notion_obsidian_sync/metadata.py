"""Convert Notion page properties into plain Python values suitable for YAML
frontmatter (rendered with PyYAML, never hand-built strings).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

FRONTMATTER_DELIMITER = "---"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


def rich_text_plain(rich_text: list[dict[str, Any]]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text or [])


# kept for internal call sites written before the function was made public
_rich_text_plain = rich_text_plain


def _convert_value(prop: dict[str, Any]) -> Any:
    ptype = prop.get("type")
    if ptype is None:
        return None
    value = prop.get(ptype)

    if ptype == "title":
        return rich_text_plain(value or []) or None
    if ptype == "rich_text":
        return rich_text_plain(value or []) or None
    if ptype == "number":
        return value
    if ptype == "select":
        return value.get("name") if value else None
    if ptype == "status":
        return value.get("name") if value else None
    if ptype == "multi_select":
        return [o.get("name") for o in value or []]
    if ptype == "checkbox":
        return bool(value)
    if ptype == "date":
        if not value:
            return None
        if value.get("end"):
            return f"{value.get('start')} -> {value.get('end')}"
        return value.get("start")
    if ptype == "url":
        return value
    if ptype == "email":
        return value
    if ptype == "phone_number":
        return value
    if ptype == "people":
        return [p.get("name") or p.get("id") for p in value or []]
    if ptype == "files":
        return [f.get("name") for f in value or []]
    if ptype == "relation":
        # Resolved to Obsidian links by links.py after the page index is built;
        # here we only record the raw related page IDs.
        return [r.get("id") for r in value or []]
    if ptype == "formula":
        return _convert_formula(value)
    if ptype == "rollup":
        return _convert_rollup(value)
    if ptype == "created_time":
        return value
    if ptype == "last_edited_time":
        return value
    if ptype == "created_by":
        return (value or {}).get("name") or (value or {}).get("id")
    if ptype == "last_edited_by":
        return (value or {}).get("name") or (value or {}).get("id")
    if ptype == "unique_id":
        prefix = (value or {}).get("prefix")
        number = (value or {}).get("number")
        if prefix and number is not None:
            return f"{prefix}-{number}"
        return number
    if ptype == "verification":
        return (value or {}).get("state")

    # Unknown/unsupported property type: best-effort readable fallback.
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _convert_formula(value: dict[str, Any] | None) -> Any:
    if not value:
        return None
    ftype = value.get("type")
    if ftype in ("string", "number", "boolean"):
        return value.get(ftype)
    if ftype == "date":
        d = value.get("date")
        return d.get("start") if d else None
    return None


def _convert_rollup(value: dict[str, Any] | None) -> Any:
    if not value:
        return None
    rtype = value.get("type")
    if rtype == "number":
        return value.get("number")
    if rtype == "array":
        return [_convert_value(item) for item in value.get("array", [])]
    if rtype == "date":
        d = value.get("date")
        return d.get("start") if d else None
    return None


def properties_to_frontmatter(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Convert a Notion page's `properties` dict into a flat dict of YAML-safe
    values, keyed by the human-readable property name. `None`/empty values are
    dropped to keep frontmatter clean.
    """
    result: dict[str, Any] = {}
    for name, prop in properties.items():
        if prop.get("type") == "title":
            continue  # the title becomes the note's H1 / filename, not frontmatter
        value = _convert_value(prop)
        if value in (None, "", []):
            continue
        result[name] = value
    return result


def get_title(properties: dict[str, dict[str, Any]]) -> str:
    for prop in properties.values():
        if prop.get("type") == "title":
            return _rich_text_plain(prop.get("title", [])).strip()
    return ""


def relation_property_ids(properties: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Map property name -> related page IDs, for relation-type properties only."""
    result = {}
    for name, prop in properties.items():
        if prop.get("type") == "relation":
            result[name] = [r.get("id") for r in prop.get("relation", [])]
    return result


def compose_note(frontmatter: dict[str, Any], title: str, body: str) -> str:
    """Assemble the final `.md` file content: YAML frontmatter + H1 title + body."""
    yaml_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    heading = f"# {title}\n\n" if title else ""
    return f"{FRONTMATTER_DELIMITER}\n{yaml_text}{FRONTMATTER_DELIMITER}\n\n{heading}{body}"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter dict, rest-of-file). Returns ({}, content)
    if there is no valid frontmatter block.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    raw_yaml, rest = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        return {}, content
    if not isinstance(data, dict):
        return {}, content
    return data, rest
