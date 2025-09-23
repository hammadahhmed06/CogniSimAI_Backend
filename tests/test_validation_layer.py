import pytest
from app.agents import epic_decomposer as ed


def test_schema_validate_valid():
    data = {"stories": [
        {"title": "User sees dashboard", "acceptance_criteria": ["Shows key metrics", "Loads under 2s"]},
        {"title": "User edits profile", "acceptance_criteria": ["Can change name"]},
    ]}
    stories, warnings = ed._schema_validate(data)  # type: ignore
    assert stories is not None
    assert len(stories) == 2
    assert not any('invalid' in w for w in warnings)


def test_schema_validate_invalid_root():
    stories, warnings = ed._schema_validate([])  # type: ignore
    assert stories is None
    assert any('parsed root not object' in w for w in warnings)


def test_final_normalize_dedup_and_limit():
    raw = [
        {"title": "A", "acceptance_criteria": []},
        {"title": "a", "acceptance_criteria": []},  # duplicate (case insens.)
        {"title": "B", "acceptance_criteria": []},
        {"title": "C", "acceptance_criteria": []},
    ]
    norm, warn = ed._final_normalize(raw, max_stories=2)
    assert len(norm) == 2
    assert any('duplicate title removed' in w for w in warn)
    assert any('truncated to max_stories=2' in w for w in warn)


def test_lint_vague_term():
    warnings = ed._lint_acceptance_criteria(["System should maybe work", "Valid output"])
    assert any('vague term' in w for w in warnings)


def test_safe_parse_json_direct():
    raw = '{"stories":[{"title":"One","acceptance_criteria":["a"]}]}'
    parsed = ed._safe_parse_json(raw)
    assert parsed and isinstance(parsed.get('stories'), list)


def test_safe_parse_json_heuristic_repair():
    # Missing JSON but bullet list present
    raw = """
    1. User can view list
    - Shows 10 items
    - Paginates results
    2. User can delete item
    - Remove confirmation dialog
    """
    parsed = ed._safe_parse_json(raw)
    assert parsed and len(parsed.get('stories', [])) == 2
    titles = [s['title'] for s in parsed['stories']]
    assert any('view list' in t.lower() for t in titles)


def test_safe_parse_json_balanced_brace_slice():
    raw = 'Noise prefix ```\n {"stories": [{"title": "X","acceptance_criteria": []}]} trailing'
    parsed = ed._safe_parse_json(raw)
    assert parsed and parsed['stories'][0]['title'] == 'X'
