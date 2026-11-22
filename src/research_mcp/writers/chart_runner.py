"""Sandboxed chart execution: user visualization code → assets/ figures.

The chart code runs in a forked child process with a CPU-time limit, a wall-clock
timeout and captured stdout/stderr, so a runaway or noisy plot cannot wedge the
MCP server, exhaust its address space or corrupt the JSON-RPC stream on stdout.
The namespace restriction (no ``__import__``/``open``/``eval``) still applies
inside the child; it is a guard against accidental misuse, not a boundary against
an operator who can run their own Python.
"""

import io
import multiprocessing
import os
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
    "NameError": NameError,
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

# Exceptions users actually trigger; anything else propagates as RuntimeError.
_REPORTED_EXCEPTIONS = {
    "NameError": NameError,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "ZeroDivisionError": ZeroDivisionError,
}

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_CPU_SECONDS = 60


def _safe_filename(filename: str) -> str:
    name = Path(filename).name  # strip any directory components / traversal
    if not name or name in {".", ".."}:
        name = "chart.png"
    if not name.lower().endswith((".png", ".svg")):
        name += ".png"
    return name


def _timeout_seconds() -> float:
    raw = os.environ.get("RESEARCH_MCP_CHART_TIMEOUT_SECONDS")
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _cpu_seconds() -> int:
    raw = os.environ.get("RESEARCH_MCP_CHART_CPU_SECONDS")
    try:
        return max(1, int(raw)) if raw else DEFAULT_CPU_SECONDS
    except ValueError:
        return DEFAULT_CPU_SECONDS


def _apply_limits() -> None:
    """Cap CPU seconds (and address space when explicitly configured)."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX platforms
        return
    try:
        cpu = _cpu_seconds()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except (OSError, ValueError):  # pragma: no cover - platform dependent
        pass
    memory_mb = os.environ.get("RESEARCH_MCP_CHART_MEMORY_MB", "").strip()
    if memory_mb.isdigit():
        limit = int(memory_mb) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (OSError, ValueError):  # pragma: no cover - platform dependent
            pass


def _render(python_code: str, output_path: Path, assets_dir: Path, safe_name: str, dpi: int):
    """Run the user's code and save every figure; returns the saved paths."""
    namespace = {**_CHART_NAMESPACE, "__builtins__": dict(_ALLOWED_BUILTINS)}
    exec(compile(python_code, "<chart>", "exec"), namespace)
    fig_numbers = plt.get_fignums()
    if not fig_numbers:
        raise ValueError("Chart code produced no matplotlib figure.")
    if len(fig_numbers) == 1:
        plt.figure(fig_numbers[0]).savefig(output_path, dpi=dpi)
        return [output_path]
    stem, ext = safe_name.rsplit(".", 1)
    saved = []
    for i, number in enumerate(fig_numbers, start=1):
        path = assets_dir / f"{stem}-{i}.{ext}"
        plt.figure(number).savefig(path, dpi=dpi)
        saved.append(path)
    return saved


def _child(code, output_path, assets_dir, safe_name, dpi, queue) -> None:
    # A child that printed to the inherited stdout would corrupt the MCP stream.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        _apply_limits()
        saved = _render(code, output_path, assets_dir, safe_name, dpi)
        queue.put(("ok", [str(path) for path in saved]))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        queue.put(("error", (type(exc).__name__, str(exc))))
    finally:
        plt.close("all")


def run_chart(project_dir: Path, filename: str, python_code: str, dpi: int) -> list[dict]:
    assets_dir = (project_dir / "assets").resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(filename)
    output_path = assets_dir / safe_name
    timeout = _timeout_seconds()

    try:
        context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - platforms without fork
        saved = _render(python_code, output_path, assets_dir, safe_name, dpi)
        return [
            {"path": str(path), "markdown": f"![{path.stem}](assets/{path.name})"} for path in saved
        ]

    queue = context.Queue()
    process = context.Process(
        target=_child,
        args=(python_code, output_path, assets_dir, safe_name, dpi, queue),
        daemon=True,
    )
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise ValueError(f"Chart code exceeded the {timeout:g}s time limit.")

    try:
        status, payload = queue.get(timeout=5)
    except Exception as exc:  # empty queue: the child died without reporting
        raise ValueError(f"Chart process exited without a result: {exc}") from exc

    if status == "error":
        name, message = payload
        raise _REPORTED_EXCEPTIONS.get(name, RuntimeError)(message)

    saved = [Path(path) for path in payload]
    return [
        {"path": str(path), "markdown": f"![{path.stem}](assets/{path.name})"} for path in saved
    ]
