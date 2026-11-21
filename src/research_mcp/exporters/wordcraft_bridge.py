"""Detect a local WordCraft install and build handoff artefacts for its MCP server.

The desktop app, its CLI (`wordcraft-cli`) and the running app's control channel
are three different things; this module keeps their discovery in one place so the
MCP tools stay thin. Every environment- or platform-dependent function takes
explicit `env` / `platform` arguments so tests never depend on the host.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_PORT = 7981
WORDCRAFT_AUTHOR = "Research Agent"
CLI_NAME = "wordcraft-cli"
DESKTOP_NAME = "WordCraft"

_ENV_OVERRIDE = "RESEARCH_MCP_WORDCRAFT_CLI"
_PROFILE_OVERRIDE = "WORDCRAFT_PROFILE_DIR"


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _system(platform: str | None) -> str:
    return sys.platform if platform is None else platform


def _home(env: Mapping[str, str]) -> Path | None:
    home = env.get("HOME") or env.get("USERPROFILE")
    return Path(home) if home else None


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def _is_macos(platform: str) -> bool:
    return platform == "darwin"


def wordcraft_settings_dir(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> Path | None:
    """The app's settings folder, where it writes preferences, logs and the control key."""
    values = _environment(env)
    override = values.get(_PROFILE_OVERRIDE)
    if override:
        return Path(override)
    system = _system(platform)
    if _is_macos(system):
        home = _home(values)
        return home / "Library/Application Support" / DESKTOP_NAME if home else None
    if _is_windows(system):
        appdata = values.get("APPDATA")
        return Path(appdata) / DESKTOP_NAME if appdata else None
    xdg = values.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "wordcraft"
    home = _home(values)
    return home / ".config" / "wordcraft" if home else None


def control_key_path(
    port: int = DEFAULT_PORT,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path | None:
    """The token file the app writes for one control port (`control-key.local-<port>`)."""
    settings = wordcraft_settings_dir(env, platform)
    return settings / f"control-key.local-{port}" if settings else None


def _cli_names(platform: str) -> tuple[str, ...]:
    return (f"{CLI_NAME}.exe", CLI_NAME) if _is_windows(platform) else (CLI_NAME,)


def _cli_candidates(platform: str, home: Path | None, env: Mapping[str, str]) -> list[Path]:
    """Install locations the ArtCraft Launcher and package managers use, best first."""
    names = _cli_names(platform)
    roots: list[Path] = []
    if _is_macos(platform):
        roots += [
            Path("/Applications") / f"{DESKTOP_NAME}.app/Contents/MacOS",
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ]
        if home:
            roots += [
                home / "Applications" / f"{DESKTOP_NAME}.app/Contents/MacOS",
                home / ".local/bin",
                home / "bin",
            ]
    elif _is_windows(platform):
        local = env.get("LOCALAPPDATA")
        program_files = env.get("ProgramFiles") or "C:/Program Files"
        if local:
            roots += [
                Path(local) / "Programs/ArtCraft" / DESKTOP_NAME,
                Path(local) / "Programs" / CLI_NAME,
            ]
        roots.append(Path(program_files) / "ArtCraft" / DESKTOP_NAME)
    else:
        roots += [Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin")]
        if home:
            roots += [
                home / ".local/bin",
                home
                / ".var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher/apps/wordcraft",
            ]
    return [root / name for root in roots for name in names]


def detect_wordcraft_cli(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> Path | None:
    """Return the `wordcraft-cli` binary, or None when WordCraft's CLI is not installed."""
    values = _environment(env)
    system = _system(platform)

    override = values.get(_ENV_OVERRIDE)
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    path_value = values.get("PATH")
    if path_value:
        found = shutil.which(CLI_NAME, path=path_value)
        if found:
            return Path(found)

    for candidate in _cli_candidates(system, _home(values), values):
        if candidate.is_file():
            return candidate
    return None


def _desktop_candidates(platform: str, home: Path | None, env: Mapping[str, str]) -> list[Path]:
    """Where the desktop app lives, best first."""
    candidates: list[Path] = []
    if _is_macos(platform):
        candidates.append(Path("/Applications") / f"{DESKTOP_NAME}.app")
        if home:
            candidates.append(home / "Applications" / f"{DESKTOP_NAME}.app")
    elif _is_windows(platform):
        local = env.get("LOCALAPPDATA")
        program_files = env.get("ProgramFiles") or "C:/Program Files"
        if local:
            candidates.append(Path(local) / "Programs/ArtCraft" / DESKTOP_NAME)
        candidates.append(Path(program_files) / "ArtCraft" / DESKTOP_NAME)
    else:
        candidates.append(Path("/usr/bin/wordcraft"))
        if home:
            candidates += [
                home / "Applications" / f"{DESKTOP_NAME}.AppImage",
                home
                / ".var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher/apps/wordcraft",
            ]
    return candidates


def detect_wordcraft_desktop(
    platform: str | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """Return the WordCraft desktop application (bundle, executable or install folder)."""
    values = _environment(env)
    system = _system(platform)
    for candidate in _desktop_candidates(system, _home(values), values):
        if candidate.exists():
            return candidate
    return None


def probe_port(host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 0.25) -> bool:
    """True when something accepts a TCP connection on `host:port`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cli_version(binary: Path) -> str | None:
    """First line of `wordcraft-cli --version`, or None when it cannot be run."""
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    return None


def build_mcp_command(
    cli: Path | None,
    workspace_root: Path,
    author: str = WORDCRAFT_AUTHOR,
) -> list[str]:
    """The argv an MCP client needs for a confined headless WordCraft server."""
    root = str(workspace_root)
    return [
        str(cli) if cli else CLI_NAME,
        "mcp",
        "--author",
        author,
        "--automation-read-root",
        root,
        "--automation-write-root",
        root,
    ]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMAGE_RE = re.compile(r'^!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"[^"]*")?\)$')
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+")
_FENCE_RE = re.compile(r"^```(?P<language>[\w+-]*)\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

_CSL_TO_WORDCRAFT = {
    "apa": "APA",
    "ieee": "IEEE",
    "vancouver": "ISO 690 (numerical)",
    "chicago": "Chicago",
}


def citation_style_for(csl_style: str) -> str:
    """Map a bundled CSL style name to WordCraft's citation style name."""
    try:
        return _CSL_TO_WORDCRAFT[csl_style]
    except KeyError:
        raise ToolError(
            f"Unknown CSL style {csl_style!r}; available styles are {sorted(_CSL_TO_WORDCRAFT)!r}."
        ) from None


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _figure_block(match: re.Match[str], project_dir: Path) -> dict:
    raw = match.group("path")
    path = Path(raw) if Path(raw).is_absolute() else project_dir / raw
    return {
        "kind": "figure",
        "alt": match.group("alt"),
        "path": str(path),
        "exists": path.is_file(),
    }


def parse_markdown_blocks(markdown: str, project_dir: Path) -> list[dict]:
    """Split rendered Markdown into typed blocks the writing blueprint can address."""
    lines = markdown.splitlines()
    blocks: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        fence = _FENCE_RE.match(stripped)
        if fence:
            language = fence.group("language")
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            blocks.append({"kind": "code", "language": language, "text": "\n".join(code)})
            continue

        if stripped.startswith("|"):
            table: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table.append(lines[index])
                index += 1
            header = _table_cells(table[0])
            rest = table[1:]
            if rest and _TABLE_SEPARATOR_RE.match(rest[0]):
                rest = rest[1:]
            blocks.append(
                {"kind": "table", "header": header, "rows": [_table_cells(row) for row in rest]}
            )
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            items: list[str] = []
            ordered = bool(_ORDERED_RE.match(line))
            while index < len(lines):
                item = _LIST_RE.match(lines[index])
                if not item:
                    break
                items.append(item.group("text").strip())
                index += 1
            blocks.append({"kind": "list", "ordered": ordered, "items": items})
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            blocks.append(
                {
                    "kind": "heading",
                    "level": len(heading.group(1)),
                    "text": heading.group(2).strip(),
                }
            )
            index += 1
            continue

        image = _IMAGE_RE.match(stripped)
        if image:
            blocks.append(_figure_block(image, project_dir))
            index += 1
            continue

        paragraph: list[str] = []
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith("|")
                or _HEADING_RE.match(candidate)
                or _FENCE_RE.match(candidate)
                or _LIST_RE.match(lines[index])
                or _IMAGE_RE.match(candidate)
            ):
                break
            paragraph.append(candidate)
            index += 1
        if paragraph:
            blocks.append({"kind": "paragraph", "text": " ".join(paragraph)})
        else:
            index += 1
    return blocks


def _execute(command: str, params: dict | None = None) -> dict:
    return {"tool": "execute", "arguments": {"command": command, "params": params or {}}}


def _style_steps() -> list[dict]:
    return [
        _execute(
            "styles.modify",
            {
                "style": "Normal",
                "chr": {"font": "Times New Roman", "size": 12},
                "para": {"lineSpacing": {"rule": "multiple", "value": 1.5}, "spaceAfter": 6},
            },
        ),
        _execute(
            "styles.modify",
            {
                "style": "Heading1",
                "chr": {"font": "Times New Roman", "size": 16, "bold": True},
                "para": {"spaceBefore": 12, "spaceAfter": 6, "keepNext": True},
            },
        ),
        _execute(
            "styles.modify",
            {
                "style": "Heading2",
                "chr": {"font": "Times New Roman", "size": 14, "bold": True},
                "para": {"spaceBefore": 12, "spaceAfter": 4, "keepNext": True},
            },
        ),
        _execute(
            "styles.modify",
            {
                "style": "Heading3",
                "chr": {"font": "Times New Roman", "size": 12, "bold": True},
                "para": {"spaceBefore": 10, "spaceAfter": 4, "keepNext": True},
            },
        ),
    ]


def _block_steps(block: dict) -> list[dict]:
    kind = block["kind"]
    if kind == "heading":
        level = block["level"]
        typed = {"tool": "type_text", "arguments": {"text": block["text"]}}
        if level <= 3:
            return [typed, _execute(f"para.heading{level}")]
        return [
            typed,
            _execute("para.set", {"props": {"style": f"Heading{level}"}}),
        ]
    if kind == "paragraph":
        return [{"tool": "type_text", "arguments": {"text": block["text"], "paragraphs": True}}]
    if kind == "table":
        values = [block["header"], *block["rows"]]
        return [{"tool": "insert_table", "arguments": {"values": values, "header": True}}]
    if kind == "list":
        step = {
            "tool": "type_text",
            "arguments": {"text": "\n".join(block["items"]), "paragraphs": True},
        }
        command = "para.numbering" if block["ordered"] else "para.bullets"
        return [step, _execute(command)]
    if kind == "figure":
        return [
            {
                "tool": "execute",
                "arguments": {
                    "command": "insert.picture",
                    "params": {"path": block["path"], "alt": block["alt"]},
                },
            }
        ]
    return [{"tool": "type_text", "arguments": {"text": block["text"], "paragraphs": True}}]


def build_blueprint(
    project_name: str,
    markdown_path: Path,
    markdown: str,
    assets: list[Path],
    csl_style: str,
    exports_dir: Path,
    output_stem: str,
    *,
    project_dir: Path | None = None,
) -> dict:
    """Turn Citeproc-rendered Markdown into blocks plus the WordCraft calls that write them."""
    root = project_dir or (exports_dir.parent if exports_dir.name == "exports" else exports_dir)
    blocks = parse_markdown_blocks(markdown, root)
    warnings = [
        f"Figure path does not exist: {block['path']}"
        for block in blocks
        if block["kind"] == "figure" and not block["exists"]
    ]

    steps: list[dict] = [
        {"tool": "new_document", "arguments": {"template": "blank"}},
        _execute("file.setAuthor", {"name": WORDCRAFT_AUTHOR}),
        *_style_steps(),
        _execute("references.citationStyle", {"style": citation_style_for(csl_style)}),
    ]
    for block in blocks:
        steps.extend(_block_steps(block))
    steps.extend(
        [
            _execute("file.strictReport"),
            {"tool": "render_page", "arguments": {"page": 1}},
            {
                "tool": "save_document",
                "arguments": {"path": str(exports_dir / f"{output_stem}.docx")},
            },
            _execute(
                "file.exportPdf",
                {"path": str(exports_dir / f"{output_stem}.pdf"), "strict": True},
            ),
        ]
    )

    return {
        "document_title": project_name,
        "markdown_path": str(markdown_path),
        "blocks": blocks,
        "bibliography": {"title": "References", "markdown": ""},
        "assets": [str(asset) for asset in assets],
        "tool_sequence": steps,
        "warnings": warnings,
    }
