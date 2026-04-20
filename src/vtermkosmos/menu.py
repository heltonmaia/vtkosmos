"""Interactive menu loop for Vision Terminal Kosmos.

Flow: ask for a path first (file or folder), then show the operations that
are valid for that target. Dispatches to `processor`, handles errors via
`cli_ui`, and loops until the user quits.
"""

from __future__ import annotations

import glob
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import cli_ui, processor
from .processor import IMAGE_EXTS, VIDEO_EXTS, ProcessorError

try:
    import readline  # type: ignore[import-not-found]
    _HAS_READLINE = True
except ImportError:  # pragma: no cover - Windows without pyreadline
    _HAS_READLINE = False


# ---------------------------------------------------------------------------
# TAB completion for filesystem paths (readline)
# ---------------------------------------------------------------------------
def _path_completer(text: str, state: int) -> str | None:
    """readline completer that expands filesystem paths like a shell."""
    expanded = os.path.expanduser(text) if text else ""
    matches = sorted(glob.glob(expanded + "*"))
    results = [m + "/" if os.path.isdir(m) else m for m in matches]

    if text.startswith("~"):
        home = os.path.expanduser("~")
        results = [
            ("~" + r[len(home):]) if r.startswith(home) else r
            for r in results
        ]
    try:
        return results[state]
    except IndexError:
        return None


@contextmanager
def _path_completion() -> Iterator[None]:
    """Temporarily enable TAB path completion for the current prompt."""
    if not _HAS_READLINE:
        yield
        return
    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(_path_completer)
    readline.set_completer_delims(" \t\n;")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    try:
        yield
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def _ask_target() -> Path | None:
    """Prompt for the file or folder to work on. Returns None if user quits."""
    while True:
        with _path_completion():
            raw = Prompt.ask(
                f"[bold {cli_ui.BRAND_COLOR}]Path to video, image, or folder[/] "
                "[dim](or 'q' to quit)[/]"
            )
        raw = raw.strip().strip('"').strip("'")
        if raw.lower() == "q":
            return None
        if not raw:
            cli_ui.error("Path cannot be empty.")
            continue
        path = Path(raw).expanduser()
        if not path.exists():
            cli_ui.error(f"Path not found: {path}")
            continue
        return path


def _ask_out_path(label: str, default: Path) -> Path:
    with _path_completion():
        raw = Prompt.ask(
            f"[bold {cli_ui.BRAND_COLOR}]{label}[/]", default=str(default)
        )
    return Path(raw.strip().strip('"').strip("'")).expanduser()


def _ask_int(label: str, default: int) -> int:
    while True:
        raw = Prompt.ask(f"[bold {cli_ui.BRAND_COLOR}]{label}[/]", default=str(default))
        try:
            return int(raw)
        except ValueError:
            cli_ui.error("Please enter an integer.")


def _ask_time(label: str) -> str | None:
    """Prompt for a time; empty input returns None (= no bound)."""
    raw = Prompt.ask(f"[bold {cli_ui.BRAND_COLOR}]{label}[/]").strip()
    return raw or None


def _file_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return None


# ---------------------------------------------------------------------------
# Flows (each receives the already-chosen target)
# ---------------------------------------------------------------------------
def _flow_cut(src: Path) -> None:
    start = _ask_time("Start time (HH:MM:SS, MM:SS, or seconds; empty = from beginning)")
    end = _ask_time("End time (HH:MM:SS, MM:SS, or seconds; empty = until end of video)")
    dst = _ask_out_path("Output file", src.with_name(f"{src.stem}_cut{src.suffix}"))
    with cli_ui.make_progress() as progress:
        task = progress.add_task(f"Cutting {src.name}", total=1)
        processor.cut_video(src, dst, start=start, end=end)
        progress.advance(task)
    cli_ui.success(f"Trim saved to: [bold]{dst}[/]")


def _flow_convert_file(src: Path) -> None:
    default_ext = ".webp" if _file_kind(src) == "image" else ".mp4"
    dst = _ask_out_path(
        "Output file (extension picks the format)", src.with_suffix(default_ext)
    )
    quality = _ask_int("Quality (1-100, images only)", default=92)
    with cli_ui.make_progress() as progress:
        task = progress.add_task(f"Converting {src.name} → {dst.suffix}", total=1)
        processor.convert_any(src, dst, quality=quality)
        progress.advance(task)
    cli_ui.success(f"Saved to: [bold]{dst}[/]")


def _flow_wa_fix(src: Path) -> None:
    dst = _ask_out_path("Output file", src.with_name(f"{src.stem}_wa.mp4"))
    max_h = _ask_int("Max height (px)", default=720)
    bitrate = Prompt.ask(f"[bold {cli_ui.BRAND_COLOR}]Video bitrate[/]", default="1500k")
    with cli_ui.make_progress() as progress:
        task = progress.add_task(f"WhatsApp-fix {src.name}", total=1)
        processor.wa_fix(src, dst, max_height=max_h, video_bitrate=bitrate)
        progress.advance(task)
    cli_ui.success(f"WhatsApp-ready: [bold]{dst}[/]")


def _flow_info(src: Path) -> None:
    m = processor.probe_video(src)
    cli_ui.console.print(
        cli_ui.media_info_panel(m.width, m.height, m.fps, m.duration_seconds, m.path)
    )


def _flow_batch_cut(folder: Path) -> None:
    start = _ask_time("Start time (HH:MM:SS, MM:SS, or seconds; empty = from beginning)")
    end = _ask_time("End time (HH:MM:SS, MM:SS, or seconds; empty = until end of each video)")
    out_dir = _ask_out_path("Output folder", folder / "_cut")
    files = processor.list_media(folder, kinds=("video",))
    if not files:
        cli_ui.error(f"No videos found in: {folder}")
        return
    cli_ui.info(f"{len(files)} video(s) → {out_dir}")
    with cli_ui.make_progress() as progress:
        task = progress.add_task("Batch cut", total=len(files))

        def _tick(_: Path) -> None:
            progress.advance(task)

        processor.batch_cut(folder, out_dir, start=start, end=end, progress_cb=_tick)
    cli_ui.success(f"Batch cut complete in: [bold]{out_dir}[/]")


def _flow_batch_convert(folder: Path) -> None:
    ext = Prompt.ask(f"[bold {cli_ui.BRAND_COLOR}]Target extension[/] (e.g. .webp, .mp4)")
    target_ext = ext if ext.startswith(".") else f".{ext}"
    out_dir = _ask_out_path("Output folder", folder / "_out")
    files = processor.list_media(folder, kinds=("image", "video"))
    if not files:
        cli_ui.error(f"No media files found in: {folder}")
        return
    cli_ui.info(f"{len(files)} file(s) → {out_dir}")
    with cli_ui.make_progress() as progress:
        task = progress.add_task("Batch convert", total=len(files))

        def _tick(_: Path) -> None:
            progress.advance(task)

        processor.batch_apply(
            folder, out_dir, operation="convert", target_ext=target_ext, progress_cb=_tick
        )
    cli_ui.success(f"Batch complete in: [bold]{out_dir}[/]")


def _flow_batch_resize(folder: Path) -> None:
    max_side = _ask_int("Longest side (px)", default=1280)
    out_dir = _ask_out_path("Output folder", folder / "_out")
    files = processor.list_media(folder, kinds=("image",))
    if not files:
        cli_ui.error(f"No images found in: {folder}")
        return
    cli_ui.info(f"{len(files)} image(s) → {out_dir}")
    with cli_ui.make_progress() as progress:
        task = progress.add_task("Batch resize", total=len(files))

        def _tick(_: Path) -> None:
            progress.advance(task)

        processor.batch_apply(
            folder, out_dir, operation="resize", max_side=max_side, progress_cb=_tick
        )
    cli_ui.success(f"Batch complete in: [bold]{out_dir}[/]")


# ---------------------------------------------------------------------------
# Operation tables (shown after a target is chosen)
# ---------------------------------------------------------------------------
def _ops_table(title: str, rows: list[tuple[str, str, str]]) -> Table:
    t = Table(
        title=title,
        title_style=f"bold {cli_ui.BRAND_COLOR}",
        border_style=cli_ui.ACCENT_COLOR,
        header_style="bold white",
        expand=True,
    )
    t.add_column("Key", style=f"bold {cli_ui.ACCENT_COLOR}", no_wrap=True, justify="center")
    t.add_column("Command", style="bold green", no_wrap=True)
    t.add_column("Description", style="white")
    for row in rows:
        t.add_row(*row)
    t.add_row("q", "quit", "Leave the menu.")
    return t


_VIDEO_MENU: dict[str, tuple[str, str, Callable[[Path], None]]] = {
    "1": ("cut",     "Trim a segment (no re-encode).",              _flow_cut),
    "2": ("convert", "Convert to another format.",                  _flow_convert_file),
    "3": ("wa-fix",  "Optimize for WhatsApp (H.264/AAC, ≤720p).",   _flow_wa_fix),
    "4": ("info",    "Show metadata (resolution, fps, duration).",  _flow_info),
}

_IMAGE_MENU: dict[str, tuple[str, str, Callable[[Path], None]]] = {
    "1": ("convert", "Convert to another format.", _flow_convert_file),
}

_FOLDER_MENU: dict[str, tuple[str, str, Callable[[Path], None]]] = {
    "1": ("batch cut",     "Same trim applied to every video.",         _flow_batch_cut),
    "2": ("batch convert", "Convert every file to another format.",     _flow_batch_convert),
    "3": ("batch resize",  "Resize every image (longest side, px).",    _flow_batch_resize),
}


def _menu_for(target: Path) -> tuple[str, dict[str, tuple[str, str, Callable[[Path], None]]]] | None:
    if target.is_dir():
        return "Operations for this folder", _FOLDER_MENU
    kind = _file_kind(target)
    if kind == "video":
        return "Operations for this video", _VIDEO_MENU
    if kind == "image":
        return "Operations for this image", _IMAGE_MENU
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def loop() -> None:
    """Ask for a target, show the matching operations, dispatch, repeat."""
    first = True
    while True:
        if first:
            first = False
        else:
            cli_ui.console.rule(style=cli_ui.ACCENT_COLOR)
        cli_ui.print_banner()

        target = _ask_target()
        if target is None:
            cli_ui.console.print(f"[bold {cli_ui.BRAND_COLOR}]Goodbye![/]")
            return

        menu = _menu_for(target)
        if menu is None:
            cli_ui.error(f"Unsupported file extension: {target.suffix}")
            if not Confirm.ask(f"\n[bold {cli_ui.BRAND_COLOR}]Choose another target?[/]", default=True):
                return
            continue

        title, actions = menu
        rows = [(key, name, desc) for key, (name, desc, _fn) in actions.items()]
        cli_ui.console.print(_ops_table(title, rows))
        choices = [*actions.keys(), "q"]
        choice = Prompt.ask(
            f"\n[bold {cli_ui.ACCENT_COLOR}]Choose an option[/]",
            choices=choices,
            default="q",
            show_choices=True,
        )
        if choice == "q":
            cli_ui.console.print(f"[bold {cli_ui.BRAND_COLOR}]Goodbye![/]")
            return

        _name, _desc, fn = actions[choice]
        try:
            fn(target)
        except ProcessorError as err:
            cli_ui.error(str(err))
        except KeyboardInterrupt:
            cli_ui.console.print("\n[yellow]Cancelled.[/]")

        if not Confirm.ask(
            f"\n[bold {cli_ui.BRAND_COLOR}]Back to menu?[/]", default=True
        ):
            cli_ui.console.print(f"[bold {cli_ui.BRAND_COLOR}]Goodbye![/]")
            return
