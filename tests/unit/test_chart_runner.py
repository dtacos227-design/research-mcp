import pytest

from research_mcp.writers.chart_runner import run_chart


def test_run_chart_saves_figure_without_explicit_savefig(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    results = run_chart(
        project_dir,
        "fig1.png",
        "plt.plot([1, 2, 3], [4, 5, 6])",
        150,
    )

    assert len(results) == 1
    saved = project_dir / "assets" / "fig1.png"
    assert saved.exists()
    assert saved.stat().st_size > 0
    assert results[0]["path"] == str(saved)
    assert results[0]["markdown"] == "![fig1](assets/fig1.png)"


def test_run_chart_blocks_open_builtin(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    with pytest.raises(NameError):
        run_chart(project_dir, "evil.png", "open('/etc/passwd')", 100)


def test_run_chart_blocks_import(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    with pytest.raises(NameError):
        run_chart(project_dir, "evil.png", "__import__('os')", 100)


def test_run_chart_coerces_path_traversal_filename(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    results = run_chart(project_dir, "../../evil.png", "plt.plot([1], [1])", 100)

    assert len(results) == 1
    saved = project_dir / "assets" / "evil.png"
    assert saved.exists()
    assert results[0]["path"] == str(saved)
