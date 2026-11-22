import textwrap

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.styles import registry


def _write_style(tmp_path, name, body):
    d = tmp_path / "styles"
    d.mkdir(exist_ok=True)
    (d / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Keep the module-level cache from leaking between tests."""
    yield
    registry.reload_styles()


def test_load_bundled_style(tmp_path, monkeypatch):
    spec = registry.get_style("article_imrad")
    assert spec.id == "article_imrad"
    assert spec.outline, "outline must not be empty"


def test_unknown_style_raises():
    with pytest.raises(ToolError):
        registry.get_style("does_not_exist")


def test_user_override_wins(tmp_path, monkeypatch):
    d = _write_style(
        tmp_path,
        "article_imrad.yaml",
        """
        id: article_imrad
        label: Custom Article
        outline:
          - {id: a, title: A, filename: 01_a.md, order: 1}
    """,
    )
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    registry.reload_styles()
    assert registry.get_style("article_imrad").label == "Custom Article"


def test_unknown_export_template_rejected(tmp_path, monkeypatch):
    d = _write_style(
        tmp_path,
        "bad.yaml",
        """
        id: bad
        label: Bad
        export: {latex_template: nope}
        outline:
          - {id: a, title: A, filename: 01_a.md, order: 1}
    """,
    )
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    with pytest.raises(ToolError):
        registry.reload_styles()


def test_list_styles_summarizes_bundled_styles():
    listing = registry.list_styles()
    assert any(entry["id"] == "article_imrad" for entry in listing)
    entry = next(entry for entry in listing if entry["id"] == "article_imrad")
    assert set(entry) == {"id", "label", "description", "sections"}
    assert entry["sections"]
    assert entry["sections"][0]["filename"]


def test_invalid_yaml_error_names_the_file(tmp_path, monkeypatch):
    d = tmp_path / "styles"
    d.mkdir()
    bad = d / "broken.yaml"
    bad.write_text("id: [unclosed\n", encoding="utf-8")
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    with pytest.raises(ToolError, match="broken.yaml"):
        registry.reload_styles()


def test_invalid_style_data_error_names_the_file(tmp_path, monkeypatch):
    d = _write_style(
        tmp_path,
        "incomplete.yaml",
        """
        id: incomplete
        label: Incomplete
        outline: []
    """,
    )
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    with pytest.raises(ToolError, match="incomplete.yaml"):
        registry.reload_styles()


def test_env_dir_must_be_a_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(tmp_path / "missing"))
    with pytest.raises(ToolError, match=registry.USER_STYLES_DIR_ENV):
        registry.reload_styles()


def test_reload_styles_picks_up_new_override(tmp_path, monkeypatch):
    assert registry.get_style("article_imrad").label != "Later Override"
    d = _write_style(
        tmp_path,
        "article_imrad.yaml",
        """
        id: article_imrad
        label: Later Override
        outline:
          - {id: a, title: A, filename: 01_a.md, order: 1}
    """,
    )
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    registry.reload_styles()
    assert registry.get_style("article_imrad").label == "Later Override"
