"""Media processing core for Vision Terminal Kosmos.

This module holds all file manipulation logic (images and videos).
The UI layer in `cli_ui.py` only consumes these functions — keeping
presentation and logic decoupled.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import cv2

IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".gif"}


class ProcessorError(Exception):
    """Domain error raised by the processor (bad file, codec, etc.)."""


@dataclass
class MediaInfo:
    """Basic metadata extracted from a video file via OpenCV."""

    path: Path
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise ProcessorError(
            "ffmpeg not found on PATH. Install it with: sudo apt install ffmpeg"
        )


def _ensure_exists(path: Path) -> None:
    if not path.exists():
        raise ProcessorError(f"File not found: {path}")


def _classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    raise ProcessorError(f"Unsupported extension: {ext}")


def probe_video(path: Path) -> MediaInfo:
    """Read width, height, fps, and frame count via OpenCV."""
    _ensure_exists(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ProcessorError(f"Could not open video: {path}")
    try:
        return MediaInfo(
            path=path,
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)) or 0.0,
            frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def _run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg quietly, raising ProcessorError on failure."""
    _ensure_ffmpeg()
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProcessorError(f"ffmpeg failed: {proc.stderr.strip() or 'unknown error'}")


# ---------------------------------------------------------------------------
# CUT - video trimming
# ---------------------------------------------------------------------------
def cut_video(src: Path, dst: Path, start: str | None, end: str | None) -> Path:
    """Trim a video segment via ffmpeg (stream copy, no re-encode).

    `start` and `end` accept `HH:MM:SS`, `MM:SS`, or plain seconds. Pass
    `None` for either to omit the bound — `start=None` starts at 0,
    `end=None` runs to the natural end of the file.
    """
    _ensure_exists(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    if start is not None:
        args += ["-ss", start]
    if end is not None:
        args += ["-to", end]
    args += ["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)]
    _run_ffmpeg(args)
    return dst


def batch_cut(
    folder: Path,
    out_folder: Path,
    *,
    start: str | None,
    end: str | None,
    progress_cb: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Apply the same trim (`start` → `end`) to every video in `folder`.

    `start=None` means from the beginning of each file; `end=None` means
    until the natural end of each file.
    """
    files = list_media(folder, kinds=("video",))
    if not files:
        raise ProcessorError(f"No video files in: {folder}")
    out_folder.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for src in files:
        dst = out_folder / f"{src.stem}_cut{src.suffix}"
        cut_video(src, dst, start=start, end=end)
        results.append(dst)
        if progress_cb is not None:
            progress_cb(src)
    return results


# ---------------------------------------------------------------------------
# CONVERT - image / video conversion
# ---------------------------------------------------------------------------
def convert_image(src: Path, dst: Path, quality: int = 92) -> Path:
    """Convert an image between supported formats via OpenCV."""
    _ensure_exists(src)
    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ProcessorError(f"OpenCV could not read the image: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    ext = dst.suffix.lower()
    params: list[int] = []
    if ext in {".jpg", ".jpeg"}:
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif ext == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
    elif ext == ".png":
        # PNG compression is 0-9; map quality (0-100) to inverse compression.
        params = [cv2.IMWRITE_PNG_COMPRESSION, max(0, min(9, 9 - quality // 12))]

    if not cv2.imwrite(str(dst), img, params):
        raise ProcessorError(f"Failed to save {dst} (format or permission).")
    return dst


def convert_video(src: Path, dst: Path) -> Path:
    """Convert a video/gif between formats using sensible per-extension codecs."""
    _ensure_exists(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = dst.suffix.lower()

    if ext == ".gif":
        # Two-pass palette pipeline for decent GIF quality.
        palette = dst.with_suffix(".palette.png")
        _run_ffmpeg(["-i", str(src), "-vf", "fps=15,scale=480:-1:flags=lanczos,palettegen", str(palette)])
        try:
            _run_ffmpeg(
                [
                    "-i", str(src),
                    "-i", str(palette),
                    "-lavfi", "fps=15,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse",
                    str(dst),
                ]
            )
        finally:
            if palette.exists():
                palette.unlink()
        return dst

    args = ["-i", str(src)]
    if ext == ".mp4":
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    elif ext == ".mkv":
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-c:a", "aac", "-b:a", "160k"]
    elif ext == ".webm":
        args += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-c:a", "libopus"]
    else:
        raise ProcessorError(f"Unsupported output video format: {ext}")

    args.append(str(dst))
    _run_ffmpeg(args)
    return dst


def convert_any(src: Path, dst: Path, quality: int = 92) -> Path:
    """Dispatch to the right converter based on the source extension."""
    kind = _classify(src)
    if kind == "image":
        return convert_image(src, dst, quality=quality)
    return convert_video(src, dst)


# ---------------------------------------------------------------------------
# WA-FIX - WhatsApp optimization
# ---------------------------------------------------------------------------
def wa_fix(src: Path, dst: Path, max_height: int = 720, video_bitrate: str = "1500k") -> Path:
    """Re-encode a video for maximum WhatsApp compatibility.

    - H.264 (libx264) + yuv420p (required by WhatsApp players).
    - AAC 128k audio.
    - Scales to at most `max_height` rows keeping aspect ratio (even dims).
    - `+faststart` for progressive playback.
    """
    _ensure_exists(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Clamp height to `max_height`, preserve aspect ratio, enforce even dims.
    scale = f"scale=-2:'min({max_height},ih)':flags=lanczos"
    _run_ffmpeg(
        [
            "-i", str(src),
            "-vf", scale,
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-b:v", video_bitrate,
            "-maxrate", video_bitrate,
            "-bufsize", "3000k",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-movflags", "+faststart",
            str(dst),
        ]
    )
    return dst


# ---------------------------------------------------------------------------
# BATCH - folder-wide operations
# ---------------------------------------------------------------------------
def list_media(folder: Path, kinds: Iterable[str] = ("image", "video")) -> list[Path]:
    """List media files (images and/or videos) inside `folder`."""
    if not folder.is_dir():
        raise ProcessorError(f"Folder does not exist: {folder}")
    allowed: set[str] = set()
    if "image" in kinds:
        allowed |= IMAGE_EXTS
    if "video" in kinds:
        allowed |= VIDEO_EXTS
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in allowed)


def resize_image(src: Path, dst: Path, max_side: int) -> Path:
    """Resize an image keeping aspect ratio (longest side = `max_side`)."""
    _ensure_exists(src)
    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ProcessorError(f"OpenCV could not read the image: {src}")
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        img = cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst), img):
        raise ProcessorError(f"Failed to save {dst}.")
    return dst


def batch_apply(
    folder: Path,
    out_folder: Path,
    operation: str,
    *,
    target_ext: str | None = None,
    max_side: int | None = None,
    progress_cb: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Apply `operation` ("convert" | "resize") to every file in `folder`.

    - `convert` requires `target_ext` (e.g. ".webp", ".mp4").
    - `resize`  requires `max_side` (images only).
    """
    kinds: tuple[str, ...] = ("image",) if operation == "resize" else ("image", "video")
    files = list_media(folder, kinds=kinds)
    if not files:
        raise ProcessorError(f"No compatible media files in: {folder}")
    out_folder.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for src in files:
        if operation == "convert":
            if not target_ext:
                raise ProcessorError("target_ext is required for batch convert.")
            dst = out_folder / (src.stem + target_ext)
            convert_any(src, dst)
        elif operation == "resize":
            if max_side is None:
                raise ProcessorError("max_side is required for batch resize.")
            dst = out_folder / src.name
            resize_image(src, dst, max_side)
        else:
            raise ProcessorError(f"Unknown batch operation: {operation}")
        results.append(dst)
        if progress_cb is not None:
            progress_cb(src)
    return results
