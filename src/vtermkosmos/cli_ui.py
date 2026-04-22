"""Visual components (Rich) for Vision Terminal Kosmos.

Keeping UI separate from logic means the `processor` is testable without
a terminal, and `main.py` reads like a thin routing table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pyfiglet
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

console = Console()

BRAND_COLOR = "bright_cyan"
ACCENT_COLOR = "magenta"

BANNER_ART = pyfiglet.figlet_format("Kosmos", font="slant").rstrip("\n")
BANNER_SUBTITLE = "Vision Terminal · Kosmos — image & video, organized."


# ---------------------------------------------------------------------------
# Banner and panels
# ---------------------------------------------------------------------------
def render_banner() -> Panel:
    """ASCII-art banner shown in --help and in the main menu."""
    subtitle = Text(BANNER_SUBTITLE, style="italic white")
    body = Group(
        Align.center(Text(BANNER_ART, style=f"bold {BRAND_COLOR}")),
        Align.center(subtitle),
    )
    return Panel(body, border_style=ACCENT_COLOR, padding=(0, 2))


def print_banner() -> None:
    console.print(render_banner())


def commands_table() -> Table:
    """Table listing the available commands in the main menu."""
    table = Table(
        title="Available commands",
        title_style=f"bold {BRAND_COLOR}",
        border_style=ACCENT_COLOR,
        header_style="bold white",
        expand=True,
    )
    table.add_column("Key", style=f"bold {ACCENT_COLOR}", no_wrap=True, justify="center")
    table.add_column("Command", style="bold green", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Example", style="dim")

    table.add_row(
        "1", "cut",
        "Trim a video segment (no re-encode).",
        "vterm cut in.mp4 -s 00:10 -e 00:30",
    )
    table.add_row(
        "2", "convert",
        "Convert between formats (image/video).",
        "vterm convert photo.png photo.webp",
    )
    table.add_row(
        "3", "wa-fix",
        "Optimize a video for WhatsApp (H.264/AAC, ≤720p).",
        "vterm wa-fix video.mp4",
    )
    table.add_row(
        "4", "batch",
        "Apply convert or resize to a whole folder.",
        "vterm batch ./photos --resize 1280",
    )
    table.add_row(
        "5", "info",
        "Show metadata for a video file.",
        "vterm info video.mp4",
    )
    table.add_row("q", "quit", "Leave the interactive menu.", "—")
    return table


def render_menu() -> None:
    """Main menu shown when `vterm` runs with no arguments."""
    console.print(render_banner())
    console.print(commands_table())
    tip = Text.assemble(
        ("Tip: ", f"bold {BRAND_COLOR}"),
        ("pick a key below, or run ", "white"),
        ("vterm <command> --help", "bold green"),
        (" for non-interactive usage.", "white"),
    )
    console.print(Panel(tip, border_style=ACCENT_COLOR))


# ---------------------------------------------------------------------------
# Utility messages
# ---------------------------------------------------------------------------
def error(msg: str) -> None:
    console.print(Panel(Text(msg, style="bold white"), title="[bold red]Error[/]", border_style="red"))


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/] {msg}")


def info(msg: str) -> None:
    console.print(f"[bold {BRAND_COLOR}]ℹ[/] {msg}")


def media_info_panel(width: int, height: int, fps: float, duration: float, path: Path) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style=f"bold {BRAND_COLOR}")
    table.add_column(style="white")
    table.add_row("File", str(path))
    table.add_row("Resolution", f"{width} x {height}")
    table.add_row("FPS", f"{fps:.2f}")
    table.add_row("Duration", f"{duration:.2f} s")
    return Panel(table, title="[bold]Metadata[/]", border_style=ACCENT_COLOR)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def make_progress() -> Progress:
    """Standard progress bar for long-running tasks."""
    return Progress(
        SpinnerColumn(style=BRAND_COLOR),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None, complete_style=BRAND_COLOR, finished_style="green"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def run_with_progress(description: str, items: Iterable[Path], worker) -> list[Path]:
    """Call `worker(item)` for each item while showing a progress bar."""
    items_list = list(items)
    results: list[Path] = []
    with make_progress() as progress:
        task = progress.add_task(description, total=len(items_list))
        for item in items_list:
            results.append(worker(item))
            progress.advance(task)
    return results
