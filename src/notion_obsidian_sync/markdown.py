"""Convert a Notion block tree into Obsidian-flavored Markdown.

Blocks are expected as plain dicts as returned by the Notion API, with one
extension: a block that has children must carry them under the `"_children"`
key (a list of blocks, recursively in the same shape). Building that tree is
the caller's job (see `sync.py:fetch_block_tree`), which keeps this module
pure and easy to unit test without any HTTP mocking.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# notion page id (with or without dashes) -> obsidian link target, or None if unresolved
LinkResolver = Callable[[str], str | None]
# block dict -> local relative path string for the downloaded asset, or None on failure
AssetDownloader = Callable[[dict[str, Any], str], str | None]

_CALLOUT_ICON_MAP = {
    "💡": "TIP",
    "ℹ️": "NOTE",
    "📌": "NOTE",
    "⚠️": "WARNING",
    "❗": "IMPORTANT",
    "❕": "IMPORTANT",
    "🔥": "IMPORTANT",
    "✅": "SUCCESS",
    "❌": "BUG",
    "🐛": "BUG",
    "❓": "QUESTION",
}

INDENT_UNIT = "  "  # 2 spaces per nesting level


@dataclass
class RenderContext:
    resolve_link: LinkResolver
    download_asset: AssetDownloader | None = None
    page_id: str = ""
    warnings: list[str] = field(default_factory=list)


def blocks_to_markdown(blocks: list[dict[str, Any]], ctx: RenderContext) -> str:
    lines = _render_children(blocks, ctx, level=0)
    return "\n".join(lines).strip() + "\n"


# -- rich text -------------------------------------------------------------------


def rich_text_to_markdown(rich_text: list[dict[str, Any]], ctx: RenderContext) -> str:
    parts: list[str] = []
    for rt in rich_text or []:
        parts.append(_annotated_segment(rt, ctx))
    return "".join(parts)


def _annotated_segment(rt: dict[str, Any], ctx: RenderContext) -> str:
    rt_type = rt.get("type")
    if rt_type == "equation":
        return f"${rt.get('equation', {}).get('expression', '')}$"

    if rt_type == "mention":
        return _render_mention(rt, ctx)

    text = rt.get("plain_text", "")
    if not text:
        return ""

    href = rt.get("href")
    annotations = rt.get("annotations", {}) or {}

    body = text
    if annotations.get("code"):
        body = f"`{body}`"
    if annotations.get("strikethrough"):
        body = f"~~{body}~~"
    if annotations.get("italic"):
        body = f"*{body}*"
    if annotations.get("bold"):
        body = f"**{body}**"
    if annotations.get("underline"):
        body = f"<u>{body}</u>"

    if href:
        resolved = _resolve_notion_url(href, ctx)
        body = f"[[{resolved}|{text}]]" if resolved else f"[{body}]({href})"

    return body


def _render_mention(rt: dict[str, Any], ctx: RenderContext) -> str:
    mention = rt.get("mention", {}) or {}
    mtype = mention.get("type")
    plain = rt.get("plain_text", "")

    if mtype == "page":
        page_id = mention.get("page", {}).get("id", "")
        resolved = ctx.resolve_link(page_id)
        return f"[[{resolved}]]" if resolved else (plain or "[Untitled]")
    if mtype == "database":
        return plain or "[Database]"
    if mtype == "date":
        date = mention.get("date", {})
        return date.get("start", plain)
    if mtype == "link_preview":
        url = mention.get("link_preview", {}).get("url", "")
        return f"[{plain or url}]({url})"
    return plain


def _resolve_notion_url(url: str, ctx: RenderContext) -> str | None:
    """If `url` is a link to another Notion page we know about, return the
    Obsidian link target; otherwise return None (caller keeps it as a plain URL).
    """
    from .links import extract_page_id_from_url

    page_id = extract_page_id_from_url(url)
    if not page_id:
        return None
    return ctx.resolve_link(page_id)


# -- block dispatch -------------------------------------------------------------


def _render_children(blocks: list[dict[str, Any]], ctx: RenderContext, level: int) -> list[str]:
    lines: list[str] = []
    numbered_counter = 0
    prev_was_numbered = False

    for block in blocks:
        btype = block.get("type")
        is_numbered = btype == "numbered_list_item"
        numbered_counter = numbered_counter + 1 if is_numbered and prev_was_numbered else 1
        prev_was_numbered = is_numbered

        rendered = _render_block(block, ctx, level, numbered_counter)
        if rendered:
            lines.extend(rendered)

    return lines


def _prefix_lines(lines: list[str], level: int) -> list[str]:
    if level == 0:
        return lines
    pad = INDENT_UNIT * level
    return [f"{pad}{line}" if line else line for line in lines]


def _children_of(block: dict[str, Any]) -> list[dict[str, Any]]:
    return block.get("_children", []) or []


def _render_block(
    block: dict[str, Any], ctx: RenderContext, level: int, numbered_index: int
) -> list[str]:
    btype = block.get("type", "")
    handler = _HANDLERS.get(btype)
    if handler is None:
        comment = f"<!-- Unsupported Notion block: {btype} -->"
        return [comment, ""]
    return handler(block, ctx, level, numbered_index)


def _text_block(kind: str) -> Callable[..., list[str]]:
    def render(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
        data = block.get(kind, {})
        text = rich_text_to_markdown(data.get("rich_text", []), ctx)
        prefix = {
            "heading_1": "# ",
            "heading_2": "## ",
            "heading_3": "### ",
            "paragraph": "",
        }[kind]
        out = [f"{prefix}{text}".rstrip() if text or prefix else "", ""]
        if data.get("is_toggleable") and _children_of(block):
            inner = _render_children(_children_of(block), ctx, 0)
            out = [
                "<details>",
                f"<summary>{text}</summary>",
                "",
                *inner,
                "",
                "</details>",
                "",
            ]
        elif _children_of(block):
            out.extend(_prefix_lines(_render_children(_children_of(block), ctx, 0), level + 1))
        return out

    return render


def _bulleted_list_item(
    block: dict[str, Any], ctx: RenderContext, level: int, _idx: int
) -> list[str]:
    text = rich_text_to_markdown(block.get("bulleted_list_item", {}).get("rich_text", []), ctx)
    lines = [f"{INDENT_UNIT * level}- {text}"]
    lines.extend(_render_nested_list_content(block, ctx, level))
    return lines


def _numbered_list_item(
    block: dict[str, Any], ctx: RenderContext, level: int, idx: int
) -> list[str]:
    text = rich_text_to_markdown(block.get("numbered_list_item", {}).get("rich_text", []), ctx)
    lines = [f"{INDENT_UNIT * level}{idx}. {text}"]
    lines.extend(_render_nested_list_content(block, ctx, level))
    return lines


def _to_do(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("to_do", {})
    text = rich_text_to_markdown(data.get("rich_text", []), ctx)
    box = "x" if data.get("checked") else " "
    lines = [f"{INDENT_UNIT * level}- [{box}] {text}"]
    lines.extend(_render_nested_list_content(block, ctx, level))
    return lines


def _render_nested_list_content(block: dict[str, Any], ctx: RenderContext, level: int) -> list[str]:
    children = _children_of(block)
    if not children:
        return []
    return _render_children(children, ctx, level + 1)


def _quote(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    text = rich_text_to_markdown(block.get("quote", {}).get("rich_text", []), ctx)
    body_lines = [text] if text else []
    for child_line in _render_children(_children_of(block), ctx, 0):
        body_lines.append(child_line)
    quoted = [f"> {line}" if line else ">" for line in body_lines]
    quoted.append("")
    return [f"{INDENT_UNIT * level}{line}" for line in quoted]


def _callout(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("callout", {})
    text = rich_text_to_markdown(data.get("rich_text", []), ctx)
    icon = (data.get("icon") or {}).get("emoji", "")
    kind = _CALLOUT_ICON_MAP.get(icon, "NOTE")
    body_lines = [text] if text else []
    body_lines.extend(_render_children(_children_of(block), ctx, 0))
    quoted = [f"[!{kind}]"] + [f"> {line}" if line else ">" for line in body_lines]
    quoted = [f"> {quoted[0]}", *quoted[1:]]
    quoted.append("")
    return [f"{INDENT_UNIT * level}{line}" for line in quoted]


def _divider(_block: dict[str, Any], _ctx: RenderContext, level: int, _idx: int) -> list[str]:
    return [f"{INDENT_UNIT * level}---", ""]


def _code(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("code", {})
    text = "".join(rt.get("plain_text", "") for rt in data.get("rich_text", []))
    lang = data.get("language", "") or ""
    caption = rich_text_to_markdown(data.get("caption", []), ctx)
    pad = INDENT_UNIT * level
    lines = [f"{pad}```{lang}"]
    lines.extend(f"{pad}{line}" for line in text.split("\n"))
    lines.append(f"{pad}```")
    if caption:
        lines.append(f"{pad}*{caption}*")
    lines.append("")
    return lines


def _equation(block: dict[str, Any], _ctx: RenderContext, level: int, _idx: int) -> list[str]:
    expr = block.get("equation", {}).get("expression", "")
    pad = INDENT_UNIT * level
    return [f"{pad}$$", f"{pad}{expr}", f"{pad}$$", ""]


def _bookmark(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("bookmark", {})
    url = data.get("url", "")
    caption = rich_text_to_markdown(data.get("caption", []), ctx) or url
    return [f"{INDENT_UNIT * level}[{caption}]({url})", ""]


def _child_page(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    page_id = block.get("id", "")
    title = block.get("child_page", {}).get("title", "Untitled")
    resolved = ctx.resolve_link(page_id)
    text = f"[[{resolved}]]" if resolved else title
    return [f"{INDENT_UNIT * level}- {text}", ""]


def _child_database(block: dict[str, Any], _ctx: RenderContext, level: int, _idx: int) -> list[str]:
    title = block.get("child_database", {}).get("title", "Untitled database")
    pad = INDENT_UNIT * level
    return [
        f"{pad}> [!NOTE] Nested database: {title}",
        f"{pad}> Not synced automatically. Add its ID to NOTION_DATABASE_IDS to sync it.",
        "",
    ]


def _link_to_page(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("link_to_page", {})
    page_id = data.get("page_id") or data.get("database_id")
    resolved = ctx.resolve_link(page_id) if page_id else None
    text = f"[[{resolved}]]" if resolved else "[Notion link]"
    return [f"{INDENT_UNIT * level}{text}", ""]


def _synced_block(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    return _render_children(_children_of(block), ctx, level)


def _column_list(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    lines: list[str] = []
    for column in _children_of(block):
        lines.extend(_render_children(_children_of(column), ctx, level))
    return lines


def _table(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    data = block.get("table", {})
    rows = _children_of(block)
    if not rows:
        return []
    has_header = data.get("has_column_header", False)
    pad = INDENT_UNIT * level

    def render_row(row: dict[str, Any]) -> str:
        cells = row.get("table_row", {}).get("cells", [])
        rendered_cells = [rich_text_to_markdown(cell, ctx).replace("|", "\\|") for cell in cells]
        return f"{pad}| " + " | ".join(rendered_cells) + " |"

    lines = [render_row(rows[0])]
    col_count = len(rows[0].get("table_row", {}).get("cells", []))
    lines.append(f"{pad}|" + "---|" * col_count)
    for row in rows[1:]:
        lines.append(render_row(row))
    if not has_header:
        pass  # Notion header styling has no direct Markdown equivalent beyond the separator row
    lines.append("")
    return lines


def _image_or_file(kind: str) -> Callable[..., list[str]]:
    def render(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
        data = block.get(kind, {})
        url = (data.get("file") or data.get("external") or {}).get("url", "")
        caption = rich_text_to_markdown(data.get("caption", []), ctx)
        local_path = None
        if ctx.download_asset and url:
            local_path = ctx.download_asset(block, url)
        pad = INDENT_UNIT * level
        if local_path:
            line = f"{pad}![[{local_path}]]"
        elif kind == "image":
            line = f"{pad}![{caption}]({url})"
        else:
            line = f"{pad}[{caption or kind}]({url})"
        out = [line]
        if caption:
            out.append(f"{pad}*{caption}*")
        out.append("")
        return out

    return render


def _video_or_embed(block: dict[str, Any], ctx: RenderContext, level: int, _idx: int) -> list[str]:
    btype = block.get("type", "")
    data = block.get(btype, {})
    url = data.get("url") or (data.get("external") or {}).get("url", "")
    caption = rich_text_to_markdown(data.get("caption", []), ctx) or url
    return [f"{INDENT_UNIT * level}[{caption}]({url})", ""]


_HANDLERS: dict[str, Callable[..., list[str]]] = {
    "paragraph": _text_block("paragraph"),
    "heading_1": _text_block("heading_1"),
    "heading_2": _text_block("heading_2"),
    "heading_3": _text_block("heading_3"),
    "bulleted_list_item": _bulleted_list_item,
    "numbered_list_item": _numbered_list_item,
    "to_do": _to_do,
    "quote": _quote,
    "callout": _callout,
    "divider": _divider,
    "code": _code,
    "equation": _equation,
    "bookmark": _bookmark,
    "child_page": _child_page,
    "child_database": _child_database,
    "link_to_page": _link_to_page,
    "synced_block": _synced_block,
    "column_list": _column_list,
    "table": _table,
    "image": _image_or_file("image"),
    "file": _image_or_file("file"),
    "pdf": _image_or_file("pdf"),
    "video": _video_or_embed,
    "embed": _video_or_embed,
}
