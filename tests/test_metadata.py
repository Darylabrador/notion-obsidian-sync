from notion_obsidian_sync.metadata import (
    compose_note,
    get_title,
    parse_frontmatter,
    properties_to_frontmatter,
    relation_property_ids,
)


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "plain_text": text}]


def test_get_title_extracts_title_property():
    props = {
        "Name": {"type": "title", "title": _rt("Project Alpha")},
        "Status": {"type": "status", "status": {"name": "In progress"}},
    }
    assert get_title(props) == "Project Alpha"


def test_properties_to_frontmatter_covers_common_types():
    props = {
        "Name": {"type": "title", "title": _rt("Ignored")},
        "Summary": {"type": "rich_text", "rich_text": _rt("A summary")},
        "Count": {"type": "number", "number": 42},
        "Category": {"type": "select", "select": {"name": "Research"}},
        "Tags": {"type": "multi_select", "multi_select": [{"name": "ai"}, {"name": "notes"}]},
        "Done": {"type": "checkbox", "checkbox": True},
        "Due": {"type": "date", "date": {"start": "2026-08-27", "end": None}},
        "Link": {"type": "url", "url": "https://example.com"},
        "Contact": {"type": "email", "email": "a@example.com"},
        "Empty": {"type": "rich_text", "rich_text": []},
    }
    fm = properties_to_frontmatter(props)

    assert "Name" not in fm  # title becomes the H1, not frontmatter
    assert fm["Summary"] == "A summary"
    assert fm["Count"] == 42
    assert fm["Category"] == "Research"
    assert fm["Tags"] == ["ai", "notes"]
    assert fm["Done"] is True
    assert fm["Due"] == "2026-08-27"
    assert fm["Link"] == "https://example.com"
    assert fm["Contact"] == "a@example.com"
    assert "Empty" not in fm


def test_properties_to_frontmatter_date_range():
    props = {"Window": {"type": "date", "date": {"start": "2026-01-01", "end": "2026-01-10"}}}
    fm = properties_to_frontmatter(props)
    assert fm["Window"] == "2026-01-01 -> 2026-01-10"


def test_relation_property_ids():
    props = {
        "Related": {"type": "relation", "relation": [{"id": "id-1"}, {"id": "id-2"}]},
        "Other": {"type": "checkbox", "checkbox": False},
    }
    assert relation_property_ids(props) == {"Related": ["id-1", "id-2"]}


def test_compose_and_parse_frontmatter_roundtrip():
    fm = {"notion_id": "abc123", "tags": ["a", "b"], "managed_by": "notion-obsidian-sync"}
    note = compose_note(fm, "My Title", "Some body text.\n")

    assert note.startswith("---\n")
    assert "# My Title" in note
    assert "Some body text." in note

    parsed_fm, rest = parse_frontmatter(note)
    assert parsed_fm["notion_id"] == "abc123"
    assert parsed_fm["tags"] == ["a", "b"]
    assert "# My Title" in rest


def test_parse_frontmatter_handles_missing_frontmatter():
    fm, rest = parse_frontmatter("# Just a title\n\nbody")
    assert fm == {}
    assert rest == "# Just a title\n\nbody"


def test_yaml_output_is_valid_and_unicode_safe():
    fm = {"title": "Étude énergétique", "tags": ["café", "recherche"]}
    note = compose_note(fm, "Étude", "corps")
    parsed, _ = parse_frontmatter(note)
    assert parsed["title"] == "Étude énergétique"
    assert parsed["tags"] == ["café", "recherche"]
