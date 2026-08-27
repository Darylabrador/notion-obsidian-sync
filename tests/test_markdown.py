from notion_obsidian_sync.links import PageIndex
from notion_obsidian_sync.markdown import RenderContext, blocks_to_markdown


def _ctx(index: PageIndex | None = None) -> RenderContext:
    index = index or PageIndex()
    return RenderContext(resolve_link=index.resolve)


def _rt(text: str, **annotations) -> dict:
    return {
        "type": "text",
        "plain_text": text,
        "href": annotations.pop("href", None),
        "annotations": {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            **annotations,
        },
    }


def test_paragraph_and_headings():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [_rt("Title")]}},
        {"type": "paragraph", "paragraph": {"rich_text": [_rt("Hello world")]}},
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "# Title" in md
    assert "Hello world" in md


def test_bold_italic_code_strikethrough():
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    _rt("bold", bold=True),
                    _rt(" "),
                    _rt("italic", italic=True),
                    _rt(" "),
                    _rt("code", code=True),
                    _rt(" "),
                    _rt("strike", strikethrough=True),
                ]
            },
        }
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "**bold**" in md
    assert "*italic*" in md
    assert "`code`" in md
    assert "~~strike~~" in md


def test_bulleted_list():
    blocks = [
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rt("Item 1")]}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rt("Item 2")]}},
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "- Item 1" in md
    assert "- Item 2" in md


def test_numbered_list_resets_on_interruption():
    blocks = [
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [_rt("First")]}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [_rt("Second")]}},
        {"type": "paragraph", "paragraph": {"rich_text": [_rt("Interruption")]}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [_rt("Restarted")]}},
    ]
    md = blocks_to_markdown(blocks, _ctx())
    lines = [line for line in md.splitlines() if line.strip()]
    assert lines[0].startswith("1. First")
    assert lines[1].startswith("2. Second")
    assert lines[-1].startswith("1. Restarted")


def test_to_do_checked_and_unchecked():
    blocks = [
        {"type": "to_do", "to_do": {"rich_text": [_rt("Done task")], "checked": True}},
        {"type": "to_do", "to_do": {"rich_text": [_rt("Open task")], "checked": False}},
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "- [x] Done task" in md
    assert "- [ ] Open task" in md


def test_quote_and_divider():
    blocks = [
        {"type": "quote", "quote": {"rich_text": [_rt("Wise words")]}},
        {"type": "divider", "divider": {}},
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "> Wise words" in md
    assert "---" in md


def test_callout_maps_icon_to_obsidian_admonition():
    blocks = [
        {
            "type": "callout",
            "callout": {
                "rich_text": [_rt("Careful here")],
                "icon": {"emoji": "⚠️"},
            },
        }
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "[!WARNING]" in md
    assert "Careful here" in md


def test_code_block_with_language():
    blocks = [
        {
            "type": "code",
            "code": {
                "rich_text": [_rt("print('hi')")],
                "language": "python",
                "caption": [],
            },
        }
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "```python" in md
    assert "print('hi')" in md
    assert "```" in md


def test_table_renders_header_separator_and_rows():
    blocks = [
        {
            "type": "table",
            "table": {"has_column_header": True},
            "_children": [
                {"type": "table_row", "table_row": {"cells": [[_rt("A")], [_rt("B")]]}},
                {"type": "table_row", "table_row": {"cells": [[_rt("1")], [_rt("2")]]}},
            ],
        }
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "| A | B |" in md
    assert "|---|---|" in md
    assert "| 1 | 2 |" in md


def test_child_page_resolves_to_wikilink_when_known():
    index = PageIndex()
    index.register("11111111-1111-1111-1111-111111111111", "Notion/Child Page")
    blocks = [
        {
            "type": "child_page",
            "id": "11111111-1111-1111-1111-111111111111",
            "child_page": {"title": "Child Page"},
        }
    ]
    md = blocks_to_markdown(blocks, _ctx(index))
    assert "[[Notion/Child Page]]" in md


def test_child_page_falls_back_to_title_when_unresolved():
    blocks = [
        {"type": "child_page", "id": "unknown-id", "child_page": {"title": "Orphan Child"}}
    ]
    md = blocks_to_markdown(blocks, _ctx())
    assert "Orphan Child" in md


def test_unsupported_block_leaves_html_comment():
    blocks = [{"type": "unsupported_future_block"}]
    md = blocks_to_markdown(blocks, _ctx())
    assert "<!-- Unsupported Notion block: unsupported_future_block -->" in md


def test_nested_bulleted_list_indents_children():
    blocks = [
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_rt("Parent")]},
            "_children": [
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [_rt("Child")]},
                }
            ],
        }
    ]
    md = blocks_to_markdown(blocks, _ctx())
    lines = md.splitlines()
    parent_line = next(line for line in lines if "Parent" in line)
    child_line = next(line for line in lines if "Child" in line)
    assert not parent_line.startswith(" ")
    assert child_line.startswith("  ")
