from notion_obsidian_sync.links import PageIndex, extract_page_id_from_url, normalize_id


def test_normalize_id_strips_dashes_and_lowercases():
    expected = "aaaa1111222233334444555566667777"
    assert normalize_id("AAAA1111-2222-3333-4444-555566667777") == expected


def test_page_index_register_and_resolve():
    index = PageIndex()
    index.register("aaaa1111-2222-3333-4444-555566667777", "Notion/Projects/Alpha")
    assert index.resolve("aaaa1111-2222-3333-4444-555566667777") == "Notion/Projects/Alpha"
    # resolves regardless of dash/case formatting
    assert index.resolve("AAAA1111222233334444555566667777") == "Notion/Projects/Alpha"


def test_page_index_resolve_unknown_returns_none():
    index = PageIndex()
    assert index.resolve("does-not-exist") is None
    assert index.resolve(None) is None


def test_extract_page_id_from_notion_url_with_title():
    url = "https://www.notion.so/My-Workspace/Project-Alpha-aaaa1111222233334444555566667777"
    assert extract_page_id_from_url(url) == "aaaa1111222233334444555566667777"


def test_extract_page_id_from_bare_url():
    url = "https://www.notion.so/aaaa1111222233334444555566667777"
    assert extract_page_id_from_url(url) == "aaaa1111222233334444555566667777"


def test_extract_page_id_from_non_notion_url_returns_none():
    assert extract_page_id_from_url("https://example.com/aaaa1111222233334444555566667777") is None
