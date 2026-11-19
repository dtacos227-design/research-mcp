from research_mcp.server import _truncate_abstract


def test_none_stays_none():
    assert _truncate_abstract(None) is None


def test_short_string_untouched():
    assert _truncate_abstract("short") == "short"


def test_exactly_300_untouched():
    assert _truncate_abstract("x" * 300) == "x" * 300


def test_301_chars_cut_to_300_plus_ellipsis():
    out = _truncate_abstract("x" * 301)
    assert out == "x" * 300 + "..."
    assert len(out) == 303
