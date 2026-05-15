"""Vision Terminal Kosmos CLI entry point (Typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__, cli_ui, processor
from .processor import ProcessorError

app = typer.Typer(
    name="vterm",
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="Vision Terminal Kosmos — fast image and video processing in the terminal.",
)


def _handle(err: ProcessorError) -> None:
    cli_ui.error(str(err))
    raise typer.Exit(code=1)


def _parse_crop_rect(s: str) -> tuple[int, int, int, int]:
    """Parse '--crop-rect X:Y:W:H' into four ints. CLI concern, not processor."""
    try:
        parts = [int(p) for p in s.split(":")]
        if len(parts) != 4:
            raise ValueError
        return parts[0], parts[1], parts[2], parts[3]
    except ValueError:
        raise typer.BadParameter("--crop-rect must be X:Y:W:H with integer values")


# ---------------------------------------------------------------------------
# Root callback: menu + --version + help with banner
# ---------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help="Show the version and exit.", is_eager=True
    ),
) -> None:
    if version:
        cli_ui.console.print(f"[bold cyan]Vision Terminal Kosmos[/] v{__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # Enter interactive menu loop; exits only when user picks quit.
        from . import menu
        menu.loop()


# ---------------------------------------------------------------------------
# cut
# ---------------------------------------------------------------------------
@app.command("cut", help="Trim a video segment — single file, or every video in a folder.")
def cmd_cut(
    src: Path = typer.Argument(..., exists=True, readable=True, help="Input video file or folder of videos."),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="Start time (HH:MM:SS, MM:SS, or seconds). Omit to start from the beginning."),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="End time (HH:MM:SS, MM:SS, or seconds). Omit to run until the end of the video."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output file (single) or folder (batch). Default: <name>_cut.<ext> / <folder>/_cut",
    ),
) -> None:
    try:
        if src.is_dir():
            out_dir = output or (src / "_cut")
            files = processor.list_media(src, kinds=("video",))
            if not files:
                cli_ui.error(f"No videos found in: {src}")
                raise typer.Exit(code=1)
            cli_ui.info(f"{len(files)} video(s) → {out_dir}")
            with cli_ui.make_progress() as progress:
                task = progress.add_task("Batch cut", total=len(files))

                def _tick(_: Path) -> None:
                    progress.advance(task)

                processor.batch_cut(src, out_dir, start=start, end=end, progress_cb=_tick)
            cli_ui.success(f"Batch cut complete in: [bold]{out_dir}[/]")
        else:
            dst = output or src.with_name(f"{src.stem}_cut{src.suffix}")
            with cli_ui.make_progress() as progress:
                task = progress.add_task(f"Cutting {src.name}", total=1)
                processor.cut_video(src, dst, start=start, end=end)
                progress.advance(task)
            cli_ui.success(f"Trim saved to: [bold]{dst}[/]")
    except ProcessorError as err:
        _handle(err)


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------
@app.command("convert", help="Convert image (PNG/JPG/WebP) or video (MP4/MKV/WebM/GIF).")
def cmd_convert(
    src: Path = typer.Argument(..., exists=True, readable=True, help="Input file."),
    dst: Path = typer.Argument(..., help="Output file (extension picks the format)."),
    quality: int = typer.Option(92, "--quality", "-q", min=1, max=100, help="Quality (images)."),
) -> None:
    try:
        with cli_ui.make_progress() as progress:
            task = progress.add_task(f"Converting {src.name} → {dst.suffix}", total=1)
            processor.convert_any(src, dst, quality=quality)
            progress.advance(task)
    except ProcessorError as err:
        _handle(err)
    cli_ui.success(f"Saved to: [bold]{dst}[/]")


# ---------------------------------------------------------------------------
# wa-fix
# ---------------------------------------------------------------------------
@app.command("wa-fix", help="Optimize video for WhatsApp (H.264 baseline, AAC, ≤720p).")
def cmd_wa_fix(
    src: Path = typer.Argument(..., exists=True, readable=True, help="Input video."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file. Default: <name>_wa.mp4"
    ),
    max_height: int = typer.Option(720, "--max-height", help="Maximum height in pixels."),
    bitrate: str = typer.Option("1500k", "--bitrate", "-b", help="Video bitrate (e.g. 1500k)."),
) -> None:
    dst = output or src.with_name(f"{src.stem}_wa.mp4")
    try:
        with cli_ui.make_progress() as progress:
            task = progress.add_task(f"WhatsApp-fix {src.name}", total=1)
            processor.wa_fix(src, dst, max_height=max_height, video_bitrate=bitrate)
            progress.advance(task)
    except ProcessorError as err:
        _handle(err)
    cli_ui.success(f"WhatsApp-ready: [bold]{dst}[/]")


# ---------------------------------------------------------------------------
# resample
# ---------------------------------------------------------------------------
@app.command("resample", help="Re-encode at a chosen FPS, optionally cropping a rectangle.")
def cmd_resample(
    src: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False, help="Input video file."),
    fps: Optional[float] = typer.Option(
        None, "--fps", "-f", help="New frame rate. Default: source video's FPS."
    ),
    crop: bool = typer.Option(
        False, "--crop", help="Open an OpenCV window to drag a crop rectangle."
    ),
    crop_rect: Optional[str] = typer.Option(
        None, "--crop-rect", help="Crop region as 'X:Y:W:H' (integers). Bypasses the GUI."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file. Default: <name>_resampled.mp4 (must end in .mp4)."
    ),
) -> None:
    if crop and crop_rect:
        cli_ui.error("Use exactly one of: --crop or --crop-rect.")
        raise typer.Exit(code=2)

    dst = output or src.with_name(f"{src.stem}_resampled.mp4")

    try:
        info = processor.probe_video(src)
        target_fps = fps if fps is not None else info.fps

        if fps is None and info.fps <= 0:
            cli_ui.error(
                "Could not determine source FPS - pass --fps explicitly."
            )
            raise typer.Exit(code=2)

        if not crop and not crop_rect and abs(target_fps - info.fps) < 1e-6:
            cli_ui.error("Nothing to do (same FPS, no crop).")
            raise typer.Exit(code=2)

        if crop:
            rect = processor.select_crop_rect(src)
        elif crop_rect:
            rect = _parse_crop_rect(crop_rect)
        else:
            rect = None

        with cli_ui.make_progress() as progress:
            task = progress.add_task(f"Resampling {src.name}", total=1)
            processor.resample_video(src, dst, fps=target_fps, crop=rect)
            progress.advance(task)
    except ProcessorError as err:
        _handle(err)
    cli_ui.success(f"Saved to: [bold]{dst}[/]")


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------
@app.command("batch", help="Apply conversion or resizing to every file in a folder.")
def cmd_batch(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, help="Input folder."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output folder. Default: <folder>/_out"
    ),
    to: Optional[str] = typer.Option(
        None, "--to", help="Output extension for conversion (e.g. .webp, .mp4)."
    ),
    resize: Optional[int] = typer.Option(
        None, "--resize", help="Resize images: longest side in pixels (e.g. 1280)."
    ),
) -> None:
    if (to is None) == (resize is None):
        cli_ui.error("Use exactly one of: --to <ext> or --resize <pixels>.")
        raise typer.Exit(code=2)

    out_dir = output or (folder / "_out")
    operation = "convert" if to else "resize"

    try:
        files = processor.list_media(
            folder,
            kinds=("image",) if operation == "resize" else ("image", "video"),
        )
    except ProcessorError as err:
        _handle(err)
        return

    if not files:
        cli_ui.error(f"No media files found in: {folder}")
        raise typer.Exit(code=1)

    cli_ui.info(f"{len(files)} file(s) → {out_dir}")
    try:
        with cli_ui.make_progress() as progress:
            task = progress.add_task(f"Batch {operation}", total=len(files))

            def _tick(_: Path) -> None:
                progress.advance(task)

            processor.batch_apply(
                folder,
                out_dir,
                operation=operation,
                target_ext=to,
                max_side=resize,
                progress_cb=_tick,
            )
    except ProcessorError as err:
        _handle(err)
    cli_ui.success(f"Batch complete in: [bold]{out_dir}[/]")


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------
@app.command("info", help="Show metadata for a video file.")
def cmd_info(
    src: Path = typer.Argument(..., exists=True, readable=True, help="Video to inspect."),
) -> None:
    try:
        m = processor.probe_video(src)
    except ProcessorError as err:
        _handle(err)
        return
    cli_ui.console.print(
        cli_ui.media_info_panel(m.width, m.height, m.fps, m.duration_seconds, m.path)
    )


if __name__ == "__main__":
    app()
