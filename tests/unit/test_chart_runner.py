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


def test_chart_runs_do_not_share_namespace(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    run_chart(
        project_dir,
        "first.png",
        "leak_marker = 'from-first-call'\n__builtins__['len'] = 'poisoned'\nplt.plot([1], [1])",
        100,
    )

    # A fresh namespace must not see variables (or poisoned builtins) from the
    # previous call — otherwise an unrelated export fails confusingly later.
    second_code = (
        "try:\n"
        "    leak_marker\n"
        "except NameError:\n"
        "    pass\n"
        "else:\n"
        "    raise ValueError('chart namespace leaked between calls')\n"
        "assert len([1, 2]) == 2\n"
        "plt.plot([2], [2])"
    )
    run_chart(project_dir, "second.png", second_code, 100)
    assert (project_dir / "assets" / "second.png").exists()


def test_runaway_chart_is_killed_by_the_cpu_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_CHART_CPU_SECONDS", "1")
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    with pytest.raises(ValueError):
        run_chart(project_dir, "loop.png", "while True:\n    pass", 100)


def test_child_stdout_does_not_reach_the_parent(tmp_path, capsys):
    project_dir = tmp_path / "proj"
    (project_dir / "assets").mkdir(parents=True)

    run_chart(project_dir, "quiet.png", "print('LEAKED')\nplt.plot([1], [1])", 100)

    captured = capsys.readouterr()
    assert "LEAKED" not in captured.out
    assert "LEAKED" not in captured.err
