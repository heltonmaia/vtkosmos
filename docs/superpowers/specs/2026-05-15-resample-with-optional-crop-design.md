# Resample with optional crop — design

**Date:** 2026-05-15
**Status:** Approved (design); awaiting implementation plan
**Scope:** Add a single new operation to vtkosmos that re-encodes a video at a chosen frame rate, optionally cropping a rectangular region selected interactively in an OpenCV window.

---

## Goal

A new `resample` operation, available both as a Typer subcommand (`vterm resample`) and as a video-target action in the interactive menu, that:

- Always re-encodes the video at a chosen FPS (default = source FPS).
- Optionally crops a rectangular region. The region is chosen by dragging a rectangle on the first frame in an OpenCV GUI window, or passed as `X:Y:W:H` for non-interactive use.
- Always writes a new `.mp4` file (H.264 video, original audio stream-copied).

This combines two needs (frame-rate change and crop) into one operation. The "no crop" path is just resample.

## Non-goals

- Output containers other than `.mp4`.
- Configurable CRF / bitrate.
- Re-encoded audio.
- Non-rectangular crops (round, mask).
- Live video preview during selection — only the first frame is shown.
- Batch resample / batch crop across a folder.
- Crop on images.
- Trimming start/end as part of the same operation. Use `cut` first if needed.

## Architecture

Three layers, matching the existing project convention (see `CLAUDE.md`).

### `src/vtermkosmos/processor.py`

Two new pure functions. Both raise `ProcessorError` on every domain failure.

#### `select_crop_rect(src: Path) -> tuple[int, int, int, int]`

Reads the first frame with OpenCV, opens `cv2.selectROI` in a GUI window, returns `(x, y, w, h)`.

```python
def select_crop_rect(src: Path) -> tuple[int, int, int, int]:
    _ensure_exists(src)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise ProcessorError(f"Could not open video: {src}")
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        raise ProcessorError(f"Could not read first frame: {src}")

    try:
        x, y, w, h = cv2.selectROI(
            "Drag a rectangle — ENTER to confirm, ESC to cancel",
            frame,
            showCrosshair=True,
            fromCenter=False,
        )
    except cv2.error as e:
        raise ProcessorError(f"GUI not available: {e}") from e
    finally:
        cv2.destroyAllWindows()

    if w == 0 or h == 0:
        raise ProcessorError("Crop cancelled — no region selected.")
    return int(x), int(y), int(w), int(h)
```

Putting the GUI primitive in `processor.py` is a deliberate exception to "no UI in processor": it uses OpenCV (already a processor dep), returns pure data (four ints), and never imports Rich/Typer. The trade-off keeps `main.py` and `menu.py` agnostic to OpenCV.

#### `resample_video(src, dst, fps, crop=None) -> Path`

Re-encodes the video at the requested FPS, optionally cropping first.

Signature:

```python
def resample_video(
    src: Path,
    dst: Path,
    fps: float,
    crop: tuple[int, int, int, int] | None = None,
) -> Path
```

Validations performed before invoking ffmpeg:

- `fps > 0` → `ProcessorError("FPS must be positive")`
- `dst.suffix.lower() == ".mp4"` → otherwise `ProcessorError("Resample output must be .mp4")`
- If `crop` is set: `w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= source_width and y + h <= source_height` → otherwise `ProcessorError("Crop rectangle out of bounds: ...")`. Source dimensions come from `probe_video(src)`.

ffmpeg invocation (built and run via `_run_ffmpeg`):

```
ffmpeg -y -hide_banner -loglevel error -i SRC \
  -vf "<filter>" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -movflags +faststart \
  -c:a copy \
  DST
```

Where `<filter>` is:

- `fps=N` when `crop is None`
- `crop=W:H:X:Y,fps=N` when `crop` is set (crop first, then resample the smaller frame — cheaper).

Notes:

- `-c:a copy` avoids re-encoding audio. If the source audio is incompatible with the mp4 container, ffmpeg fails and `_run_ffmpeg` propagates the stderr message verbatim.
- `CRF 20` is hardcoded. Add `--quality` later if it becomes a recurring request.
- `yuv420p` ensures playback compatibility.
- `+faststart` matches the project's existing pattern (`wa_fix`, `cut_video`).

### `src/vtermkosmos/main.py`

A new `cmd_resample` Typer command, plus a tiny `_parse_crop_rect` helper local to this file (it parses user input — a CLI concern, not a processor concern).

CLI signature:

```
vterm resample <src> [--fps N] [--crop] [--crop-rect X:Y:W:H] [-o OUTPUT]
```

| Flag                 | Type    | Default                       | Behavior                                                   |
|----------------------|---------|-------------------------------|------------------------------------------------------------|
| `src`                | `Path`  | required                      | Input video file (`exists=True, dir_okay=False`).          |
| `--fps` / `-f`       | `float` | source video's FPS            | New frame rate. If omitted, uses the value from `probe_video`. |
| `--crop`             | bool    | `False`                       | Open OpenCV window to select a rectangle interactively.    |
| `--crop-rect`        | `str`   | `None`                        | `X:Y:W:H` integers; bypasses the GUI for scripted use.     |
| `--output` / `-o`    | `Path`  | `<name>_resampled.mp4`        | Output file. Must end in `.mp4`.                           |

Mutual exclusion enforced at the top of `cmd_resample`:

- If both `--crop` and `--crop-rect` are passed → red panel + `typer.Exit(2)`.
- If neither is passed and the requested fps equals the source fps → red panel "Nothing to do (same FPS, no crop)" + `typer.Exit(2)`.

Wrapping pattern (matches existing commands):

```python
try:
    if crop_flag:
        rect = processor.select_crop_rect(src)
    elif crop_rect_str:
        rect = _parse_crop_rect(crop_rect_str)
    else:
        rect = None

    with cli_ui.make_progress() as progress:
        task = progress.add_task(f"Resampling {src.name}", total=1)
        processor.resample_video(src, dst, fps=fps, crop=rect)
        progress.advance(task)
except ProcessorError as err:
    _handle(err)
cli_ui.success(f"Saved to: [bold]{dst}[/]")
```

`_parse_crop_rect`:

```python
def _parse_crop_rect(s: str) -> tuple[int, int, int, int]:
    try:
        parts = [int(p) for p in s.split(":")]
        if len(parts) != 4:
            raise ValueError
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        raise typer.BadParameter("--crop-rect must be X:Y:W:H with integer values")
```

### `src/vtermkosmos/menu.py`

A new `_flow_resample(src: Path)` function, registered in `_VIDEO_MENU`.

Flow:

1. Call `processor.probe_video(src)` to get the source FPS for display and as the prompt default.
2. `Prompt.ask("New FPS", default=str(round(source_fps, 2)))` and parse to `float`.
3. `Confirm.ask("Crop a region?", default=False)`.
4. If yes → `processor.select_crop_rect(src)`. The `ProcessorError("Crop cancelled …")` is caught by the existing `loop()` try/except and rendered as a red panel; the menu keeps running.
5. Output prompt with `_path_completion()` and default `<src.parent>/<src.stem>_resampled.mp4`.
6. Call `processor.resample_video(src, dst, fps=fps, crop=rect)` inside `cli_ui.make_progress()`.
7. `cli_ui.success(...)`. Loop returns to "Back to menu?" via the existing `loop()` machinery.

Menu placement: add as a new key in `_VIDEO_MENU`, kept after `wa-fix` and before `info`. Renumber later entries by one. (Exact key chosen during implementation by reading the current dict.)

## Errors and edge cases

| Situation                                          | Handling                                                                |
|----------------------------------------------------|-------------------------------------------------------------------------|
| `ffmpeg` missing                                    | `_ensure_ffmpeg()` already raises `ProcessorError`.                     |
| Source not a video / unsupported container          | `processor.probe_video` raises `ProcessorError`.                        |
| `--fps 0` or negative                               | `ProcessorError("FPS must be positive")` from `resample_video`.         |
| `--crop` and `--crop-rect` both set                 | Red panel + `typer.Exit(2)` from `cmd_resample`.                        |
| `--crop-rect` malformed                             | `typer.BadParameter` from `_parse_crop_rect`.                           |
| Crop rectangle out of source bounds                 | `ProcessorError("Crop rectangle out of bounds: ...")`.                  |
| User presses ESC in `cv2.selectROI`                 | `ProcessorError("Crop cancelled — no region selected.")` → red panel; menu loop continues. |
| No display available (`cv2.error` from `selectROI`) | `ProcessorError("GUI not available: ...")`. CLI users should pass `--crop-rect` instead. |
| Same FPS as source AND no crop                      | `cmd_resample` exits with code 2 and a red panel "Nothing to do".       |
| Output extension not `.mp4`                         | `ProcessorError("Resample output must be .mp4")`.                       |
| Audio codec incompatible with `.mp4` (`-c:a copy`)  | ffmpeg fails; `_run_ffmpeg` raises `ProcessorError` with stderr message verbatim. |

All `ProcessorError` instances bubble through the existing `_handle()` (CLI) and `loop()` (menu) machinery — no traceback ever reaches the user.

## Files touched

Three, in the order prescribed by `CLAUDE.md`:

1. **`src/vtermkosmos/processor.py`** — add `select_crop_rect` and `resample_video`.
2. **`src/vtermkosmos/main.py`** — add `cmd_resample` and `_parse_crop_rect`.
3. **`src/vtermkosmos/menu.py`** — add `_flow_resample` and register it in `_VIDEO_MENU`.

Not touched:

- `cli_ui.py` — existing progress / panel / banner machinery is sufficient.
- `pyproject.toml` — `cv2.selectROI` ships with `opencv-python`, already a dependency.
- `IMAGE_EXTS` / `VIDEO_EXTS` — no new file types.
- `CLAUDE.md` — the existing "adding a new command means touching three files" guidance still applies.
- `README.md` — the table of commands gets one new row, but that is an implementation detail, not part of this design.

## Manual verification

After implementation, run by hand:

- `./run.sh resample video.mp4 --fps 24` → re-encodes without crop, writes `video_resampled.mp4`.
- `./run.sh resample video.mp4 --fps 24 --crop` → opens GUI, drag rectangle, ENTER, writes resampled+cropped mp4.
- `./run.sh resample video.mp4 --fps 24 --crop-rect 100:50:640:480` → no GUI, runs headless.
- `./run.sh resample video.mp4 --fps 24 --crop --crop-rect 0:0:10:10` → red panel, exit 2.
- `./run.sh resample video.mp4` (omit `--fps`, no crop) → red panel "Nothing to do", exit 2.
- `./run.sh` → menu → pick a video → "resample" → test both yes/no crop paths.
- In the menu, press ESC during crop selection → red panel ("Crop cancelled — no region selected."), menu keeps running.
