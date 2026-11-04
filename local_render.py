"""Local illustrated-video renderer (FFmpeg Ken Burns + titles + fades).

Turns one illustration per scene into genuinely animated clips: every
scene gets deterministic camera motion (varied by scene index), scenes
carry their step title, and fades soften the joins. Output is real
H.264 — frames differ across time, which the tests verify by hashing
decoded frames. A static slideshow cannot pass those tests.
"""

import os
import subprocess
import tempfile

FPS = 30
LANDSCAPE = (1280, 720)
PORTRAIT = (720, 1280)
ARIAL = "C:/Windows/Fonts/arial.ttf"

MOTIONS = ("zoom-in", "zoom-out", "pan-right", "pan-left", "pan-down", "pan-up")


class RenderError(Exception):
    """A scene could not be rendered."""


def motion_for(index: int) -> str:
    """Deterministic motion per scene: variety without randomness."""
    return MOTIONS[index % len(MOTIONS)]


def fonts_available() -> bool:
    return os.path.exists(ARIAL.replace("/", os.sep))


def _escape_drawtext(text: str) -> str:
    clean = " ".join(str(text or "").split())
    return clean.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")


def zoompan_filter(motion: str, frames: int, width: int, height: int) -> str:
    """Scale-up + zoompan chain for one motion style."""
    if frames <= 0:
        raise RenderError("scene must last at least one frame")
    if motion == "zoom-out":
        zoom = "z='max(1.25-0.0009*on,1.0)'"
        pos = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif motion == "pan-right":
        zoom = "z='1.25'"
        pos = f"x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'"
    elif motion == "pan-left":
        zoom = "z='1.25'"
        pos = f"x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'"
    elif motion == "pan-down":
        zoom = "z='1.25'"
        pos = f"x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/{frames}'"
    elif motion == "pan-up":
        zoom = "z='1+0.0004*on'"
        pos = f"y='(ih-ih/zoom)*(1-on/{frames})':x='iw/2-(iw/zoom/2)'"
    else:
        zoom = "z='min(1+0.0009*on,1.25)'"
        pos = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return (
        f"scale={width * 2}:{height * 2},"
        f"zoompan={zoom}:{pos}:d={frames}:s={width}x{height}:fps={FPS}"
    )


def build_filter(*, motion: str, seconds: float, index: int, title: str, orientation: str, is_last: bool) -> tuple:
    """Full video filter chain + whether titles were applied."""
    width, height = PORTRAIT if orientation == "portrait" else LANDSCAPE
    frames = max(1, int(round(seconds * FPS)))
    chain = zoompan_filter(motion, frames, width, height)
    chain += f",fade=t=in:st=0:d=0.4,fade=t=out:st={max(0.1, seconds - 0.4):.3f}:d=0.4"
    if not fonts_available():
        return chain, False
    label = _escape_drawtext(f"Step {index + 1}: {title}"[:48])
    chain += (
        ",drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf'"
        f":text='{label}':fontsize=40:fontcolor=white"
        ":box=1:boxcolor=black@0.6:boxborderw=14"
        ":x=(w-text_w)/2:y=64"
    )
    if is_last and seconds >= 2:
        chain += (
            ",drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf'"
            ":text='Ask Jizo about this lesson':fontsize=36:fontcolor=white"
            ":box=1:boxcolor=black@0.6:boxborderw=12"
            ":x=(w-text_w)/2:y=h-140"
        )
    return chain + f",format=yuv420p", True


def render_scene(
    image_bytes: bytes, *, seconds: float, index: int, orientation: str, title: str, is_last: bool, ffmpeg: str
) -> bytes:
    """Animate one illustration into an MP4 clip. Returns the clip bytes."""
    if orientation not in ("landscape", "portrait"):
        raise RenderError(f"orientation must be landscape or portrait, got {orientation!r}")
    if not seconds or seconds <= 0:
        raise RenderError("scene must last longer than zero seconds")
    if not image_bytes:
        raise RenderError("scene has no illustration")
    width, height = PORTRAIT if orientation == "portrait" else LANDSCAPE
    frames = max(1, int(round(seconds * FPS)))
    motion = motion_for(index)
    chain, _titled = build_filter(
        motion=motion, seconds=seconds, index=index, title=title, orientation=orientation, is_last=is_last
    )
    img_path = out_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(image_bytes)
            img_path = handle.name
        handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        out_path = handle.name
        handle.close()
        try:
            completed = subprocess.run(
                [
                    ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS), "-i", img_path,
                    "-vf", chain, "-frames:v", str(frames),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", out_path,
                ],
                capture_output=True,
                text=True,
                timeout=120 + int(seconds) * 10,
                cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired:
            raise RenderError(f"scene {index + 1} render timed out")
        if completed.returncode != 0:
            raise RenderError(f"scene {index + 1} render failed: {completed.stderr[-500:]}")
        with open(out_path, "rb") as handle:
            return handle.read()
    finally:
        for path in (img_path, out_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
