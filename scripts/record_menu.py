"""Render the Vision Terminal Kosmos main menu to an SVG for documentation.

Run with:
    uv run --no-project --with rich --with pyfiglet python scripts/record_menu.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console

from vtermkosmos import cli_ui


def main() -> None:
    console = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    # Point the UI module at our recording console.
    cli_ui.console = console
    cli_ui.render_menu()
    # Mimic the prompt line so the screenshot shows the full UX.
    console.print(
        f"\n[bold {cli_ui.ACCENT_COLOR}]Choose an option[/] "
        "[dim](1=cut, 2=convert, 3=wa-fix, 4=batch, 5=info, q=quit)[/]: "
    )

    out = ROOT / "assets" / "menu.svg"
    out.parent.mkdir(exist_ok=True)
    console.save_svg(str(out), title="Vision Terminal Kosmos")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
