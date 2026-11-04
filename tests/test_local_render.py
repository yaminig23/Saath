"""Tests for the local renderer and narration engine.

Proves with real FFmpeg execution: deterministic motion variety, valid
filter graphs, genuine animated output (decoded frames differ across
time — a slideshow cannot pass), portrait mode, garbage rejection, and
local speech synthesis. Tests needing ffmpeg/voices skip honestly when
those are absent; pure-logic tests always run.

Run from the project folder: python -m unittest
"""

import os
import subprocess
import tempfile
import unittest

import local_render
import narration
import video


def synth_png(path, size="320x240"):
    subprocess.run(
        [
            video.ffmpeg_path(), "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=1",
            "-vframes", "1", path,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def frame_md5(clip_path, frame):
    completed = subprocess.run(
        [
            video.ffmpeg_path(), "-i", clip_path, "-vf", f"select='eq(n,{frame})'",
            "-vframes", "1", "-f", "md5", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.stdout.strip()


class RenderLogicTest(unittest.TestCase):
    def test_motion_varies_by_scene(self):
        motions = [local_render.motion_for(index) for index in range(6)]
        self.assertEqual(len(set(motions)), 6)
        self.assertEqual(local_render.motion_for(0), local_render.motion_for(6))

    def test_zoompan_filter_shape(self):
        chain = local_render.zoompan_filter("pan-right", 90, 1280, 720)
        self.assertIn("zoompan=", chain)
        self.assertIn("s=1280x720", chain)
        self.assertIn("d=90", chain)
        with self.assertRaises(local_render.RenderError):
            local_render.zoompan_filter("zoom-in", 0, 1280, 720)

    def test_build_filter_escapes_text(self):
        chain, titled = local_render.build_filter(
            motion="zoom-in", seconds=10, index=0, title="a:b'c%d\\e", orientation="landscape", is_last=True
        )
        self.assertIn("fade=t=in", chain)
        self.assertIn("fade=t=out", chain)
        if local_render.fonts_available():
            self.assertTrue(titled)
            self.assertIn("drawtext=", chain)
            self.assertIn("a\\:b\\'c\\%d\\\\e", chain)
            self.assertIn("Ask Saath about this lesson", chain)
        else:
            self.assertFalse(titled)
            self.assertNotIn("drawtext=", chain)

    def test_build_filter_validates(self):
        with self.assertRaises(local_render.RenderError):
            local_render.render_scene(b"x", seconds=0, index=0, orientation="landscape", title="t", is_last=False, ffmpeg="ffmpeg")
        with self.assertRaises(local_render.RenderError):
            local_render.render_scene(b"x", seconds=2, index=0, orientation="square", title="t", is_last=False, ffmpeg="ffmpeg")
        with self.assertRaises(local_render.RenderError):
            local_render.render_scene(b"", seconds=2, index=0, orientation="landscape", title="t", is_last=False, ffmpeg="ffmpeg")


@unittest.skipUnless(video.ffmpeg_path(), "scene rendering needs ffmpeg")
class RenderExecutionTest(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="saath-render-")
        self.addCleanup(shutil_rmtree, self.workdir)

    def test_rendered_clip_is_genuinely_animated(self):
        image = os.path.join(self.workdir, "art.png")
        synth_png(image)
        with open(image, "rb") as handle:
            art = handle.read()
        clip = local_render.render_scene(
            art, seconds=3, index=2, orientation="landscape", title="Power button", is_last=False,
            ffmpeg=video.ffmpeg_path(),
        )
        self.assertGreater(len(clip), 1000)
        path = os.path.join(self.workdir, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(clip)
        streams = video.ffprobe_streams(path, label="test clip")
        self.assertEqual((streams["width"], streams["height"]), (1280, 720))
        self.assertTrue(2.5 < streams["duration"] < 3.5)
        first = frame_md5(path, 0)
        last = frame_md5(path, 89)
        self.assertNotEqual(first, last)

    def test_portrait_render(self):
        image = os.path.join(self.workdir, "art.png")
        synth_png(image)
        with open(image, "rb") as handle:
            art = handle.read()
        clip = local_render.render_scene(
            art, seconds=2, index=1, orientation="portrait", title="Power button", is_last=True,
            ffmpeg=video.ffmpeg_path(),
        )
        path = os.path.join(self.workdir, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(clip)
        streams = video.ffprobe_streams(path, label="test clip")
        self.assertEqual((streams["width"], streams["height"]), (720, 1280))

    def test_garbage_image_fails(self):
        with self.assertRaises(local_render.RenderError):
            local_render.render_scene(
                b"not an image at all", seconds=2, index=0, orientation="landscape", title="t",
                is_last=False, ffmpeg=video.ffmpeg_path(),
            )


def shutil_rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class NarrationTest(unittest.TestCase):
    def test_speak_validates_without_engine(self):
        with self.assertRaises(narration.NarrationError):
            narration.speak_to_wav("  ", os.path.join(tempfile.gettempdir(), "never.wav"))
        with self.assertRaises(narration.NarrationError):
            narration.speak_to_wav("x" * 2001, os.path.join(tempfile.gettempdir(), "never.wav"))

    def test_local_speech_synthesis(self):
        voices = narration.available_voices()
        if not voices:
            self.skipTest("no system voices installed")
        workdir = tempfile.mkdtemp(prefix="saath-narrate-")
        self.addCleanup(shutil_rmtree, workdir)
        path = os.path.join(workdir, "line.wav")
        result = narration.speak_to_wav("Press the round power button once.", path)
        self.assertTrue(result["voice"])
        self.assertGreater(os.path.getsize(path), 1000)
        if video.ffprobe_path():
            completed = subprocess.run(
                [video.ffprobe_path(), "-v", "error", "-show_entries", "stream=codec_type",
                 "-of", "csv", path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertIn("audio", completed.stdout)


if __name__ == "__main__":
    unittest.main()
