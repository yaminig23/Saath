"""Tests for the AI video lesson engine.

Proves over real HTTP with a throwaway database and media folder, plus
direct pipeline runs: creation validates honestly (including the exact
missing-key refusal), paid footage waits for cost confirmation, the
worker advances jobs one step per tick, a single-clip short completes
with verified bytes + captions + transcript, provider failures and
timeouts fail honestly with failure rows, multi-scene assembly refuses
without ffmpeg, quotas hold, and users are isolated. The fake footage
provider and fake tutor client keep every test deterministic: no
network calls here, ever.

Run from the project folder: python -m unittest
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import kernel
import main
import models
import narration
import tutor
import video
from config import settings
from model_client import ModelError, ModelResponse

PIXEL_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
PHOTO_DATA_URL = "data:image/png;base64," + PIXEL_B64


class FakeTutorClient:
    """Deterministic lesson planner + illustrator (no network)."""

    name = "fake-tutor-1"
    supports_planning = True
    supports_vision = True

    def __init__(self, steps=1):
        self.steps = steps
        self.seen = []

    def generate(self, *, system, messages, response_format=None, max_output_tokens=None):
        self.seen.extend(messages)
        steps = [
            {
                "title": f"Step {i + 1}",
                "text": f"Do part {i + 1} slowly and carefully.",
                "image_prompt": f"simple illustration {i + 1}",
            }
            for i in range(self.steps)
        ]
        return ModelResponse(
            text=json.dumps({"steps": steps, "follow_up": "Ask about any step."}),
            model=self.name,
            input_chars=10,
            output_chars=10,
            input_tokens=5,
            output_tokens=15,
        )

    def generate_image(self, *, prompt):
        return {"model": self.name, "format": "png", "b64": PIXEL_B64}

    def describe_image(self, *, image_data_url, question, system=""):
        return ModelResponse(
            text="a white box with one round button", model=self.name, input_chars=10, output_chars=10
        )


def use_fake_client(testcase, steps=1):
    """Patch the tutor's model client for one test (restored after)."""
    real = tutor.model_client.get_model_client
    client = FakeTutorClient(steps=steps)
    tutor.model_client.get_model_client = lambda: client
    testcase.fake_tutor = client
    testcase.addCleanup(setattr, tutor.model_client, "get_model_client", real)


def synth_clip(path, size="320x240", seconds=1):
    """A genuine little MP4 (ffmpeg test pattern, local, no network)."""
    subprocess.run(
        [
            video.ffmpeg_path(), "-y", "-f", "lavfi", "-i",
            f"testsrc=duration={seconds}:size={size}:rate=10",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", path,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def clip_bytes_for_env(testcase):
    """Real synthesized footage when ffmpeg exists, else labeled fake bytes."""
    if video.ffmpeg_path() is None:
        return video.FAKE_MP4
    path = os.path.join(testcase.media, "synth.mp4")
    synth_clip(path)
    with open(path, "rb") as handle:
        return handle.read()


class VideoApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine)

        def override_get_session():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        main.app.dependency_overrides[db.get_session] = override_get_session
        self.client = TestClient(main.app)
        self._old_key = settings.meta_api_key
        self._old_runway = settings.runway_api_secret
        self._old_media = settings.media_dir
        settings.meta_api_key = ""
        settings.runway_api_secret = ""
        self.media = tempfile.mkdtemp(prefix="saath-video-")
        settings.media_dir = self.media

    def tearDown(self):
        settings.meta_api_key = self._old_key
        settings.runway_api_secret = self._old_runway
        settings.media_dir = self._old_media
        video.clear_providers()
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)
        shutil.rmtree(self.media, ignore_errors=True)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _create(self, headers, **overrides):
        body = {"topic": "use the kettle", "format": "short", "duration_s": 10, "provider": "fake"}
        body.update(overrides)
        return self.client.post("/video/jobs", json=body, headers=headers)

    def test_video_needs_login(self):
        self.assertEqual(self.client.post("/video/jobs", json={"topic": "x"}).status_code, 401)
        self.assertEqual(self.client.get("/video/jobs").status_code, 401)
        self.assertEqual(self.client.get("/video/jobs/1").status_code, 401)
        self.assertEqual(self.client.post("/video/jobs/1/confirm", json={"confirm": True}).status_code, 401)
        self.assertEqual(self.client.post("/video/jobs/1/cancel").status_code, 401)
        self.assertEqual(self.client.delete("/video/jobs/1").status_code, 401)
        self.assertEqual(self.client.get("/video/jobs/1/file").status_code, 401)
        self.assertEqual(self.client.get("/video/jobs/1/captions").status_code, 401)
        self.assertEqual(self.client.post("/video/jobs/1/ask", json={"question": "x"}).status_code, 401)
        self.assertEqual(self.client.get("/video/jobs/1/keyframes").status_code, 401)

    def test_create_validates(self):
        headers = self._login_headers()
        self.assertEqual(self._create(headers, topic="  ").status_code, 422)
        self.assertEqual(self._create(headers, format="movie").status_code, 422)
        self.assertEqual(self._create(headers, duration_s=99).status_code, 422)
        self.assertEqual(self._create(headers, duration_s=60).status_code, 422)
        self.assertEqual(self._create(headers, provider="nope").status_code, 422)
        refused = self._create(headers, provider="runway")
        self.assertEqual(refused.status_code, 422)
        self.assertIn("RUNWAYML_API_SECRET", refused.json()["detail"])
        made = self._create(headers)
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertFalse(body["live_footage"])
        self.assertIn("fake provider", body["cost_note"])

    def test_ownership_isolated(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        job_id = self._create(headers).json()["id"]
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}", headers=other).status_code, 404)
        self.assertEqual(
            self.client.post(f"/video/jobs/{job_id}/confirm", json={"confirm": True}, headers=other).status_code,
            404,
        )
        self.assertEqual(self.client.post(f"/video/jobs/{job_id}/cancel", headers=other).status_code, 404)
        self.assertEqual(self.client.delete(f"/video/jobs/{job_id}", headers=other).status_code, 404)
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/file", headers=other).status_code, 404)
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/captions", headers=other).status_code, 404)
        self.assertEqual(
            self.client.post(f"/video/jobs/{job_id}/ask", json={"question": "x"}, headers=other).status_code, 404
        )
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/keyframes", headers=other).status_code, 404)
        self.assertEqual(self.client.get("/video/jobs", headers=other).json(), [])
        self.assertEqual(len(self.client.get("/video/jobs", headers=headers).json()), 1)
        self.assertEqual(self.client.get("/video/jobs/9999", headers=headers).status_code, 404)

    def test_active_quota(self):
        headers = self._login_headers()
        self.assertEqual(self._create(headers).status_code, 201)
        self.assertEqual(self._create(headers).status_code, 201)
        third = self._create(headers)
        self.assertEqual(third.status_code, 422)
        self.assertIn("at once", third.json()["detail"])

    def test_paid_confirm_flow_over_http(self):
        headers = self._login_headers()
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(paid=True))
        job_id = self._create(headers).json()["id"]
        self.client.post("/worker/tick", headers=headers)
        waiting = self.client.get(f"/video/jobs/{job_id}", headers=headers).json()
        self.assertEqual(waiting["status"], "PENDING_APPROVAL")
        self.assertIn("Confirm to spend", waiting["cost_note"])
        confirmed = self.client.post(f"/video/jobs/{job_id}/confirm", json={"confirm": True}, headers=headers)
        self.assertEqual(confirmed.json()["status"], "PLANNING")
        other_id = self._create(headers).json()["id"]
        self.client.post("/worker/tick", headers=headers)
        declined = self.client.post(f"/video/jobs/{other_id}/confirm", json={"confirm": False}, headers=headers)
        self.assertEqual(declined.json()["status"], "CANCELLED")
        self.assertEqual(
            self.client.post(f"/video/jobs/{other_id}/confirm", json={"confirm": True}, headers=headers).status_code,
            422,
        )

    def test_cancel_and_delete_over_http(self):
        headers = self._login_headers()
        job_id = self._create(headers).json()["id"]
        self.assertEqual(
            self.client.delete(f"/video/jobs/{job_id}", headers=headers).status_code, 422
        )
        cancelled = self.client.post(f"/video/jobs/{job_id}/cancel", headers=headers)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(self.client.post(f"/video/jobs/{job_id}/cancel", headers=headers).status_code, 422)
        deleted = self.client.delete(f"/video/jobs/{job_id}", headers=headers)
        self.assertEqual(deleted.json(), {"deleted": job_id})
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}", headers=headers).status_code, 404)

    def test_files_not_ready(self):
        headers = self._login_headers()
        job_id = self._create(headers).json()["id"]
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/file", headers=headers).status_code, 422)
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/captions", headers=headers).status_code, 422)

    def test_worker_tick_completes_short_end_to_end(self):
        headers = self._login_headers()
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job_id = self._create(headers).json()["id"]
        status = "PENDING"
        for _ in range(18):
            report = self.client.post("/worker/tick", headers=headers).json()
            self.assertGreaterEqual(report["video_advanced"], 1)
            self.assertEqual(report["video_errors"], [])
            status = self.client.get(f"/video/jobs/{job_id}", headers=headers).json()["status"]
            if status == "COMPLETED":
                break
        self.assertEqual(status, "COMPLETED")
        body = self.client.get(f"/video/jobs/{job_id}", headers=headers).json()
        self.assertTrue(all(stage["state"] == "done" for stage in body["stages"]))
        self.assertTrue(body["has_file"])
        self.assertEqual(body["lesson"]["model"], "fake-tutor-1")
        clip = self.client.get(f"/video/jobs/{job_id}/file", headers=headers)
        self.assertEqual(clip.status_code, 200)
        self.assertEqual(clip.headers["content-type"], "video/mp4")
        self.assertEqual(clip.content[4:8], b"ftyp")
        captions = self.client.get(f"/video/jobs/{job_id}/captions", headers=headers).json()
        self.assertEqual(captions["format"], "srt")
        if video.ffmpeg_path() is None:
            self.assertIn(b"FAKE-VIDEO", clip.content)
            self.assertIn("00:00:00,000 --> 00:00:10,000", captions["captions"])
            self.assertEqual(body["has_audio"], "unknown")
        else:
            self.assertTrue(body["has_audio"])
            self.assertIn("00:00:00,000 --> 00:00:01,", captions["captions"])
        self.assertIn("Do part 1 slowly and carefully.", captions["captions"])
        self.assertIn("Step 1", captions["transcript"])
        notes = self.client.get("/notifications", headers=headers).json()
        self.assertTrue(any(note["kind"] == "video_ready" for note in notes))

    def test_ask_and_keyframes_over_http(self):
        headers = self._login_headers()
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job_id = self._create(headers).json()["id"]
        early = self.client.post(f"/video/jobs/{job_id}/ask", json={"question": "x"}, headers=headers)
        self.assertEqual(early.status_code, 422)
        self.assertEqual(self.client.get(f"/video/jobs/{job_id}/keyframes", headers=headers).json(), {"keyframes": []})
        for _ in range(18):
            status = self.client.post("/worker/tick", headers=headers)
            body = self.client.get(f"/video/jobs/{job_id}", headers=headers).json()
            if body["status"] == "COMPLETED":
                break
        self.assertEqual(body["status"], "COMPLETED")
        answer = self.client.post(f"/video/jobs/{job_id}/ask", json={"question": "slower?"}, headers=headers).json()
        self.assertTrue(answer["answer"])
        self.assertEqual(answer["model"], "fake-tutor-1")
        self.assertIsNone(answer["scene"])
        scoped = self.client.post(
            f"/video/jobs/{job_id}/ask", json={"question": "this step?", "scene_index": 0}, headers=headers
        ).json()
        self.assertEqual(scoped["scene"], 0)
        self.assertEqual(
            self.client.post(f"/video/jobs/{job_id}/ask", json={"question": "x", "scene_index": 9}, headers=headers).status_code,
            422,
        )
        frames = self.client.get(f"/video/jobs/{job_id}/keyframes", headers=headers).json()["keyframes"]
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0]["image_data_url"].startswith("data:image/png;base64,"))

    def test_local_demo_create_validation(self):
        headers = self._login_headers()
        self.assertEqual(self._create(headers, provider="local-demo", orientation="square").status_code, 422)
        refused = self._create(headers, provider="local-demo", orientation="portrait", format="tutorial", duration_s=30)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("portrait", refused.json()["detail"])
        if video.ffmpeg_path() is None:
            missing = self._create(headers, provider="local-demo")
            self.assertEqual(missing.status_code, 422)
            self.assertIn("ffmpeg", missing.json()["detail"])
            return
        made = self._create(headers, provider="local-demo")
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertTrue(body["live_footage"])
        self.assertEqual(body["orientation"], "landscape")
        self.assertIn("local rendering is free", body["cost_note"])
        self.assertIn("quota", body["cost_note"])
        self.assertEqual(
            self._create(headers, provider="local-demo", orientation="portrait").status_code, 201
        )

    def test_local_demo_refused_without_ffmpeg(self):
        headers = self._login_headers()
        real_which = video.shutil.which
        video.shutil.which = lambda name: None
        self.addCleanup(setattr, video.shutil, "which", real_which)
        old_ff, old_fp = settings.ffmpeg_path, settings.ffprobe_path
        settings.ffmpeg_path = ""
        settings.ffprobe_path = ""
        self.addCleanup(setattr, settings, "ffmpeg_path", old_ff)
        self.addCleanup(setattr, settings, "ffprobe_path", old_fp)
        self.assertIsNone(video.ffmpeg_path())
        refused = self._create(headers, provider="local-demo")
        self.assertEqual(refused.status_code, 422)
        self.assertIn("ffmpeg", refused.json()["detail"])

    def test_providers_video_row_missing(self):
        headers = self._login_headers()
        rows = self.client.get("/providers", headers=headers).json()
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["video"]["status"], "missing")
        self.assertIn("RUNWAYML_API_SECRET", by_name["video"]["detail"])
        check = self.client.post("/providers/video/check", headers=headers).json()
        self.assertFalse(check["ok"])


class VideoPipelineTest(unittest.TestCase):
    """Direct pipeline runs: failures, timeouts, quotas, units."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self._old_key = settings.meta_api_key
        self._old_runway = settings.runway_api_secret
        self._old_media = settings.media_dir
        settings.meta_api_key = ""
        settings.runway_api_secret = ""
        self.media = tempfile.mkdtemp(prefix="saath-video-")
        settings.media_dir = self.media

    def tearDown(self):
        settings.meta_api_key = self._old_key
        settings.runway_api_secret = self._old_runway
        settings.media_dir = self._old_media
        video.clear_providers()
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)
        shutil.rmtree(self.media, ignore_errors=True)

    def _run(self, job_id, limit=20):
        status = ""
        for _ in range(limit):
            status = video.advance_job(self.session, job_id)["status"]
            if status in video.TERMINAL:
                break
        return status

    def test_single_scene_completes_with_files(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10,
            image_data_url=PHOTO_DATA_URL, provider="fake",
        )
        self.assertTrue(os.path.exists(os.path.join(video.job_dir(job["id"]), "photo.png")))
        self.assertEqual(self._run(job["id"]), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertEqual(body["lesson"]["model"], "fake-tutor-1")
        self.assertTrue(body["lesson"]["vision_used"])
        self.assertEqual(body["scenes"][0]["seconds"], 10)
        directory = video.job_dir(job["id"])
        for name in ("keyframe_0.png", "clip_0.mp4", "lesson.mp4", "captions.srt", "transcript.txt", "narration_0.mp3"):
            self.assertTrue(os.path.exists(os.path.join(directory, name)), name)
        if video.ffmpeg_path() is None:
            self.assertEqual(body["has_audio"], "unknown")
        else:
            self.assertTrue(os.path.exists(os.path.join(directory, "norm_0.mp4")))
            self.assertTrue(body["has_audio"])
        task = kernel.get_task(self.session, 1, job["task_id"])
        self.assertEqual(task.status, "COMPLETED")
        self.assertIn("fake", task.evidence)
        notes = self.session.query(models.Notification).filter_by(user_id=1, kind="video_ready").all()
        self.assertEqual(len(notes), 1)

    @unittest.skipIf(video.ffmpeg_path(), "multi-scene assembly needs no-ffmpeg to fail honestly")
    def test_multi_scene_needs_ffmpeg(self):
        use_fake_client(self, steps=3)
        job = video.create_job(
            self.session, user_id=1, topic="assemble the shelf", format="tutorial", duration_s=30, provider="fake"
        )
        self.assertEqual(self._run(job["id"], limit=30), "FAILED")
        row = video._get_job(self.session, 1, job["id"])
        body = video.job_dict(row)
        self.assertIn("ffmpeg", body["error"])
        scenes = json.loads(row.scenes)
        self.assertEqual(len(scenes), 3)
        self.assertEqual(sum(scene["seconds"] for scene in scenes), 30)
        failures = self.session.query(models.Failure).filter_by(task_id=job["task_id"]).all()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].agent, "video")

    def test_provider_failure_records_failure_row(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(fail_with="boom"))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertEqual(self._run(job["id"]), "FAILED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertIn("boom", body["error"])
        failures = self.session.query(models.Failure).filter_by(task_id=job["task_id"]).all()
        self.assertEqual(len(failures), 1)
        self.assertIn("boom", failures[0].error)

    def test_retry_is_idempotent_then_times_out(self):
        use_fake_client(self, steps=1)
        footage = video.FakeVideoProvider(never_ready=True)
        video.set_provider("fake", footage)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        first_id = ""
        for _ in range(8):
            video.advance_job(self.session, job["id"])
            row = video._get_job(self.session, 1, job["id"])
            scenes = json.loads(row.scenes)
            first_id = scenes[0].get("provider_job_id", "") if scenes else ""
            if first_id:
                break
        self.assertTrue(first_id)
        video.advance_job(self.session, job["id"])
        row = video._get_job(self.session, 1, job["id"])
        self.assertEqual(json.loads(row.scenes)[0]["provider_job_id"], first_id)
        self.assertEqual(footage.submits, 1)
        scenes = json.loads(row.scenes)
        scenes[0]["submitted_at"] = "2000-01-01T00:00:00+00:00"
        row.scenes = json.dumps(scenes)
        self.session.commit()
        self.assertEqual(self._run(job["id"]), "FAILED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertIn("timed out", body["error"])

    def test_cancel_midflight(self):
        use_fake_client(self, steps=1)
        footage = video.FakeVideoProvider()
        video.set_provider("fake", footage)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        provider_job_id = ""
        for _ in range(8):
            video.advance_job(self.session, job["id"])
            row = video._get_job(self.session, 1, job["id"])
            scenes = json.loads(row.scenes)
            provider_job_id = scenes[0].get("provider_job_id", "") if scenes else ""
            if provider_job_id:
                break
        self.assertTrue(provider_job_id)
        cancelled = video.cancel_job(self.session, user_id=1, job_id=job["id"])
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertIn(provider_job_id, footage.cancelled)
        self.assertEqual(video.advance_job(self.session, job["id"])["status"], "CANCELLED")
        task = kernel.get_task(self.session, 1, job["task_id"])
        self.assertEqual(task.status, "FAILED")

    def test_decline_and_delete(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(paid=True))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertEqual(video.advance_job(self.session, job["id"])["status"], "PENDING_APPROVAL")
        declined = video.confirm_job(self.session, user_id=1, job_id=job["id"], confirm=False)
        self.assertEqual(declined["status"], "CANCELLED")
        video.clear_providers()
        job_id = job["id"]
        self.assertEqual(video.delete_job(self.session, user_id=1, job_id=job_id), {"deleted": job_id})
        self.assertFalse(os.path.exists(video.job_dir(job_id)))
        with self.assertRaises(kernel.TaskNotFoundError):
            video._get_job(self.session, 1, job_id)
        events = self.session.query(models.TaskEvent).filter_by(task_id=job["task_id"]).all()
        self.assertTrue(any("assets deleted" in event.note for event in events))

    def test_delete_refuses_active(self):
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        with self.assertRaises(video.VideoError):
            video.delete_job(self.session, user_id=1, job_id=job["id"])

    def test_daily_quota(self):
        for _ in range(5):
            job = video.create_job(
                self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
            )
            video.cancel_job(self.session, user_id=1, job_id=job["id"])
        with self.assertRaises(video.VideoError) as raised:
            video.create_job(
                self.session, user_id=1, topic="one more", format="short", duration_s=10, provider="fake"
            )
        self.assertIn("daily limit", str(raised.exception))

    def test_ask_validates(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        with self.assertRaises(video.VideoError):
            video.ask_about_lesson(self.session, user_id=1, job_id=job["id"], question="x")
        self.assertEqual(self._run(job["id"]), "COMPLETED")
        with self.assertRaises(video.VideoError):
            video.ask_about_lesson(self.session, user_id=1, job_id=job["id"], question="  ")
        with self.assertRaises(video.VideoError):
            video.ask_about_lesson(self.session, user_id=1, job_id=job["id"], question="x", scene_index=5)
        with self.assertRaises(kernel.TaskNotFoundError):
            video.ask_about_lesson(self.session, user_id=2, job_id=job["id"], question="x")
        with self.assertRaises(kernel.TaskNotFoundError):
            video.keyframes_data(self.session, user_id=2, job_id=job["id"])

    def test_ask_retries_once_after_truncation(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertEqual(self._run(job["id"]), "COMPLETED")

        class TruncatingAsk(FakeTutorClient):
            def __init__(self):
                super().__init__(steps=1)
                self.calls = 0

            def generate(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ModelError("model status 'incomplete' (max_output_tokens)")
                return ModelResponse(text="Short answer.", model=self.name, input_chars=5, output_chars=20)

        asking = TruncatingAsk()
        tutor.model_client.get_model_client = lambda: asking
        answer = video.ask_about_lesson(self.session, user_id=1, job_id=job["id"], question="again?")
        self.assertEqual(answer["answer"], "Short answer.")
        self.assertEqual(asking.calls, 2)

    @unittest.skipUnless(video.ffmpeg_path(), "multi-scene assembly needs ffmpeg")
    def test_multiscene_assembly_with_real_media(self):
        use_fake_client(self, steps=2)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self)))
        job = video.create_job(
            self.session, user_id=1, topic="assemble the shelf", format="tutorial", duration_s=30, provider="fake"
        )
        self.assertEqual(self._run(job["id"], limit=25), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertTrue(body["has_audio"])
        self.assertEqual(body["narration_note"], "narrated")
        directory = video.job_dir(job["id"])
        streams = video.ffprobe_streams(os.path.join(directory, "lesson.mp4"), label="test output")
        self.assertEqual((streams["width"], streams["height"]), (1280, 720))
        self.assertEqual(streams["vcodec"], "h264")
        self.assertTrue(streams["has_audio"])
        self.assertTrue(1.5 < streams["duration"] < 2.5)
        scenes = json.loads(video._get_job(self.session, 1, job["id"]).scenes)
        self.assertTrue(all(scene.get("actual_seconds") for scene in scenes))
        captions = video.captions_data(self.session, user_id=1, job_id=job["id"])["captions"]
        self.assertIn("00:00:00,000 --> 00:00:01,", captions)
        task = kernel.get_task(self.session, 1, job["task_id"])
        self.assertIn("1280x720", task.result)

    @unittest.skipUnless(video.ffmpeg_path(), "narration assembly needs ffmpeg")
    def test_narration_skips_honestly(self):
        use_fake_client(self, steps=1)
        video.set_provider("fake", video.FakeVideoProvider(clip_bytes=clip_bytes_for_env(self), no_audio=True))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertEqual(self._run(job["id"], limit=25), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertTrue(body["has_audio"])
        self.assertIn("skipped after 3 tries", body["narration_note"])

    def test_retryable_poll_keeps_job_alive(self):
        use_fake_client(self, steps=1)

        class FlakyFake(video.FakeVideoProvider):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.flakes = 2

            def poll(self, provider_job_id):
                if self.flakes > 0:
                    self.flakes -= 1
                    raise video._RetryableError("flaky")
                return super().poll(provider_job_id)

        footage = FlakyFake(clip_bytes=clip_bytes_for_env(self))
        video.set_provider("fake", footage)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertEqual(self._run(job["id"], limit=25), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertEqual(body["error"], "")
        self.assertEqual(footage.submits, 1)

    def test_ffmpeg_missing_fails_multiscene(self):
        use_fake_client(self, steps=2)
        real_which = video.shutil.which
        video.shutil.which = lambda name: None
        self.addCleanup(setattr, video.shutil, "which", real_which)
        old_ff, old_fp = settings.ffmpeg_path, settings.ffprobe_path
        settings.ffmpeg_path = ""
        settings.ffprobe_path = ""
        self.addCleanup(setattr, settings, "ffmpeg_path", old_ff)
        self.addCleanup(setattr, settings, "ffprobe_path", old_fp)
        self.assertIsNone(video.ffmpeg_path())
        job = video.create_job(
            self.session, user_id=1, topic="assemble the shelf", format="tutorial", duration_s=30, provider="fake"
        )
        self.assertEqual(self._run(job["id"], limit=25), "FAILED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertIn("ffmpeg", body["error"])

    def test_bad_ffmpeg_path_errors(self):
        old = settings.ffmpeg_path
        settings.ffmpeg_path = "C:\\definitely\\not\\here\\ffmpeg.exe"
        self.addCleanup(setattr, settings, "ffmpeg_path", old)
        with self.assertRaises(video.VideoError) as raised:
            video.ffmpeg_path()
        self.assertIn("FFMPEG_PATH", str(raised.exception))

    @unittest.skipUnless(video.ffprobe_path(), "stream inspection needs ffprobe")
    def test_ffprobe_validation(self):
        clip = os.path.join(self.media, "probe.mp4")
        synth_clip(clip, size="640x480", seconds=2)
        streams = video.ffprobe_streams(clip)
        self.assertEqual((streams["width"], streams["height"]), (640, 480))
        self.assertTrue(1.5 < streams["duration"] < 2.5)
        self.assertFalse(streams["has_audio"])
        garbage = os.path.join(self.media, "garbage.mp4")
        with open(garbage, "wb") as handle:
            handle.write(b"hello world, not a video")
        with self.assertRaises(video.VideoError) as raised:
            video.ffprobe_streams(garbage)
        self.assertIn("not a real video", str(raised.exception))

    def test_runway_http_mapping(self):
        provider = video.RunwayProvider(api_key="k")

        class Resp:
            def __init__(self, status_code, text="", payload=None):
                self.status_code = status_code
                self.text = text
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("no json")
                return self._payload

        with mock.patch("video.httpx.get", return_value=Resp(401, "unauthorized")):
            with self.assertRaises(video.VideoError) as raised:
                provider.poll("x")
            self.assertIn("RUNWAYML_API_SECRET", str(raised.exception))
        with mock.patch("video.httpx.get", return_value=Resp(429, "slow down")):
            with self.assertRaises(video._RetryableError):
                provider.poll("x")
        with mock.patch("video.httpx.get", side_effect=httpx.TimeoutException("slow")):
            with self.assertRaises(video._RetryableError):
                provider.poll("x")
        with mock.patch("video.httpx.post", return_value=Resp(503, "busy")):
            with self.assertRaises(video.VideoError) as raised:
                provider.submit_text_to_video(prompt="x", duration_s=5)
            self.assertIn("duplicate charge", str(raised.exception))
        with mock.patch("video.httpx.post", return_value=Resp(401, "nope")):
            with self.assertRaises(video.VideoError) as raised:
                provider.submit_text_to_video(prompt="x", duration_s=5)
            self.assertIn("RUNWAYML_API_SECRET", str(raised.exception))
        with mock.patch("video.httpx.post", return_value=Resp(200, "", {"id": "a1"})):
            self.assertEqual(provider.submit_audio(text="hi"), "a1")
        with mock.patch("video.httpx.get", return_value=Resp(500, "oops")):
            with self.assertRaises(video._RetryableError):
                provider.download("https://cdn.example/v.mp4")
        with mock.patch("video.httpx.get", return_value=Resp(404, "gone")):
            with self.assertRaises(video.VideoError):
                provider.download("https://cdn.example/v.mp4")

    def test_cost_estimate(self):
        self.assertEqual(video.estimate_cost_usd("runway", 10), 1.2)
        self.assertEqual(video.estimate_cost_usd("fake", 10), 0.0)
        video.set_provider("fake", video.FakeVideoProvider(paid=True))
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        self.assertIn("Confirm to spend", job["cost_note"])
        self.assertEqual(job["cost"]["model"], "fake")
        self.assertEqual(job["cost"]["estimate_usd"], 0.0)
        self.assertEqual(job["cost"]["seconds"], 10)

    def test_unknown_provider(self):
        with self.assertRaises(video.VideoError):
            video.get_provider("nope")
        with self.assertRaises(video.VideoError):
            video.create_job(
                self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="nope"
            )

    def test_runway_refuses_without_key(self):
        provider = video.RunwayProvider(api_key="")
        self.assertFalse(provider.configured)
        with self.assertRaises(video.VideoError) as raised:
            provider.submit_text_to_video(prompt="x", duration_s=10)
        self.assertIn("RUNWAYML_API_SECRET", str(raised.exception))
        with self.assertRaises(video.VideoError):
            provider.submit_image_to_video(image_bytes=b"x", prompt="x", duration_s=10)
        with self.assertRaises(video.VideoError):
            provider.poll("abc")
        self.assertTrue(video.RunwayProvider(api_key="k").configured)
        provider.cancel("abc")

    def test_normalize_task(self):
        self.assertEqual(
            video.normalize_task({"status": "SUCCEEDED", "output": ["https://cdn.example/v.mp4"]})["state"], "ready"
        )
        ready = video.normalize_task({"status": "succeeded", "artifacts": [{"url": "https://cdn.example/w.mp4"}]})
        self.assertEqual((ready["state"], ready["video_url"]), ("ready", "https://cdn.example/w.mp4"))
        self.assertEqual(video.normalize_task({"status": "SUCCEEDED"})["state"], "failed")
        failed = video.normalize_task({"status": "FAILED", "failure": "content blocked"})
        self.assertEqual((failed["state"], failed["error"]), ("failed", "content blocked"))
        self.assertEqual(video.normalize_task({"status": "CANCELLED"})["state"], "failed")
        self.assertEqual(video.normalize_task({"status": "RUNNING"})["state"], "pending")
        self.assertEqual(video.normalize_task({"status": "SOMETHING_NEW"})["state"], "pending")
        self.assertEqual(video.normalize_task({})["state"], "pending")

    def test_verify_and_captions(self):
        good = os.path.join(self.media, "good.mp4")
        with open(good, "wb") as handle:
            handle.write(video.FAKE_MP4)
        self.assertEqual(video.verify_video_file(good), {"bytes": len(video.FAKE_MP4)})
        bad = os.path.join(self.media, "bad.mp4")
        with open(bad, "wb") as handle:
            handle.write(b"hello world, not a video")
        with self.assertRaises(video.VideoError):
            video.verify_video_file(bad)
        with self.assertRaises(video.VideoError):
            video.verify_video_file(os.path.join(self.media, "missing.mp4"))
        empty = os.path.join(self.media, "empty.mp4")
        open(empty, "wb").close()
        with self.assertRaises(video.VideoError):
            video.verify_video_file(empty)
        scenes = [
            {"title": "A", "narration": "First  bit.", "start": 0, "end": 5},
            {"title": "B", "narration": "Second bit.", "start": 5, "end": 12},
        ]
        self.assertEqual(
            video.build_captions(scenes),
            "1\n00:00:00,000 --> 00:00:05,000\nFirst bit.\n\n2\n00:00:05,000 --> 00:00:12,000\nSecond bit.\n",
        )

    def test_storyboard_validation(self):
        video._validate_scenes([{"index": 0, "title": "A", "narration": "Do it.", "seconds": 10}])
        with self.assertRaises(video.VideoError):
            video._validate_scenes([])
        with self.assertRaises(video.VideoError):
            video._validate_scenes([{"index": 1, "title": "A", "narration": "Do it.", "seconds": 10}])
        with self.assertRaises(video.VideoError):
            video._validate_scenes([{"index": 0, "title": "  ", "narration": "Do it.", "seconds": 10}])
        with self.assertRaises(video.VideoError):
            video._validate_scenes([{"index": 0, "title": "A", "narration": "", "seconds": 10}])
        with self.assertRaises(video.VideoError):
            video._validate_scenes([{"index": 0, "title": "A", "narration": "Do it.", "seconds": 0}])

    def test_orientation_rejected(self):
        with self.assertRaises(video.VideoError) as raised:
            video.create_job(
                self.session, user_id=1, topic="use the kettle", format="short", duration_s=10,
                provider="local-demo", orientation="square",
            )
        self.assertIn("orientation", str(raised.exception))
        with self.assertRaises(video.VideoError) as raised:
            video.create_job(
                self.session, user_id=1, topic="assemble the shelf", format="tutorial", duration_s=30,
                provider="local-demo", orientation="portrait",
            )
        self.assertIn("portrait", str(raised.exception))

    def test_local_demo_provider_unit(self):
        provider = video.get_provider("local-demo")
        self.assertEqual(provider.name, "local-demo")
        self.assertTrue(provider.renders_locally)
        self.assertFalse(provider.paid)
        self.assertEqual(provider.configured, video.ffmpeg_path() is not None)
        self.assertEqual(video.estimate_cost_usd("local-demo", 10), 0.0)

    def test_guidance_reaches_planner(self):
        use_fake_client(self, steps=1)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10, provider="fake"
        )
        video.advance_job(self.session, job["id"])
        video.advance_job(self.session, job["id"])
        sent = " ".join(message.get("content", "") for message in self.fake_tutor.seen)
        self.assertIn("Teach safely", sent)
        self.assertIn("safety warning", sent)

    def _frame_md5(self, clip_path, frame):
        completed = subprocess.run(
            [video.ffmpeg_path(), "-i", clip_path, "-vf", f"select='eq(n,{frame})'",
             "-vframes", "1", "-f", "md5", "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return completed.stdout.strip()

    @unittest.skipUnless(video.ffmpeg_path(), "local rendering needs ffmpeg")
    def test_local_demo_end_to_end(self):
        use_fake_client(self, steps=1)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10,
            provider="local-demo",
        )
        self.assertIn("local rendering is free", job["cost_note"])
        self.assertEqual(self._run(job["id"], limit=20), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertEqual(body["provider"], "local-demo")
        self.assertEqual(body["orientation"], "landscape")
        self.assertEqual(body["lesson"]["model"], "fake-tutor-1")
        self.assertTrue(body["has_audio"])
        if narration.available_voices():
            self.assertTrue(body["narration_note"].startswith("narrated ("))
        else:
            self.assertIn("continuing silent", body["narration_note"])
        directory = video.job_dir(job["id"])
        for name in ("keyframe_0.png", "clip_0.mp4", "lesson.mp4", "captions.srt", "transcript.txt"):
            self.assertTrue(os.path.exists(os.path.join(directory, name)), name)
        scenes = json.loads(video._get_job(self.session, 1, job["id"]).scenes)
        self.assertEqual(scenes[0]["clip_source"], "local")
        streams = video.ffprobe_streams(os.path.join(directory, "lesson.mp4"), label="test output")
        self.assertEqual((streams["width"], streams["height"]), (1280, 720))
        self.assertEqual(streams["vcodec"], "h264")
        self.assertTrue(streams["has_audio"])
        self.assertTrue(9 < streams["duration"] < 11)
        final = os.path.join(directory, "lesson.mp4")
        last_frame = max(1, int(round(streams["duration"] * 30)) - 1)
        self.assertNotEqual(self._frame_md5(final, 0), self._frame_md5(final, last_frame))

    @unittest.skipUnless(video.ffmpeg_path(), "local rendering needs ffmpeg")
    def test_local_demo_portrait(self):
        use_fake_client(self, steps=1)
        job = video.create_job(
            self.session, user_id=1, topic="use the kettle", format="short", duration_s=10,
            provider="local-demo", orientation="portrait",
        )
        self.assertEqual(self._run(job["id"], limit=20), "COMPLETED")
        body = video.job_dict(video._get_job(self.session, 1, job["id"]))
        self.assertEqual(body["orientation"], "portrait")
        streams = video.ffprobe_streams(
            os.path.join(video.job_dir(job["id"]), "lesson.mp4"), label="test output"
        )
        self.assertEqual((streams["width"], streams["height"]), (720, 1280))


if __name__ == "__main__":
    unittest.main()
