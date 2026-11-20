"""Sandboxed chart execution: user visualization code → assets/ figures."""

import io
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend, no display required
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

# Builtins a user's chart code may legitimately use, minus the dangerous ones
# (__import__, open, eval, exec, compile, globals, locals, vars, input, and
# anything that reaches the OS). This is a namespace restriction for a
# local-first tool, not a security boundary against a malicious operator who
# could just run their own Python.
_ALLOWED_BUILTINS = {
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "reversed": reversed,
    "iter": iter,
    "next": next,
    "min": min,
    "max": max,
    "sum": sum,
    "any": any,
    "all": all,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "slice": slice,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "bytes": bytes,
    "repr": repr,
    "print": print,
    "isinstance": isinstance,
    "type": type,
    "object": object,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "property": property,
    "super": super,
    "classmethod": classmethod,
    "staticmethod": staticmethod,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "ZeroDivisionError": ZeroDivisionError,
}

_CHART_NAMESPACE = {
    "plt": plt,
    "sns": sns,
    "np": np,
    "pd": pd,
    "__builtins__": _ALLOWED_BUILTINS,
}


def _safe_filename(filename: str) -> str:
    name = Path(filename).name  # strip any directory components / traversal
    if not name or name in {".", ".."}:
        name = "chart.png"
    if not name.lower().endswith((".png", ".svg")):
        name += ".png"
    return name


def run_chart(project_dir: Path, filename: str, python_code: str, dpi: int) -> list[dict]:
    assets_dir = (project_dir / "assets").resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(filename)
    output_path = assets_dir / safe_name

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = captured_stdout, captured_stderr
    try:
        exec(compile(python_code, "<chart>", "exec"), _CHART_NAMESPACE)
        fig_numbers = plt.get_fignums()
        if not fig_numbers:
            raise ValueError("Chart code produced no matplotlib figure.")
        if len(fig_numbers) == 1:
            plt.figure(fig_numbers[0]).savefig(output_path, dpi=dpi)
        else:
            stem, ext = safe_name.rsplit(".", 1)
            for i, number in enumerate(fig_numbers, start=1):
                plt.figure(number).savefig(assets_dir / f"{stem}-{i}.{ext}", dpi=dpi)
    finally:
        plt.close("all")
        sys.stdout, sys.stderr = old_stdout, old_stderr

    saved = (
        [output_path]
        if len(fig_numbers) == 1
        else [
            assets_dir / f"{safe_name.rsplit('.', 1)[0]}-{i}.{safe_name.rsplit('.', 1)[1]}"
            for i in range(1, len(fig_numbers) + 1)
        ]
    )
    return [
        {
            "path": str(path),
            "markdown": f"![{path.stem}](assets/{path.name})",
        }
        for path in saved
    ]
