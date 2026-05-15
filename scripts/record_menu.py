"""Render the Vision Terminal Kosmos action picker to an SVG for documentation.

Reproduces the keyboard-driven `_pick_action` view for a video target, with the
cursor sitting on `resample`. Run with:

    uv run --no-project --with rich --with pyfiglet python scripts/record_menu.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console
from rich.text import Text

from vtermkosmos import cli_ui


_ACTIONS: list[tuple[str, str, str]] = [
    ("1", "cut",      "Trim a segment (no re-encode)."),
    ("2", "convert",  "Convert to another format."),
    ("3", "wa-fix",   "Optimize for WhatsApp (H.264/AAC, ≤720p)."),
    ("4", "resample", "Re-encode at a chosen FPS, optionally crop."),
    ("5", "info",     "Show metadata (resolution, fps, duration)."),
    ("q", "quit",     "Leave the menu."),
]
_CURSOR_INDEX = 3  # resample


def main() -> None:
    console = Console(
        record=True,
        width=100,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
    )

    name_width = max(len(name) for _k, name, _d in _ACTIONS)

    console.print(Text(" Operations for this video", style=f"bold {cli_ui.BRAND_COLOR}"))
    console.rule(style=cli_ui.ACCENT_COLOR)

    for i, (k, name, desc) in enumerate(_ACTIONS):
        if i == _CURSOR_INDEX:
            row = Text(f"▶ [{k}]  {name:<{name_width}}   {desc}", style="reverse bold")
        else:
            row = Text("  [")
            row.append(k, style=f"bold {cli_ui.ACCENT_COLOR}")
            row.append("]  ")
            row.append(f"{name:<{name_width}}", style="bold green")
            row.append(f"   {desc}")
        console.print(row)

    console.rule(style=cli_ui.ACCENT_COLOR)
    console.print(
        Text(
            " ↑/↓ move   ↵ select   1-9 shortcut   q cancel",
            style="dim",
        )
    )

    out = ROOT / "assets" / "menu.svg"
    out.parent.mkdir(exist_ok=True)
    console.save_svg(str(out), title="Vision Terminal Kosmos")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
