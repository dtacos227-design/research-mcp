"""Shared pytest fixtures for the research-mcp test suite."""

import pytest

from research_mcp.providers.aggregator import clear_search_cache
from research_mcp.styles import registry


@pytest.fixture(autouse=True)
def _hermetic_styles_dir(tmp_path, monkeypatch):
    """Point the style registry at an empty per-test dir.

    Bundled styles stay visible, but a developer's ``~/.research-mcp/styles``
    overrides can never leak into a test run.
    """
    styles_dir = tmp_path / "style_overrides"
    styles_dir.mkdir()
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(styles_dir))
    registry.reload_styles()
    yield
    # Restore the isolated dir (a test may have pointed the env at a bad path)
    # so the eager reload below cannot fail during teardown.
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(styles_dir))
    registry.reload_styles()


@pytest.fixture(autouse=True)
def _empty_search_cache():
    """The provider search cache is process-global; keep tests independent."""
    clear_search_cache()
    yield
    clear_search_cache()
