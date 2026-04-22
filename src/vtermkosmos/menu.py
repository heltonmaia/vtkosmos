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

from prompt_toolkit import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, ScrollOffsets, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich.prompt import Confirm, Prompt

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
# Keyboard-driven filesystem browser (prompt_toolkit)
# ---------------------------------------------------------------------------
class _BrowseOutcome:
    __slots__ = ("path", "text_mode", "quit")

    def __init__(self) -> None:
        self.path: Path | None = None
        self.text_mode: bool = False
        self.quit: bool = False


def _list_dir(p: Path) -> list[Path]:
    try:
        entries = [e for e in p.iterdir() if not e.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
    return entries


_BROWSER_STYLE = Style.from_dict({
    "banner": "bold ansibrightcyan",
    "subtitle": "italic",
    "rule": "ansimagenta",
    "header": "bold ansibrightcyan",
    "dir": "bold ansicyan",
    "virtual": "italic ansimagenta",
    "help": "ansigray",
    "cursor": "reverse bold",
})

_BANNER_LINES = cli_ui.BANNER_ART.split("\n")
_BANNER_HEIGHT = len(_BANNER_LINES) + 1  # art + subtitle line


def _browse_filesystem(start: Path) -> _BrowseOutcome:
    """Full-screen directory browser. Returns when the user selects or exits."""
    from prompt_toolkit.application import get_app

    outcome = _BrowseOutcome()
    state = {"cwd": start.resolve(), "idx": 0}

    def _virtuals() -> list[tuple[str, str, Path | None]]:
        items: list[tuple[str, str, Path | None]] = [("use", "[use this folder]", None)]
        cwd = state["cwd"]
        if cwd.parent != cwd:
            items.append(("parent", ".. (parent)", cwd.parent))
        return items

    def _total() -> int:
        return len(_virtuals()) + len(_list_dir(state["cwd"]))

    def _selection() -> tuple[str, Path | None]:
        virtuals = _virtuals()
        i = state["idx"]
        if i < len(virtuals):
            kind, _label, p = virtuals[i]
            return kind, p
        j = i - len(virtuals)
        entries = _list_dir(state["cwd"])
        if 0 <= j < len(entries):
            return "entry", entries[j]
        return "none", None

    def _centered(text: str, style: str) -> tuple[str, str]:
        try:
            width = get_app().output.get_size().columns
        except Exception:
            width = 80
        pad = max(0, (width - len(text)) // 2)
        return (style, " " * pad + text + "\n")

    def render_banner() -> list[tuple[str, str]]:
        rows = [_centered(line, "class:banner") for line in _BANNER_LINES]
        rows.append(_centered(cli_ui.BANNER_SUBTITLE, "class:subtitle"))
        return rows

    def render_path() -> list[tuple[str, str]]:
        return [("class:header", f" {state['cwd']}\n")]

    def render_body() -> list[tuple[str, str]]:
        entries = _list_dir(state["cwd"])
        virtuals = _virtuals()
        total = len(virtuals) + len(entries)
        if total == 0:
            state["idx"] = 0
        elif state["idx"] >= total:
            state["idx"] = total - 1

        rows: list[tuple[str, str]] = []
        for i, (_kind, label, _p) in enumerate(virtuals):
            marker = "▶ " if i == state["idx"] else "  "
            style = "class:cursor" if i == state["idx"] else "class:virtual"
            rows.append((style, f"{marker}{label}\n"))
        for j, e in enumerate(entries):
            i = len(virtuals) + j
            marker = "▶ " if i == state["idx"] else "  "
            suffix = "/" if e.is_dir() else ""
            if i == state["idx"]:
                style = "class:cursor"
            elif e.is_dir():
                style = "class:dir"
            else:
                style = ""
            rows.append((style, f"{marker}{e.name}{suffix}\n"))
        if not rows:
            rows.append(("", "  (empty)\n"))
        return rows

    def render_footer() -> list[tuple[str, str]]:
        return [(
            "class:help",
            " ↑/↓ move   ↵ open/select   ← parent   →/space descend   / type path   q quit",
        )]

    def cursor_pos() -> Point:
        return Point(x=0, y=state["idx"])

    kb = KeyBindings()

    @kb.add("up")
    def _(event):  # noqa: ANN001
        state["idx"] = max(0, state["idx"] - 1)

    @kb.add("down")
    def _(event):  # noqa: ANN001
        total = _total()
        if total:
            state["idx"] = min(total - 1, state["idx"] + 1)

    @kb.add("home")
    def _(event):  # noqa: ANN001
        state["idx"] = 0

    @kb.add("end")
    def _(event):  # noqa: ANN001
        total = _total()
        state["idx"] = max(0, total - 1)

    @kb.add("pageup")
    def _(event):  # noqa: ANN001
        state["idx"] = max(0, state["idx"] - 10)

    @kb.add("pagedown")
    def _(event):  # noqa: ANN001
        total = _total()
        if total:
            state["idx"] = min(total - 1, state["idx"] + 10)

    def _descend_or_select(event) -> None:  # noqa: ANN001
        kind, p = _selection()
        if kind == "use":
            outcome.path = state["cwd"]
            event.app.exit()
        elif kind == "parent" and p is not None:
            state["cwd"] = p
            state["idx"] = 0
        elif kind == "entry" and p is not None:
            if p.is_dir():
                state["cwd"] = p
                state["idx"] = 0
            else:
                outcome.path = p
                event.app.exit()

    kb.add("enter")(_descend_or_select)
    kb.add("right")(_descend_or_select)
    kb.add("space")(_descend_or_select)

    @kb.add("left")
    @kb.add("backspace")
    def _(event):  # noqa: ANN001
        parent = state["cwd"].parent
        if parent != state["cwd"]:
            state["cwd"] = parent
            state["idx"] = 0

    @kb.add("q")
    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):  # noqa: ANN001
        outcome.quit = True
        event.app.exit()

    @kb.add("/")
    def _(event):  # noqa: ANN001
        outcome.text_mode = True
        event.app.exit()

    banner_window = Window(
        content=FormattedTextControl(text=render_banner),
        height=_BANNER_HEIGHT,
    )
    path_window = Window(
        content=FormattedTextControl(text=render_path),
        height=1,
    )
    body_window = Window(
        content=FormattedTextControl(
            text=render_body,
            focusable=True,
            show_cursor=False,
            get_cursor_position=cursor_pos,
        ),
        scroll_offsets=ScrollOffsets(top=2, bottom=2),
    )
    footer_window = Window(
        content=FormattedTextControl(text=render_footer),
        height=1,
    )

    layout = Layout(HSplit([
        banner_window,
        Window(char="─", height=1, style="class:rule"),
        path_window,
        body_window,
        Window(char="─", height=1, style="class:rule"),
        footer_window,
    ]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        style=_BROWSER_STYLE,
    )
    app.run()
    return outcome


# ---------------------------------------------------------------------------
# Keyboard-driven action picker (prompt_toolkit)
# ---------------------------------------------------------------------------
_PICKER_STYLE = Style.from_dict({
    "title": "bold ansibrightcyan",
    "rule": "ansimagenta",
    "key": "bold ansimagenta",
    "name": "bold ansigreen",
    "desc": "",
    "cursor": "reverse bold",
    "help": "ansigray",
})


def _pick_action(
    title: str,
    actions: dict[str, tuple[str, str, Callable[[Path], None]]],
) -> str | None:
    """Arrow-key picker. Returns the chosen key from `actions`, or None to cancel."""
    rows_data: list[tuple[str, str, str]] = [
        (k, name, desc) for k, (name, desc, _fn) in actions.items()
    ]
    rows_data.append(("q", "quit", "Leave the menu."))

    state = {"idx": 0}
    chosen: dict[str, str | None] = {"key": None}
    name_width = max((len(n) for _k, n, _d in rows_data), default=8)

    def render_title() -> list[tuple[str, str]]:
        return [("class:title", f" {title}\n")]

    def render_body() -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for i, (k, name, desc) in enumerate(rows_data):
            marker = "▶ " if i == state["idx"] else "  "
            if i == state["idx"]:
                rows.append((
                    "class:cursor",
                    f"{marker}[{k}]  {name:<{name_width}}   {desc}\n",
                ))
            else:
                rows.append(("", f"{marker}["))
                rows.append(("class:key", k))
                rows.append(("", "]  "))
                rows.append(("class:name", f"{name:<{name_width}}"))
                rows.append(("", f"   {desc}\n"))
        return rows

    def render_footer() -> list[tuple[str, str]]:
        return [(
            "class:help",
            " ↑/↓ move   ↵ select   1-9 shortcut   q cancel",
        )]

    def cursor_pos() -> Point:
        return Point(x=0, y=state["idx"])

    kb = KeyBindings()

    @kb.add("up")
    def _(event):  # noqa: ANN001
        state["idx"] = max(0, state["idx"] - 1)

    @kb.add("down")
    def _(event):  # noqa: ANN001
        state["idx"] = min(len(rows_data) - 1, state["idx"] + 1)

    @kb.add("home")
    def _(event):  # noqa: ANN001
        state["idx"] = 0

    @kb.add("end")
    def _(event):  # noqa: ANN001
        state["idx"] = len(rows_data) - 1

    @kb.add("enter")
    def _(event):  # noqa: ANN001
        k, _name, _desc = rows_data[state["idx"]]
        chosen["key"] = k
        event.app.exit()

    @kb.add("q")
    @kb.add("c-c")
    @kb.add("c-d")
    @kb.add("escape", eager=True)
    def _(event):  # noqa: ANN001
        chosen["key"] = "q"
        event.app.exit()

    # Digit shortcuts: press "1" to pick the first action, etc.
    for i, (k, _n, _d) in enumerate(rows_data):
        if k.isdigit() and len(k) == 1:
            def _make(key: str):
                def _h(event):  # noqa: ANN001
                    chosen["key"] = key
                    event.app.exit()
                return _h
            kb.add(k)(_make(k))

    title_window = Window(
        content=FormattedTextControl(text=render_title),
        height=1,
    )
    body_window = Window(
        content=FormattedTextControl(
            text=render_body,
            focusable=True,
            show_cursor=False,
            get_cursor_position=cursor_pos,
        ),
        scroll_offsets=ScrollOffsets(top=1, bottom=1),
    )
    footer_window = Window(
        content=FormattedTextControl(text=render_footer),
        height=1,
    )

    layout = Layout(HSplit([
        title_window,
        Window(char="─", height=1, style="class:rule"),
        body_window,
        Window(char="─", height=1, style="class:rule"),
        footer_window,
    ]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        style=_PICKER_STYLE,
    )
    app.run()
    return chosen["key"]


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def _ask_target() -> Path | None:
    """Prompt for the file or folder to work on. Returns None if user quits.

    Opens a keyboard-driven filesystem browser first. Pressing `/` inside the
    browser drops to a text prompt with TAB completion (the original behavior).
    """
    while True:
        outcome = _browse_filesystem(Path.cwd())
        if outcome.quit:
            return None
        if outcome.path is not None:
            return outcome.path
        if not outcome.text_mode:
            continue

        # Text-mode fallback — preserves original prompt behavior.
        while True:
            with _path_completion():
                raw = Prompt.ask(
                    f"[bold {cli_ui.BRAND_COLOR}]Path to video, image, or folder[/] "
                    "[dim](empty = back to browser, 'q' to quit)[/]"
                )
            raw = raw.strip().strip('"').strip("'")
            if raw.lower() == "q":
                return None
            if not raw:
                break  # back to browser
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
        choice = _pick_action(title, actions)
        if choice is None or choice == "q":
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
