# Resample with optional crop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `resample` operation to vtkosmos that re-encodes a video at a chosen FPS, optionally cropping a rectangle selected interactively in an OpenCV window or passed as `X:Y:W:H`.

**Architecture:** Two new pure functions in `processor.py` (`select_crop_rect`, `resample_video`), a new Typer subcommand `cmd_resample` in `main.py` (plus a `_parse_crop_rect` CLI helper), and a new `_flow_resample` in `menu.py` registered in `_VIDEO_MENU`. Matches the existing three-file layering already documented in the project `CLAUDE.md`.

**Tech Stack:** Python ≥ 3.10, OpenCV (`opencv-python`), ffmpeg (subprocess), Typer, Rich, prompt_toolkit. Same stack as the rest of vtkosmos — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-15-resample-with-optional-crop-design.md` (Approved 2026-05-15).

**Testing convention:** This project intentionally has no automated test suite (per `CLAUDE.md`: *"No test suite, linter, or formatter is configured. Don't invent one unless asked."*). Each task below ends with a **manual smoke test** taken from the spec's "Manual verification" section, and a commit. Do not add pytest or any test infrastructure.

**Commit message style:** Plain imperative ("Add X to processor"), no Conventional-Commits prefix — matches the project's git log.

---

## Files touched

| File                                  | Change                                                                                  |
|---------------------------------------|-----------------------------------------------------------------------------------------|
| `src/vtermkosmos/processor.py`        | Add `select_crop_rect()` and `resample_video()`. No existing function touched.          |
| `src/vtermkosmos/main.py`             | Add `cmd_resample` Typer command and a file-local `_parse_crop_rect` helper.            |
| `src/vtermkosmos/menu.py`             | Add `_ask_float` helper, `_flow_resample`, and register it as key `"4"` in `_VIDEO_MENU` (renumbering `info` to `"5"`). |

Files **not** touched (verified against the spec):

- `src/vtermkosmos/cli_ui.py` — progress/panel/banner machinery is sufficient. `commands_table()` and `render_menu()` are dead code in the current `loop()` flow; leave them alone.
- `src/vtermkosmos/__init__.py` — no version bump; feature add, not a release.
- `pyproject.toml` — `cv2.selectROI` ships with `opencv-python`, already a dependency.
- `processor.IMAGE_EXTS` / `VIDEO_EXTS` — no new file types.

---

## Task 0: Commit the design spec

The spec is currently staged but not committed. Make it part of git history before any code lands, so the implementation commits reference a permanent design artifact.

**Files:**
- Already staged: `docs/superpowers/specs/2026-05-15-resample-with-optional-crop-design.md`

- [ ] **Step 1: Verify staged state**

Run: `git status`
Expected: shows the spec as staged ("new file: docs/superpowers/specs/2026-05-15-resample-with-optional-crop-design.md"), no other staged files.

- [ ] **Step 2: Commit the spec on its own**

```bash
git commit -m "Add design spec for resample with optional crop"
```

- [ ] **Step 3: Stage and commit this plan**

```bash
git add docs/superpowers/plans/2026-05-15-resample-with-optional-crop.md
git commit -m "Add implementation plan for resample with optional crop"
```

---

## Task 1: Add `select_crop_rect` to `processor.py`

A pure helper that opens the first frame of a video in an OpenCV GUI window, lets the user drag a rectangle, and returns `(x, y, w, h)`. Putting a GUI primitive in `processor.py` is a deliberate exception to "no UI in processor" — it uses OpenCV (already a processor dependency), returns pure ints, and never touches Rich/Typer. The trade-off keeps `main.py` and `menu.py` agnostic to OpenCV.

**Files:**
- Modify: `src/vtermkosmos/processor.py` — add new function after `probe_video` (around line 81).

- [ ] **Step 1: Add `select_crop_rect`**

Insert this function in `src/vtermkosmos/processor.py` after `probe_video` and before `_run_ffmpeg`:

```python
def select_crop_rect(src: Path) -> tuple[int, int, int, int]:
    """Open an OpenCV window on the first frame; return the user's `(x, y, w, h)`.

    Used by the `resample` command to pick a crop rectangle interactively.
    Raises `ProcessorError` if the video cannot be read, the GUI is not
    available, or the user cancels without selecting a region.
    """
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
            "Drag a rectangle - ENTER to confirm, ESC to cancel",
            frame,
            showCrosshair=True,
            fromCenter=False,
        )
    except cv2.error as e:
        raise ProcessorError(f"GUI not available: {e}") from e
    finally:
        cv2.destroyAllWindows()

    if w == 0 or h == 0:
        raise ProcessorError("Crop cancelled - no region selected.")
    return int(x), int(y), int(w), int(h)
```

- [ ] **Step 2: Manual smoke test — import sanity**

Run: `./run.sh --help`
Expected: banner + help text print without import errors. (We're only verifying the file still parses; the new function isn't wired up yet.)

- [ ] **Step 3: Commit**

```bash
git add src/vtermkosmos/processor.py
git commit -m "Add select_crop_rect helper to processor"
```

---

## Task 2: Add `resample_video` to `processor.py`

Re-encodes a video at a chosen FPS, optionally cropping first. Always writes `.mp4` (H.264 + yuv420p + `+faststart`, audio stream-copied with `-c:a copy`). CRF is hardcoded at 20 — add `--quality` later if it becomes a recurring request.

**Files:**
- Modify: `src/vtermkosmos/processor.py` — add new function after `wa_fix` (around line 251), before the "BATCH" section.

- [ ] **Step 1: Add `resample_video`**

Insert this function in `src/vtermkosmos/processor.py` after `wa_fix`:

```python
# ---------------------------------------------------------------------------
# RESAMPLE - change FPS, optionally cropping
# ---------------------------------------------------------------------------
def resample_video(
    src: Path,
    dst: Path,
    fps: float,
    crop: tuple[int, int, int, int] | None = None,
) -> Path:
    """Re-encode `src` at `fps`, optionally cropping a `(x, y, w, h)` region.

    Always writes `.mp4` (H.264, CRF 20, yuv420p, `+faststart`). The original
    audio stream is copied without re-encoding (`-c:a copy`); if the source
    audio is not mp4-compatible, ffmpeg's stderr is propagated verbatim via
    `ProcessorError`.
    """
    _ensure_exists(src)
    if fps <= 0:
        raise ProcessorError("FPS must be positive.")
    if dst.suffix.lower() != ".mp4":
        raise ProcessorError("Resample output must be .mp4")

    if crop is not None:
        x, y, w, h = crop
        info = probe_video(src)
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > info.width or y + h > info.height:
            raise ProcessorError(
                f"Crop rectangle out of bounds: ({x},{y},{w},{h}) "
                f"vs source {info.width}x{info.height}."
            )
        vf = f"crop={w}:{h}:{x}:{y},fps={fps}"
    else:
        vf = f"fps={fps}"

    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "copy",
            str(dst),
        ]
    )
    return dst
```

- [ ] **Step 2: Manual smoke test — headless resample**

Pick any short `.mp4` you have lying around (let's call it `sample.mp4`). From a Python shell launched via the venv:

```bash
./run.sh python -c "
from pathlib import Path
from vtermkosmos.processor import resample_video, probe_video
src = Path('sample.mp4')
info = probe_video(src)
print(f'source: {info.width}x{info.height} @ {info.fps:.2f} fps')
out = resample_video(src, Path('sample_resampled.mp4'), fps=15.0)
print(f'wrote: {out}')
"
```

Note: `run.sh` only knows how to dispatch to the `vterm` console script — there's no `./run.sh python`. Use either of these instead:

```bash
# uv backend (most common):
source config.sh && uv run python -c "..."

# or venv backend:
source config.sh && source "$VTERM_VENV_PATH/bin/activate" && python -c "..."
```

Expected: prints the source dimensions/fps, writes `sample_resampled.mp4` with the new fps, exits cleanly. Verify with `./run.sh info sample_resampled.mp4` — fps should read ~15.00.

- [ ] **Step 3: Manual smoke test — crop validation**

In the same shell:

```python
from pathlib import Path
from vtermkosmos.processor import resample_video
resample_video(Path('sample.mp4'), Path('bad.mp4'), fps=15.0, crop=(0, 0, 99999, 99999))
```

Expected: raises `ProcessorError("Crop rectangle out of bounds: ...")`. No file written.

- [ ] **Step 4: Commit**

```bash
git add src/vtermkosmos/processor.py
git commit -m "Add resample_video to processor"
```

---

## Task 3: Add `cmd_resample` and `_parse_crop_rect` to `main.py`

Wire `resample` as a Typer subcommand. Mirrors the pattern of `cmd_wa_fix` (line 109 in `main.py`): build the destination path, wrap the processor call in `cli_ui.make_progress()`, and let `_handle()` turn `ProcessorError` into a red panel. `_parse_crop_rect` parses user-supplied `"X:Y:W:H"` — a CLI concern, so it lives here, not in `processor.py`.

**Files:**
- Modify: `src/vtermkosmos/main.py` — add the helper near the top (after `_handle`), and the command after `cmd_wa_fix` (around line 125) and before `cmd_batch`.

- [ ] **Step 1: Add `_parse_crop_rect` helper**

Insert in `src/vtermkosmos/main.py` directly after `_handle` (around line 24):

```python
def _parse_crop_rect(s: str) -> tuple[int, int, int, int]:
    """Parse '--crop-rect X:Y:W:H' into four ints. CLI concern, not processor."""
    try:
        parts = [int(p) for p in s.split(":")]
        if len(parts) != 4:
            raise ValueError
        return parts[0], parts[1], parts[2], parts[3]
    except ValueError:
        raise typer.BadParameter("--crop-rect must be X:Y:W:H with integer values")
```

- [ ] **Step 2: Add `cmd_resample` Typer command**

Insert in `src/vtermkosmos/main.py` after the `cmd_wa_fix` block (after line 125), before `cmd_batch`:

```python
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
```

- [ ] **Step 3: Manual smoke test — `--help` shows new subcommand**

Run: `./run.sh --help`
Expected: `resample` appears in the commands list with the help text "Re-encode at a chosen FPS, optionally cropping a rectangle."

Run: `./run.sh resample --help`
Expected: shows the five options (`--fps`, `--crop`, `--crop-rect`, `--output`, plus the positional `src`).

- [ ] **Step 4: Manual smoke test — headless resample, no crop**

```bash
./run.sh resample sample.mp4 --fps 24
```

Expected: writes `sample_resampled.mp4`, green success line `Saved to: sample_resampled.mp4`. Verify fps with `./run.sh info sample_resampled.mp4`.

- [ ] **Step 5: Manual smoke test — headless resample with `--crop-rect`**

```bash
./run.sh resample sample.mp4 --fps 24 --crop-rect 50:30:320:240 -o sample_cropped.mp4
```

Expected: writes `sample_cropped.mp4` at 320x240, 24 fps. No GUI opens.

- [ ] **Step 6: Manual smoke test — error paths**

```bash
./run.sh resample sample.mp4 --crop --crop-rect 0:0:10:10
```

Expected: red error panel "Use exactly one of: --crop or --crop-rect.", exit code 2.

```bash
./run.sh resample sample.mp4 --fps "$(./run.sh info sample.mp4 | grep -oP 'FPS\s+\K[0-9.]+' | head -1)"
```

(Or just call it with no flags: `./run.sh resample sample.mp4` — same effect, no fps change.)
Expected: red error panel "Nothing to do (same FPS, no crop).", exit code 2.

```bash
./run.sh resample sample.mp4 --fps 24 -o out.mkv
```

Expected: red error panel "Resample output must be .mp4", exit code 1.

```bash
./run.sh resample sample.mp4 --fps 24 --crop-rect not-a-rect
```

Expected: Typer's own `BadParameter` error mentioning "X:Y:W:H with integer values".

- [ ] **Step 7: Commit**

```bash
git add src/vtermkosmos/main.py
git commit -m "Wire resample as a Typer command"
```

---

## Task 4: Add `_flow_resample` and register it in `_VIDEO_MENU` (`menu.py`)

Three changes in `menu.py`:

1. A new `_ask_float` helper, parallel to the existing `_ask_int` (line 490 in `menu.py`).
2. A new `_flow_resample(src)` function, structured like `_flow_wa_fix` (line 541).
3. Register it in `_VIDEO_MENU` as key `"4"`, between `wa-fix` (key `"3"`) and `info` (which gets renumbered to `"5"`).

Loop-level `ProcessorError` handling already exists in `loop()` (line 681) — pressing ESC during `cv2.selectROI` will raise `ProcessorError("Crop cancelled - no region selected.")`, which the loop catches and renders as a red panel before continuing.

**Files:**
- Modify: `src/vtermkosmos/menu.py` — three insertions described below.

- [ ] **Step 1: Add `_ask_float` helper**

Insert in `src/vtermkosmos/menu.py` directly after `_ask_int` (after line 496), before `_ask_time`:

```python
def _ask_float(label: str, default: float) -> float:
    while True:
        raw = Prompt.ask(f"[bold {cli_ui.BRAND_COLOR}]{label}[/]", default=str(default))
        try:
            return float(raw)
        except ValueError:
            cli_ui.error("Please enter a number.")
```

- [ ] **Step 2: Add `_flow_resample`**

Insert in `src/vtermkosmos/menu.py` directly after `_flow_wa_fix` (after line 549), before `_flow_info`:

```python
def _flow_resample(src: Path) -> None:
    info = processor.probe_video(src)
    source_fps = info.fps if info.fps > 0 else 30.0
    fps = _ask_float("New FPS", default=round(source_fps, 2))
    do_crop = Confirm.ask(
        f"[bold {cli_ui.BRAND_COLOR}]Crop a region?[/]", default=False
    )
    rect = processor.select_crop_rect(src) if do_crop else None
    dst = _ask_out_path("Output file", src.with_name(f"{src.stem}_resampled.mp4"))
    with cli_ui.make_progress() as progress:
        task = progress.add_task(f"Resampling {src.name}", total=1)
        processor.resample_video(src, dst, fps=fps, crop=rect)
        progress.advance(task)
    cli_ui.success(f"Saved to: [bold]{dst}[/]")
```

- [ ] **Step 3: Register in `_VIDEO_MENU`**

Replace the `_VIDEO_MENU` block in `src/vtermkosmos/menu.py` (lines 619-624). The existing block is:

```python
_VIDEO_MENU: dict[str, tuple[str, str, Callable[[Path], None]]] = {
    "1": ("cut",     "Trim a segment (no re-encode).",              _flow_cut),
    "2": ("convert", "Convert to another format.",                  _flow_convert_file),
    "3": ("wa-fix",  "Optimize for WhatsApp (H.264/AAC, ≤720p).",   _flow_wa_fix),
    "4": ("info",    "Show metadata (resolution, fps, duration).",  _flow_info),
}
```

Replace it with:

```python
_VIDEO_MENU: dict[str, tuple[str, str, Callable[[Path], None]]] = {
    "1": ("cut",      "Trim a segment (no re-encode).",              _flow_cut),
    "2": ("convert",  "Convert to another format.",                  _flow_convert_file),
    "3": ("wa-fix",   "Optimize for WhatsApp (H.264/AAC, ≤720p).",   _flow_wa_fix),
    "4": ("resample", "Re-encode at a chosen FPS, optionally crop.", _flow_resample),
    "5": ("info",     "Show metadata (resolution, fps, duration).",  _flow_info),
}
```

- [ ] **Step 4: Manual smoke test — menu lists the new action**

Run: `./run.sh`
Then in the file browser, arrow down to a `.mp4` file and press `Enter`.
Expected: action picker shows five entries — `cut`, `convert`, `wa-fix`, `resample`, `info` — plus `quit`. Press `q` to leave.

- [ ] **Step 5: Manual smoke test — resample flow, no crop**

Run: `./run.sh`, pick a video, choose `resample` (key `4` or arrow + Enter).
- At "New FPS" prompt, accept default by pressing Enter, then type a different value like `15` and confirm.
- At "Crop a region?" answer `n`.
- At "Output file" accept default `<name>_resampled.mp4`.
Expected: progress bar runs, green success line. The output file plays at the new FPS.

- [ ] **Step 6: Manual smoke test — resample flow, with crop**

Same as Step 5 but answer `y` to "Crop a region?".
Expected: OpenCV window opens with the first frame. Drag a rectangle, press Enter. Window closes, progress bar runs, green success. Output file plays at the new FPS and is cropped.

- [ ] **Step 7: Manual smoke test — ESC cancels crop cleanly**

Same as Step 6 but press `ESC` (or Enter with no rectangle drawn) when the OpenCV window opens.
Expected: red error panel "Crop cancelled - no region selected.", **menu keeps running**, "Back to menu?" prompt appears.

- [ ] **Step 8: Commit**

```bash
git add src/vtermkosmos/menu.py
git commit -m "Add resample flow to the interactive menu"
```

---

## Task 5: End-to-end verification sweep

Final pass exercising every code path from the spec's "Manual verification" section in one sitting, against a clean checkout state. Catches anything that slipped past the per-task tests (e.g., import cycles, menu rendering issues, prompt defaults).

**Files:** none modified — verification only.

- [ ] **Step 1: CLI — resample without crop**

```bash
./run.sh resample video.mp4 --fps 24
```
Expected: re-encodes without crop, writes `video_resampled.mp4`. Green success line.

- [ ] **Step 2: CLI — resample with interactive crop**

```bash
./run.sh resample video.mp4 --fps 24 --crop
```
Expected: OpenCV window opens with the first frame; drag a rectangle, press Enter; mp4 is written cropped and resampled.

- [ ] **Step 3: CLI — resample with scripted crop**

```bash
./run.sh resample video.mp4 --fps 24 --crop-rect 100:50:640:480
```
Expected: no GUI; writes a cropped mp4 at 24 fps.

- [ ] **Step 4: CLI — mutex error**

```bash
./run.sh resample video.mp4 --fps 24 --crop --crop-rect 0:0:10:10
```
Expected: red panel, exit code 2.

- [ ] **Step 5: CLI — no-op error**

```bash
./run.sh resample video.mp4
```
Expected: red panel "Nothing to do (same FPS, no crop).", exit code 2.

- [ ] **Step 6: Menu — happy path, no crop**

`./run.sh` → menu → pick a video → choose `resample` → enter FPS → answer `n` to crop → accept default output.
Expected: success.

- [ ] **Step 7: Menu — happy path, with crop**

Same as Step 6 but answer `y` to crop and drag a rectangle.
Expected: success.

- [ ] **Step 8: Menu — crop cancelled keeps the menu alive**

Same as Step 7 but press ESC in the OpenCV window.
Expected: red "Crop cancelled - no region selected." panel; "Back to menu?" prompt appears; answering `y` returns to the file browser.

- [ ] **Step 9: No outstanding changes**

Run: `git status`
Expected: working tree clean. Every implementation change is committed under a `select_crop_rect` / `resample_video` / `Wire resample as a Typer command` / `Add resample flow to the interactive menu` commit.

---

## Out of scope (not in this plan)

Stated explicitly in the spec's "Non-goals" section — flagged here so future-you doesn't mistake them for missing work:

- Output containers other than `.mp4`.
- Configurable CRF / bitrate (CRF is hardcoded at 20).
- Re-encoded audio (audio is stream-copied with `-c:a copy`).
- Non-rectangular crops.
- Live preview during ROI selection — only the first frame is shown.
- Batch resample / batch crop across a folder.
- Crop on images.
- Trimming start/end as part of `resample` — use `cut` first if needed.
- Updating `cli_ui.commands_table()` to list `resample` — that function is currently dead code (not called from `loop()`); leave it alone.

If any of these come up later, write a new spec — don't tack them onto this one.
