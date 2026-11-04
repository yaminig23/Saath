"""AI video lesson engine (Visual Tutor video stage).

Pipeline per job: Muse plans a lesson (the reused tutor planner) ->
storyboard scenes -> Muse Image keyframes -> provider motion clips ->
assembled MP4 + captions + transcript. Jobs advance one step per worker
tick; nothing blocks a request while footage renders.

Honesty boundaries, enforced in code rather than promised in prose:
- Meta's Model API has no video endpoint, so motion footage needs a
  third-party provider (Runway). Without its key, runway jobs refuse
  at creation with the exact missing variable.
- Joining scenes needs ffmpeg. Without it, multi-scene jobs fail with
  that exact message instead of faking a video. Single-clip shorts
  are stored directly (there is no assembly step to fake).
- The fake provider is deterministic test tooling. Every job row
  records its provider name, fake output bytes are labeled inside,
  and job status reports live_footage=false for fake jobs.
- Verification checks real file facts (exists, non-empty, MP4 magic).
  Duration and audio probing need ffprobe, so durations stay
  provider-claimed and are labeled as such.
"""

import base64
import binascii
import json
import os
import shutil
import subprocess

import httpx

import kernel
import local_render
import model_client
import narration
import tutor
from config import settings
from models import Failure, Notification, VideoJob
from util import parse_utc, utcnow

VIDEO_AGENT = "video"

FORMATS = ("short", "tutorial", "scenario")
DURATIONS = {"short": (10, 20, 30), "tutorial": (30, 60, 90), "scenario": (30, 60, 90)}
ORIENTATIONS = ("landscape", "portrait")
PROVIDERS = ("runway", "local-demo", "fake")
VIDEO_SAFETY_GUIDANCE = (
    "Teach safely: use generic steps that work for most models, "
    "and put any safety warning as the first sentence of the relevant step."
)

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")

MAX_ACTIVE_PER_USER = 2
MAX_PER_DAY = 5
CLIP_TIMEOUT_S = 1800
MAX_CLIP_BYTES = 100_000_000
REQUEST_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0

RUNWAY_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_MODEL = "gen4.5"
RUNWAY_USD_PER_SEC = 0.12
MIN_CLIP_S = 3
MAX_CLIP_S = 30
AUDIO_ATTEMPTS = 3

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

_STAGE_LABELS = (
    ("PLANNING", "Planning the lesson"),
    ("GENERATING_ASSETS", "Creating illustrations"),
    ("GENERATING_VIDEO", "Generating footage"),
    ("ASSEMBLING", "Assembling the video"),
    ("VERIFYING", "Verifying playback"),
)
_STAGE_ORDER = ("PENDING", "PENDING_APPROVAL") + tuple(key for key, _ in _STAGE_LABELS) + ("COMPLETED",)

_PHOTO_EXTENSIONS = {
    "data:image/png": "png",
    "data:image/jpeg": "jpg",
    "data:image/gif": "gif",
    "data:image/webp": "webp",
}

FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + b"FAKE-VIDEO-BYTES-NOT-A-REAL-VIDEO" + b"\x00" * 84


class VideoError(kernel.KernelError):
    """A video request broke a video rule."""


class _RetryableError(VideoError):
    """A transient provider hiccup (rate limit, 5xx, timeout) on an
    idempotent read. The job keeps its state and tries again next tick.
    Submissions are never retried silently: a failed submit fails
    honestly rather than risking a duplicate paid task."""


class VideoProvider:
    """One motion-footage source. poll() returns normalized states."""

    name = ""
    paid = False
    supports_audio = False
    renders_locally = False

    @property
    def configured(self) -> bool:
        return True

    def submit_text_to_video(self, *, prompt: str, duration_s: int) -> str:
        raise NotImplementedError

    def submit_image_to_video(self, *, image_bytes: bytes, prompt: str, duration_s: int) -> str:
        raise NotImplementedError

    def submit_audio(self, *, text: str) -> str:
        raise NotImplementedError

    def poll(self, provider_job_id: str) -> dict:
        """Return {"state": pending|ready|failed, "video_url": str, "error": str}."""
        raise NotImplementedError

    def cancel(self, provider_job_id: str) -> None:
        """Best-effort cancellation. Never raises."""

    def download(self, video_url: str) -> bytes:
        raise NotImplementedError

    def download_audio(self, audio_url: str) -> bytes:
        raise NotImplementedError


_RUNNING_STATES = {"PENDING", "QUEUED", "THROTTLED", "RUNNING", "IN_PROGRESS", "PROCESSING"}
_SUCCESS_STATES = {"SUCCEEDED", "COMPLETED", "SUCCESS", "DONE"}
_FAILED_STATES = {"FAILED", "FAILURE", "ERROR"}
_CANCELLED_STATES = {"CANCELLED", "CANCELED"}


def _find_video_url(task: dict) -> str:
    """Pull the finished clip URL out of a provider task payload (any shape)."""
    output = task.get("output")
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
        if isinstance(first, dict) and str(first.get("url", "")).startswith("http"):
            return first["url"]
    if isinstance(output, dict) and str(output.get("url", "")).startswith("http"):
        return output["url"]
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        url = (artifacts[0] or {}).get("url", "") if isinstance(artifacts[0], dict) else ""
        if str(url).startswith("http"):
            return url
    for key in ("video_url", "url"):
        url = task.get(key, "")
        if isinstance(url, str) and url.startswith("http"):
            return url
    return ""


def normalize_task(task: dict) -> dict:
    """Map a provider task payload onto pending/ready/failed. Unknown states
    keep polling until the clip timeout rather than failing blindly."""
    status = str((task or {}).get("status", "") or "").upper()
    if status in _SUCCESS_STATES:
        url = _find_video_url(task or {})
        if url:
            return {"state": "ready", "video_url": url, "error": ""}
        return {"state": "failed", "video_url": "", "error": "provider finished without a video URL"}
    if status in _FAILED_STATES:
        message = (
            (task or {}).get("failure") or (task or {}).get("error") or (task or {}).get("message") or "provider failed the task"
        )
        return {"state": "failed", "video_url": "", "error": str(message)[:500]}
    if status in _CANCELLED_STATES:
        return {"state": "failed", "video_url": "", "error": "provider cancelled the task"}
    return {"state": "pending", "video_url": "", "error": ""}


def _task_id(payload: dict) -> str:
    found = payload.get("id") or payload.get("taskId") or (payload.get("task") or {}).get("id")
    if not found:
        raise VideoError("runway returned no task id")
    return str(found)


def _auth_error(status: int, body: str) -> VideoError:
    if status in (401, 403):
        return VideoError(f"runway rejected the API key (HTTP {status}) — check RUNWAYML_API_SECRET")
    return VideoError(f"runway error {status}: {body[:300]}")


class RunwayProvider(VideoProvider):
    """Runway footage + narration over the documented task API.

    Model (gen4.5), fields (promptText/promptImage/ratio/duration),
    Bearer auth, and data-URI images are confirmed against the official
    guides; the TTS request shape is a minimal guess and degrades
    gracefully (narration skips with a record instead of killing jobs).
    The live path is unverified (no key here): without one, every
    method refuses instead of pretending.
    """

    name = "runway"
    paid = True
    supports_audio = True

    def __init__(self, api_key=None) -> None:
        if api_key is None:
            api_key = settings.runway_api_secret
        self.api_key = (api_key or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        if not self.configured:
            raise VideoError(
                "video generation needs RUNWAYML_API_SECRET in .env "
                "(create a key at dev.runwayml.com, add it, and restart the server)"
            )
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, path: str, body: dict) -> dict:
        """Submit a paid task. Failures here never retry silently: the
        provider may have taken the task despite the error, so the job
        fails honestly rather than risking a duplicate charge."""
        try:
            response = httpx.post(RUNWAY_BASE + path, headers=self._headers(), json=body, timeout=REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise VideoError(f"runway did not confirm the task ({exc}); failing safely rather than risking a duplicate charge")
        if response.status_code >= 400:
            if response.status_code in (429, 500, 502, 503, 504):
                raise VideoError(
                    f"runway did not confirm the task (HTTP {response.status_code}); "
                    "failing safely rather than risking a duplicate charge"
                )
            raise _auth_error(response.status_code, response.text)
        try:
            return response.json()
        except ValueError:
            raise VideoError("runway returned invalid JSON")

    def submit_text_to_video(self, *, prompt: str, duration_s: int) -> str:
        payload = self._post(
            "/text_to_video",
            {"model": RUNWAY_MODEL, "promptText": prompt, "duration": duration_s, "ratio": "1280:720"},
        )
        return _task_id(payload)

    def submit_image_to_video(self, *, image_bytes: bytes, prompt: str, duration_s: int) -> str:
        data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        payload = self._post(
            "/image_to_video",
            {
                "model": RUNWAY_MODEL,
                "promptText": prompt,
                "promptImage": data_url,
                "duration": duration_s,
                "ratio": "1280:720",
            },
        )
        return _task_id(payload)

    def submit_audio(self, *, text: str) -> str:
        payload = self._post("/text_to_speech", {"text": text})
        return _task_id(payload)

    def poll(self, provider_job_id: str) -> dict:
        try:
            response = httpx.get(
                f"{RUNWAY_BASE}/tasks/{provider_job_id}", headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise _RetryableError(f"runway status check failed ({exc}); retrying on a later tick")
        if response.status_code in (429, 500, 502, 503, 504):
            raise _RetryableError(f"runway is busy (HTTP {response.status_code}); retrying on a later tick")
        if response.status_code >= 400:
            raise _auth_error(response.status_code, response.text)
        try:
            return normalize_task(response.json())
        except ValueError:
            raise VideoError("runway returned invalid JSON")

    def cancel(self, provider_job_id: str) -> None:
        try:
            httpx.delete(f"{RUNWAY_BASE}/tasks/{provider_job_id}", headers=self._headers(), timeout=REQUEST_TIMEOUT)
        except (VideoError, httpx.HTTPError):
            pass

    def download(self, video_url: str) -> bytes:
        return self._download(video_url, "clip")

    def download_audio(self, audio_url: str) -> bytes:
        return self._download(audio_url, "narration")

    def _download(self, url: str, label: str) -> bytes:
        try:
            response = httpx.get(url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise _RetryableError(f"{label} download failed ({exc}); retrying on a later tick")
        if response.status_code in (429, 500, 502, 503, 504):
            raise _RetryableError(f"{label} download hit HTTP {response.status_code}; retrying on a later tick")
        if response.status_code != 200:
            raise VideoError(f"{label} download error {response.status_code}")
        if len(response.content) > MAX_CLIP_BYTES:
            raise VideoError(f"{label} is larger than 100 MB; refusing to store it")
        return response.content


class LocalDemoProvider(VideoProvider):
    """Muse-powered illustrated video: local FFmpeg animation of the
    scene illustrations plus local narration. No paid footage, no key —
    but ffmpeg must be installed."""

    name = "local-demo"
    paid = False
    supports_audio = False
    renders_locally = True

    @property
    def configured(self) -> bool:
        try:
            return ffmpeg_path() is not None
        except VideoError:
            return False

    def render_scene(self, image_bytes: bytes, *, seconds: float, index: int, orientation: str, title: str, is_last: bool) -> bytes:
        ffmpeg = ffmpeg_path()
        if ffmpeg is None:
            raise VideoError("local video needs ffmpeg (set FFMPEG_PATH or install it)")
        try:
            return local_render.render_scene(
                image_bytes, seconds=seconds, index=index, orientation=orientation,
                title=title, is_last=is_last, ffmpeg=ffmpeg,
            )
        except local_render.RenderError as exc:
            raise VideoError(str(exc))


def _silent_wav(seconds=1, rate=8000) -> bytes:
    """One silent WAV (pure stdlib, really decodes). Test narration."""
    import struct

    frames = rate * seconds
    data = b"\x00" * frames * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16, b"data", len(data)
    )
    return header + data


class FakeVideoProvider(VideoProvider):
    """Deterministic stand-in for tests. Default output is labeled fake
    bytes; tests may inject real synthesized clips/narration to exercise
    the genuine assembly path."""

    name = "fake"
    supports_audio = True

    def __init__(
        self,
        *,
        paid=False,
        polls_until_ready=2,
        fail_with="",
        never_ready=False,
        clip_bytes=None,
        narration_bytes=None,
        no_audio=False,
    ) -> None:
        self.paid = paid
        self.polls_until_ready = polls_until_ready
        self.fail_with = fail_with
        self.never_ready = never_ready
        self.clip_bytes = FAKE_MP4 if clip_bytes is None else clip_bytes
        self.narration_bytes = None if no_audio else (_silent_wav() if narration_bytes is None else narration_bytes)
        self.submits = 0
        self.audio_submits = 0
        self.cancelled: list = []
        self._polls: dict = {}

    def submit_text_to_video(self, *, prompt: str, duration_s: int) -> str:
        return self._submit()

    def submit_image_to_video(self, *, image_bytes: bytes, prompt: str, duration_s: int) -> str:
        return self._submit()

    def submit_audio(self, *, text: str) -> str:
        if self.narration_bytes is None:
            raise VideoError("fake narration not configured")
        self.audio_submits += 1
        job_id = f"fake-audio-{self.audio_submits}"
        self._polls[job_id] = 0
        return job_id

    def _submit(self) -> str:
        self.submits += 1
        job_id = f"fake-{self.submits}"
        self._polls[job_id] = 0
        return job_id

    def poll(self, provider_job_id: str) -> dict:
        if self.fail_with:
            return {"state": "failed", "video_url": "", "error": self.fail_with}
        seen = self._polls.get(provider_job_id, 0) + 1
        self._polls[provider_job_id] = seen
        if self.never_ready or seen < self.polls_until_ready:
            return {"state": "pending", "video_url": "", "error": ""}
        return {"state": "ready", "video_url": f"fake://{provider_job_id}", "error": ""}

    def cancel(self, provider_job_id: str) -> None:
        self.cancelled.append(provider_job_id)
        self._polls.pop(provider_job_id, None)

    def download(self, video_url: str) -> bytes:
        if not video_url.startswith("fake://"):
            raise VideoError(f"fake provider cannot download {video_url!r}")
        return self.clip_bytes

    def download_audio(self, audio_url: str) -> bytes:
        if not audio_url.startswith("fake://"):
            raise VideoError(f"fake provider cannot download {audio_url!r}")
        if self.narration_bytes is None:
            raise VideoError("fake narration not configured")
        return self.narration_bytes


_instances: dict = {}
_overrides: dict = {}


def get_provider(name: str) -> VideoProvider:
    """One footage source by name. Instances persist: poll counters and
    submitted ids must survive across worker ticks."""
    if name in _overrides:
        return _overrides[name]
    if name not in _instances:
        if name == "runway":
            _instances[name] = RunwayProvider()
        elif name == "local-demo":
            _instances[name] = LocalDemoProvider()
        elif name == "fake":
            _instances[name] = FakeVideoProvider()
        else:
            raise VideoError(f"unknown video provider {name!r} (want {', '.join(PROVIDERS)})")
    return _instances[name]


def set_provider(name: str, provider: VideoProvider) -> None:
    """Pin a scripted provider (tests only)."""
    _overrides[name] = provider


def clear_providers() -> None:
    """Drop pinned providers (tests only)."""
    _overrides.clear()


def _media_root() -> str:
    return os.path.join(_PROJECT_ROOT, settings.media_dir)


def ffmpeg_path() -> str | None:
    """ffmpeg binary: explicit FFMPEG_PATH wins (a wrong path errors
    loudly), else PATH discovery, else None."""
    configured = (settings.ffmpeg_path or "").strip()
    if configured:
        if os.path.exists(configured):
            return configured
        raise VideoError(f"FFMPEG_PATH points to a missing file: {configured}")
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    """ffprobe binary: explicit FFPROBE_PATH wins, else PATH, else None."""
    configured = (settings.ffprobe_path or "").strip()
    if configured:
        if os.path.exists(configured):
            return configured
        raise VideoError(f"FFPROBE_PATH points to a missing file: {configured}")
    return shutil.which("ffprobe")


def ffprobe_streams(path: str, *, label: str = "a scene clip") -> dict:
    """Real stream facts about a media file. Raises when the file is not
    a genuine video (or when ffprobe itself is missing)."""
    probe = ffprobe_path()
    if probe is None:
        raise VideoError("media inspection needs ffprobe (set FFPROBE_PATH or install it)")
    try:
        completed = subprocess.run(
            [
                probe, "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise VideoError(f"ffprobe timed out inspecting {label}")
    if completed.returncode != 0:
        raise VideoError(f"{label} is not a real video file")
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        raise VideoError("ffprobe returned invalid JSON")
    video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoError(f"{label} has no video stream")
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise VideoError(f"{label} has no measurable duration")
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "vcodec": str(video.get("codec_name") or ""),
        "has_audio": any(s.get("codec_type") == "audio" for s in payload.get("streams", [])),
    }


def estimate_cost_usd(provider_name: str, seconds: int) -> float:
    """Footage estimate at list price (narration bills extra)."""
    if provider_name == "runway":
        return round(seconds * RUNWAY_USD_PER_SEC, 2)
    return 0.0


def job_dir(job_id: int) -> str:
    return os.path.join(_media_root(), f"job_{job_id}")


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw or "")
    except ValueError:
        return fallback


def _srt_time(total_seconds: float) -> str:
    millis = max(0, int(round(total_seconds * 1000)))
    hours, remainder = divmod(millis, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def build_captions(scenes: list) -> str:
    """Deterministic SRT captions from scene narration (no provider needed)."""
    entries = []
    for position, scene in enumerate(scenes, start=1):
        text = " ".join(str(scene.get("narration", "")).split())
        entries.append(
            f"{position}\n{_srt_time(scene.get('start', 0))} --> {_srt_time(scene.get('end', 0))}\n{text}"
        )
    return "\n\n".join(entries) + "\n"


def verify_video_file(path: str) -> dict:
    """Real file facts: exists, non-empty, MP4 magic. Duration/audio probing
    needs ffprobe, so those stay provider-claimed."""
    if not os.path.exists(path):
        raise VideoError("verification failed: output file was not created")
    size = os.path.getsize(path)
    if size == 0:
        raise VideoError("verification failed: output file is empty")
    with open(path, "rb") as handle:
        head = handle.read(12)
    if len(head) < 8 or head[4:8] != b"ftyp":
        raise VideoError("verification failed: output is not an MP4 file")
    return {"bytes": size}


def _stages(status: str, provider: str = "") -> list:
    if status in ("FAILED", "CANCELLED"):
        return [{"key": key, "label": label, "state": "todo"} for key, label in _STAGE_LABELS]
    position = _STAGE_ORDER.index(status) - 2 if status in _STAGE_ORDER else -2
    stages = []
    for index, (key, label) in enumerate(_STAGE_LABELS):
        if key == "GENERATING_VIDEO" and provider == "local-demo":
            label = "Rendering animated scenes"
        if index < position:
            state = "done"
        elif index == position:
            state = "current"
        else:
            state = "todo"
        stages.append({"key": key, "label": label, "state": state})
    return stages


def _validate_scenes(scenes: list) -> None:
    """Storyboard schema check: ordered scenes with text and duration."""
    if not scenes:
        raise VideoError("storyboard has no scenes")
    for position, scene in enumerate(scenes):
        if scene.get("index") != position:
            raise VideoError("storyboard scene numbers are out of order")
        if not str(scene.get("title", "")).strip() or not str(scene.get("narration", "")).strip():
            raise VideoError(f"storyboard scene {position + 1} is missing text")
        if not scene.get("seconds") or scene["seconds"] <= 0:
            raise VideoError(f"storyboard scene {position + 1} has no duration")


def job_dict(job: VideoJob) -> dict:
    scenes = _load_json(job.scenes, [])
    request = _load_json(job.storyboard, {})
    return {
        "id": job.id,
        "task_id": job.task_id,
        "topic": job.topic,
        "format": job.format,
        "duration_s": job.duration_s,
        "status": job.status,
        "provider": job.provider,
        "live_footage": job.provider != "fake",
        "lesson": {
            "task_id": request.get("lesson_task_id"),
            "model": request.get("model", ""),
            "vision_used": bool(request.get("vision_used")),
        },
        "orientation": request.get("orientation", "landscape"),
        "language": request.get("language", "en"),
        "scenes": [
            {
                "index": scene.get("index"),
                "title": scene.get("title", ""),
                "seconds": scene.get("seconds", 0),
                "start": scene.get("start", 0),
                "end": scene.get("end", 0),
                "has_keyframe": bool(scene.get("keyframe")),
                "has_clip": bool(scene.get("clip")),
            }
            for scene in scenes
        ],
        "stages": _stages(job.status, job.provider),
        "has_file": bool(job.output_path),
        "waiting_on": "confirm the cost to continue" if job.status == "PENDING_APPROVAL" else "",
        "error": job.error,
        "cost_note": job.cost_note,
        "cost": {
            "provider": job.provider,
            "model": {"runway": RUNWAY_MODEL, "local-demo": "local"}.get(job.provider, "fake"),
            "scenes": len(scenes),
            "seconds": job.duration_s,
            "estimate_usd": estimate_cost_usd(job.provider, job.duration_s),
            "note": (
                f"Footage estimate at the {RUNWAY_MODEL} list price (${RUNWAY_USD_PER_SEC}/sec); "
                "narration bills extra at provider rates."
                if job.provider == "runway"
                else (
                    "Local rendering is free; Muse planning/illustrations use API quota."
                    if job.provider == "local-demo"
                    else "No provider spend."
                )
            ),
        },
        "has_audio": request.get("has_audio", "unknown"),
        "narration_note": request.get("narration_note", ""),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _get_job(db, user_id: int, job_id: int) -> VideoJob:
    job = db.query(VideoJob).filter_by(id=job_id, user_id=user_id).first()
    if job is None:
        raise kernel.TaskNotFoundError("video job not found")
    return job


def _set_status(db, job: VideoJob, status: str, note: str) -> None:
    job.status = status
    job.updated_at = utcnow()
    kernel.log_event(db, user_id=job.user_id, task_id=job.task_id, event_type="video_stage", note=note)


def _fail(db, job: VideoJob, message: str) -> None:
    job.status = "FAILED"
    job.error = message[:2000]
    job.updated_at = utcnow()
    kernel.transition_task(
        db, user_id=job.user_id, task_id=job.task_id, to_status="FAILED", note="video job failed", result=f"failed: {message[:1500]}"
    )
    db.add(
        Failure(
            user_id=job.user_id,
            task_id=job.task_id,
            agent=VIDEO_AGENT,
            tool="video-pipeline",
            error=message[:2000],
            context=json.dumps({"job_id": job.id, "stage": job.status}),
            created_at=utcnow(),
        )
    )


def _check_photo(value: str) -> tuple:
    """Validate a photo data URL. Returns (extension, raw bytes) or ("", b"")."""
    data_url = (value or "").strip()
    if not data_url:
        return "", b""
    if not data_url.startswith("data:image/"):
        raise VideoError("photo must be a data:image/... data URL")
    if len(data_url) > tutor.MAX_IMAGE_DATA_URL_LEN:
        raise VideoError("photo is too large (about 5 MB max)")
    try:
        header, encoded = data_url.split(",", 1)
    except ValueError:
        raise VideoError("photo data URL is malformed")
    extension = _PHOTO_EXTENSIONS.get(header.split(";")[0], "")
    if not extension:
        raise VideoError("photo must be PNG, JPEG, GIF, or WebP")
    try:
        return extension, base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise VideoError("photo is not valid base64")


def create_job(
    db,
    *,
    user_id: int,
    topic: str,
    format: str,
    duration_s: int,
    context: str = "",
    image_data_url: str = "",
    provider: str = "runway",
    orientation: str = "landscape",
    language: str = "en",
) -> dict:
    """Start a video lesson job. Everything reversible is validated before
    the kernel task exists, so failed requests leave no orphans."""
    clean_topic = (topic or "").strip()
    if not clean_topic:
        raise VideoError("topic must not be empty")
    if len(clean_topic) > tutor.MAX_TOPIC_LEN:
        raise VideoError("topic must be at most 500 characters")
    if format not in FORMATS:
        raise VideoError(f"format must be one of {', '.join(FORMATS)}")
    if duration_s not in DURATIONS[format]:
        allowed = ", ".join(f"{value}s" for value in DURATIONS[format])
        raise VideoError(f"a {format} lesson must be {allowed}")
    clean_context = (context or "").strip()
    if len(clean_context) > tutor.MAX_CONTEXT_LEN:
        raise VideoError("context must be at most 2000 characters")
    if orientation not in ORIENTATIONS:
        raise VideoError(f"orientation must be one of {', '.join(ORIENTATIONS)}")
    if orientation == "portrait" and format != "short":
        raise VideoError("portrait is only for quick shorts")
    clean_language = (language or "en").strip().lower()
    if clean_language not in ("en", "hi"):
        raise VideoError("language must be en or hi")
    extension, photo_bytes = _check_photo(image_data_url)
    footage = get_provider(provider)
    if footage.paid and not footage.configured:
        raise VideoError(
            "video generation needs RUNWAYML_API_SECRET in .env "
            "(create a key at dev.runwayml.com, add it, and restart the server)"
        )
    if provider == "local-demo" and not footage.configured:
        raise VideoError("local video needs ffmpeg (set FFMPEG_PATH or install it)")
    active = (
        db.query(VideoJob).filter(VideoJob.user_id == user_id, VideoJob.status.notin_(TERMINAL)).count()
    )
    if active >= MAX_ACTIVE_PER_USER:
        raise VideoError(f"only {MAX_ACTIVE_PER_USER} video jobs can run at once — wait or cancel one")
    today = utcnow()[:10]
    made_today = (
        db.query(VideoJob).filter(VideoJob.user_id == user_id, VideoJob.created_at.startswith(today)).count()
    )
    if made_today >= MAX_PER_DAY:
        raise VideoError(f"daily limit of {MAX_PER_DAY} video jobs reached")
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Video lesson ({format} {duration_s}s): {clean_topic[:120]}",
        department=tutor.TUTOR_DEPARTMENT,
        agent=VIDEO_AGENT,
        priority="normal",
    )
    kernel.transition_task(db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="video job started")
    if footage.paid:
        estimate = estimate_cost_usd(provider, duration_s)
        model = RUNWAY_MODEL if provider == "runway" else provider
        cost_note = (
            f"About ${estimate:.2f} for ~{duration_s}s of footage ({model} list price ${RUNWAY_USD_PER_SEC}/sec) "
            "+ narration at provider rates. Confirm to spend."
        )
    elif provider == "local-demo":
        cost_note = "No provider spend: local rendering is free; Muse planning/illustrations use API quota."
    else:
        cost_note = "No provider spend: fake provider (test footage, not a real video)."
    job = VideoJob(
        user_id=user_id,
        task_id=task.id,
        topic=clean_topic,
        format=format,
        duration_s=duration_s,
        status="PENDING",
        provider=provider,
        storyboard=json.dumps(
            {"context": clean_context, "photo": f"photo.{extension}" if extension else "", "orientation": orientation, "language": clean_language}
        ),
        scenes="[]",
        output_path="",
        error="",
        cost_note=cost_note,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(job)
    db.flush()
    directory = job_dir(job.id)
    os.makedirs(directory, exist_ok=True)
    if extension:
        with open(os.path.join(directory, f"photo.{extension}"), "wb") as handle:
            handle.write(photo_bytes)
    db.commit()
    db.refresh(job)
    return job_dict(job)


def _read_photo(job_id: int, name: str) -> str:
    """Rebuild the stored photo data URL, or "" when there is none."""
    if not name or "/" in name or "\\" in name:
        return ""
    path = os.path.join(job_dir(job_id), name)
    if not os.path.exists(path):
        return ""
    extension = name.rsplit(".", 1)[-1].lower()
    mime = "jpeg" if extension == "jpg" else extension
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def _do_plan(db, job: VideoJob) -> None:
    request = _load_json(job.storyboard, {})
    photo = _read_photo(job.id, request.get("photo", ""))
    lesson = tutor.plan_lesson(
        db,
        user_id=job.user_id,
        topic=job.topic,
        context=request.get("context", ""),
        image_data_url=photo,
        extra_guidance=VIDEO_SAFETY_GUIDANCE,
        language=request.get("language", "en"),
    )
    steps = lesson["steps"]
    total = job.duration_s
    count = len(steps)
    scenes = []
    start = 0
    for index, step in enumerate(steps):
        seconds = total // count + (1 if index < total % count else 0)
        scenes.append(
            {
                "index": index,
                "title": step["title"],
                "narration": step["text"],
                "visual": step["image_prompt"] or f"Simple flat illustration for: {step['title']}",
                "seconds": seconds,
                "start": start,
                "end": start + seconds,
                "keyframe": "",
                "provider_job_id": "",
                "submitted_at": "",
                "clip": "",
                "clip_source": "",
            }
        )
        start += seconds
    _validate_scenes(scenes)
    job.scenes = json.dumps(scenes)
    request["lesson_task_id"] = lesson["task_id"]
    request["model"] = lesson["model"]
    request["vision_used"] = lesson["vision_used"]
    job.storyboard = json.dumps(request)
    _set_status(db, job, "GENERATING_ASSETS", f"lesson planned ({count} scenes)")


def _do_assets(db, job: VideoJob) -> None:
    scenes = _load_json(job.scenes, [])
    pending = next((scene for scene in scenes if not scene.get("keyframe")), None)
    if pending is None:
        _set_status(db, job, "GENERATING_VIDEO", "keyframes complete")
        return
    made = tutor.illustrate(db, user_id=job.user_id, task_id=job.task_id, prompt=pending["visual"])
    try:
        encoded = made["image_data_url"].split(",", 1)[1]
        raw = base64.b64decode(encoded, validate=True)
    except (IndexError, binascii.Error, KeyError):
        raise VideoError("keyframe was not valid image data")
    name = f"keyframe_{pending['index']}.png"
    with open(os.path.join(job_dir(job.id), name), "wb") as handle:
        handle.write(raw)
    pending["keyframe"] = name
    job.scenes = json.dumps(scenes)
    if all(scene.get("keyframe") for scene in scenes):
        _set_status(db, job, "GENERATING_VIDEO", "keyframes complete")


def _do_clips(db, job: VideoJob, footage: VideoProvider) -> None:
    scenes = _load_json(job.scenes, [])
    if not scenes:
        raise VideoError("storyboard has no scenes")
    if getattr(footage, "renders_locally", False):
        request = _load_json(job.storyboard, {})
        orientation = request.get("orientation", "landscape")
        pending = next((scene for scene in scenes if not scene.get("clip")), None)
        if pending is None:
            _set_status(db, job, "ASSEMBLING", "scenes rendered")
            return
        keyframe = os.path.join(job_dir(job.id), pending.get("keyframe") or "")
        if not pending.get("keyframe") or not os.path.exists(keyframe):
            raise VideoError(f"scene {pending['index'] + 1} has no illustration to animate")
        with open(keyframe, "rb") as handle:
            image_bytes = handle.read()
        clip_bytes = footage.render_scene(
            image_bytes,
            seconds=pending["seconds"],
            index=pending["index"],
            orientation=orientation,
            title=pending["title"],
            is_last=pending["index"] == len(scenes) - 1,
        )
        name = f"clip_{pending['index']}.mp4"
        with open(os.path.join(job_dir(job.id), name), "wb") as handle:
            handle.write(clip_bytes)
        pending["clip"] = name
        pending["clip_source"] = "local"
        job.scenes = json.dumps(scenes)
        if all(scene.get("clip") for scene in scenes):
            _set_status(db, job, "ASSEMBLING", "scenes rendered")
        return
    now = parse_utc(utcnow())
    for scene in scenes:
        if scene.get("clip") or not scene.get("provider_job_id"):
            continue
        result = footage.poll(scene["provider_job_id"])
        if result["state"] == "ready":
            clip_bytes = footage.download(result["video_url"])
            if not clip_bytes:
                raise VideoError(f"scene {scene['index'] + 1}: provider returned an empty clip")
            name = f"clip_{scene['index']}.mp4"
            with open(os.path.join(job_dir(job.id), name), "wb") as handle:
                handle.write(clip_bytes)
            scene["clip"] = name
        elif result["state"] == "failed":
            raise VideoError(f"scene {scene['index'] + 1}: {result['error']}")
        else:
            age = (now - parse_utc(scene["submitted_at"])).total_seconds()
            if age > CLIP_TIMEOUT_S:
                raise VideoError(f"scene {scene['index'] + 1}: provider timed out after 30 minutes")
    waiting = next(
        (scene for scene in scenes if not scene.get("clip") and not scene.get("provider_job_id")), None
    )
    if waiting is not None:
        prompt = f"{waiting['title']}. {waiting['narration']}"[:500]
        request_s = min(max(waiting["seconds"], MIN_CLIP_S), MAX_CLIP_S)
        keyframe_path = os.path.join(job_dir(job.id), waiting.get("keyframe") or "")
        if waiting.get("keyframe") and os.path.exists(keyframe_path):
            with open(keyframe_path, "rb") as handle:
                keyframe_bytes = handle.read()
            provider_job_id = footage.submit_image_to_video(
                image_bytes=keyframe_bytes, prompt=prompt, duration_s=request_s
            )
            waiting["clip_source"] = "image"
        else:
            provider_job_id = footage.submit_text_to_video(prompt=prompt, duration_s=request_s)
            waiting["clip_source"] = "text"
        waiting["provider_job_id"] = provider_job_id
        waiting["submitted_at"] = utcnow()
        waiting["clip_seconds"] = request_s
    job.scenes = json.dumps(scenes)
    if scenes and all(scene.get("clip") for scene in scenes):
        _set_status(db, job, "ASSEMBLING", "clips complete")


def _audio_state(request: dict) -> dict:
    audio = request.get("audio") or {}
    audio.setdefault("jobs", {})
    audio.setdefault("attempts", 0)
    audio.setdefault("skipped", "")
    audio.setdefault("done", False)
    return audio


def _ensure_narration(db, job: VideoJob, footage: VideoProvider, request: dict) -> bool:
    """Return True when narration is settled (done or skipped with a
    record). Transient hiccups raise _RetryableError (pause, no attempt
    spent); hard failures spend attempts, then skip honestly."""
    audio = _audio_state(request)
    if audio["done"] or audio["skipped"]:
        return True
    if not footage.supports_audio:
        audio["skipped"] = f"{footage.name or 'provider'} has no narration; continuing silent"
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        return True
    scenes = _load_json(job.scenes, [])
    try:
        for scene in scenes:
            pending = audio["jobs"].get(str(scene["index"]))
            if not pending or scene.get("narration_file"):
                continue
            result = footage.poll(pending["id"])
            if result["state"] == "ready":
                data = footage.download_audio(result["video_url"])
                if not data:
                    raise VideoError(f"scene {scene['index'] + 1}: provider returned empty narration")
                name = f"narration_{scene['index']}.mp3"
                with open(os.path.join(job_dir(job.id), name), "wb") as handle:
                    handle.write(data)
                scene["narration_file"] = name
                del audio["jobs"][str(scene["index"])]
            elif result["state"] == "failed":
                raise VideoError(f"scene {scene['index'] + 1} narration: {result['error']}")
            else:
                age = (parse_utc(utcnow()) - parse_utc(pending["submitted_at"])).total_seconds()
                if age > CLIP_TIMEOUT_S:
                    raise VideoError(f"scene {scene['index'] + 1} narration timed out")
        waiting = next(
            (scene for scene in scenes if not scene.get("narration_file") and str(scene["index"]) not in audio["jobs"]),
            None,
        )
        if waiting is not None:
            audio["jobs"][str(waiting["index"])] = {
                "id": footage.submit_audio(text=waiting["narration"]),
                "submitted_at": utcnow(),
            }
    except _RetryableError:
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        job.scenes = json.dumps(scenes)
        raise
    except VideoError as exc:
        audio["attempts"] += 1
        if audio["attempts"] >= AUDIO_ATTEMPTS:
            audio["skipped"] = f"narration skipped after {AUDIO_ATTEMPTS} tries ({exc}); continuing silent"
            audio["jobs"] = {}
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        job.scenes = json.dumps(scenes)
        return bool(audio["skipped"])
    request["audio"] = audio
    job.storyboard = json.dumps(request)
    job.scenes = json.dumps(scenes)
    if scenes and all(scene.get("narration_file") for scene in scenes):
        audio["done"] = True
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        return True
    return False


def _ensure_local_narration(db, job: VideoJob, request: dict) -> bool:
    """Narrate every scene with the local speech engine. Missing voices
    or a failed read skips honestly — retrying cannot help."""
    audio = _audio_state(request)
    if audio["done"] or audio["skipped"]:
        return True
    voices = narration.available_voices()
    if not voices:
        audio["skipped"] = "local narration unavailable (no system voices found); continuing silent"
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        return True
    voice = narration.voice_for(request.get("language", "en"))
    if not voice:
        audio["skipped"] = "Hindi narration needs a Hindi system voice (none found); video is captioned instead"
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        return True
    scenes = _load_json(job.scenes, [])
    try:
        for scene in scenes:
            if scene.get("narration_file"):
                continue
            name = f"narration_{scene['index']}.wav"
            narration.speak_to_wav(scene["narration"], os.path.join(job_dir(job.id), name), voice=voice)
            scene["narration_file"] = name
    except narration.NarrationError as exc:
        audio["skipped"] = f"local narration failed ({exc}); continuing silent"
        request["audio"] = audio
        job.storyboard = json.dumps(request)
        job.scenes = json.dumps(scenes)
        return True
    audio["done"] = True
    audio["voice"] = voice
    request["audio"] = audio
    job.storyboard = json.dumps(request)
    job.scenes = json.dumps(scenes)
    return True


def _run_ffmpeg(args: list, *, directory: str, timeout: int, label: str) -> None:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=directory)
    except subprocess.TimeoutExpired:
        raise VideoError(f"{label} timed out")
    if completed.returncode != 0:
        raise VideoError(f"{label} failed: {completed.stderr[-500:]}")


def _mux_lesson(job: VideoJob, scenes: list, ffmpeg: str, orientation: str) -> None:
    """Normalize every clip (720p or 720x1280, 30fps, H.264), fit
    narration to the real clip lengths, mux to lesson.mp4. Records
    actual durations."""
    width, height = (720, 1280) if orientation == "portrait" else (1280, 720)
    directory = job_dir(job.id)
    actuals = []
    for scene in scenes:
        streams = ffprobe_streams(os.path.join(directory, scene["clip"]))
        actuals.append(streams["duration"])
        _run_ffmpeg(
            [
                ffmpeg, "-y", "-i", scene["clip"],
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                f"norm_{scene['index']}.mp4",
            ],
            directory=directory,
            timeout=300,
            label=f"scene {scene['index'] + 1} normalization",
        )
    inputs: list = []
    filters: list = []
    for position, scene in enumerate(scenes):
        actual = actuals[position]
        inputs += ["-i", f"norm_{scene['index']}.mp4"]
        narration = scene.get("narration_file", "")
        narration_path = os.path.join(directory, narration) if narration else ""
        if narration and os.path.exists(narration_path):
            inputs += ["-i", narration]
            filters.append(
                f"[{2 * position + 1}:a]atrim=0:{actual:.3f},apad,atrim=0:{actual:.3f},"
                f"aformat=sample_rates=44100:channel_layouts=stereo[a{position}]"
            )
        else:
            inputs += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={actual:.3f}"]
            filters.append(f"[{2 * position + 1}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{position}]")
    filters.append("".join(f"[{2 * p}:v]" for p in range(len(scenes))) + f"concat=n={len(scenes)}:v=1:a=0[v]")
    filters.append("".join(f"[a{p}]" for p in range(len(scenes))) + f"concat=n={len(scenes)}:v=0:a=1[a]")
    _run_ffmpeg(
        [
            ffmpeg, "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "lesson.mp4",
        ],
        directory=directory,
        timeout=600,
        label="final assembly",
    )
    start = 0.0
    for scene, actual in zip(scenes, actuals):
        scene["actual_seconds"] = actual
        scene["start"] = round(start, 3)
        start += actual
        scene["end"] = round(start, 3)
    job.scenes = json.dumps(scenes)
    job.output_path = "lesson.mp4"


def _do_assemble(db, job: VideoJob) -> None:
    scenes = _load_json(job.scenes, [])
    if not scenes:
        raise VideoError("storyboard has no scenes")
    if any(not scene.get("clip") for scene in scenes):
        raise VideoError("scene clips are missing")
    if len(scenes) > 1 and ffmpeg_path() is None:
        raise VideoError(
            f"joining {len(scenes)} scenes needs ffmpeg, which is not installed — "
            "install ffmpeg to enable multi-scene assembly"
        )
    footage = get_provider(job.provider)
    request = _load_json(job.storyboard, {})
    if job.provider == "local-demo":
        settled = _ensure_local_narration(db, job, request)
    else:
        settled = _ensure_narration(db, job, footage, request)
    if not settled:
        return
    audio = _audio_state(request)
    if audio["skipped"]:
        request["narration_note"] = audio["skipped"]
    elif audio.get("voice"):
        request["narration_note"] = f"narrated ({audio['voice']})"
    elif audio["done"]:
        request["narration_note"] = "narrated"
    job.storyboard = json.dumps(request)
    ffmpeg = ffmpeg_path()
    directory = job_dir(job.id)
    if ffmpeg is None:
        clip = os.path.join(directory, scenes[0]["clip"])
        if not os.path.exists(clip):
            raise VideoError("verification failed: scene clip went missing from storage")
        shutil.copyfile(clip, os.path.join(directory, "lesson.mp4"))
        job.output_path = "lesson.mp4"
        request["narration_note"] = "silent: assembly needs ffmpeg for narration"
        job.storyboard = json.dumps(request)
        _set_status(db, job, "VERIFYING", "single clip stored directly (no assembly needed)")
        return
    _mux_lesson(job, scenes, ffmpeg, request.get("orientation", "landscape"))
    _set_status(db, job, "VERIFYING", f"assembled {len(scenes)} scenes")


def _do_verify(db, job: VideoJob) -> None:
    scenes = _load_json(job.scenes, [])
    directory = job_dir(job.id)
    path = os.path.join(directory, job.output_path or "lesson.mp4")
    request = _load_json(job.storyboard, {})
    if ffprobe_path() is None:
        info = verify_video_file(path)
        has_audio = "unknown"
        evidence_media = f"{info['bytes']} bytes (magic-checked only; ffprobe missing)"
    else:
        streams = ffprobe_streams(path, label="the final video")
        if streams["width"] <= 0 or streams["height"] <= 0:
            raise VideoError("verification failed: video has no valid dimensions")
        has_audio = streams["has_audio"]
        evidence_media = (
            f"{streams['width']}x{streams['height']} {streams['vcodec']} "
            f"{streams['duration']:.1f}s audio={has_audio}"
        )
    if any(scene.get("actual_seconds") for scene in scenes):
        start = 0.0
        for scene in scenes:
            actual = scene.get("actual_seconds") or scene.get("seconds", 0)
            scene["start"] = round(start, 3)
            start += actual
            scene["end"] = round(start, 3)
        job.scenes = json.dumps(scenes)
    with open(os.path.join(directory, "captions.srt"), "w", encoding="utf-8") as handle:
        handle.write(build_captions(scenes))
    transcript = "\n\n".join(f"{scene['title']}\n{scene['narration']}" for scene in scenes)
    with open(os.path.join(directory, "transcript.txt"), "w", encoding="utf-8") as handle:
        handle.write(transcript + "\n")
    request["has_audio"] = has_audio
    job.storyboard = json.dumps(request)
    job.status = "COMPLETED"
    job.updated_at = utcnow()
    kernel.transition_task(
        db,
        user_id=job.user_id,
        task_id=job.task_id,
        to_status="COMPLETED",
        note="video lesson ready",
        result=f"video ready: {len(scenes)} scene(s), {evidence_media}",
        evidence=f"provider={job.provider} model={request.get('model', '')} scenes={len(scenes)} narration={request.get('narration_note', '')}",
    )
    db.add(
        Notification(
            user_id=job.user_id,
            task_id=job.task_id,
            kind="video_ready",
            text=f"Your video lesson '{job.topic[:80]}' is ready.",
            created_at=utcnow(),
            read_at="",
        )
    )


def _advance(db, job: VideoJob) -> None:
    footage = get_provider(job.provider)
    if job.status == "PENDING":
        if footage.paid:
            _set_status(db, job, "PENDING_APPROVAL", "waiting for cost confirmation")
        else:
            _set_status(db, job, "PLANNING", "planning started")
    elif job.status == "PENDING_APPROVAL":
        pass
    elif job.status == "PLANNING":
        _do_plan(db, job)
    elif job.status == "GENERATING_ASSETS":
        _do_assets(db, job)
    elif job.status == "GENERATING_VIDEO":
        _do_clips(db, job, footage)
    elif job.status == "ASSEMBLING":
        _do_assemble(db, job)
    elif job.status == "VERIFYING":
        _do_verify(db, job)


def advance_job(db, job_id: int) -> dict:
    """Run one pipeline step. Job problems fail the job honestly; a missing
    job raises. Never leaves the row half-written."""
    job = db.query(VideoJob).filter_by(id=job_id).first()
    if job is None:
        raise kernel.TaskNotFoundError("video job not found")
    if job.status in TERMINAL:
        return job_dict(job)
    try:
        _advance(db, job)
    except _RetryableError:
        db.commit()
        db.refresh(job)
        return job_dict(job)
    except Exception as exc:
        db.rollback()
        job = db.query(VideoJob).filter_by(id=job_id).first()
        message = str(exc).strip() or type(exc).__name__
        _fail(db, job, message)
    db.commit()
    db.refresh(job)
    return job_dict(job)


def advance_due_jobs(db) -> dict:
    """Advance every unfinished job once (what the worker tick calls)."""
    rows = db.query(VideoJob).filter(VideoJob.status.notin_(TERMINAL)).order_by(VideoJob.id).all()
    advanced = 0
    errors = []
    for row in rows:
        try:
            advance_job(db, row.id)
            advanced += 1
        except Exception as exc:
            db.rollback()
            errors.append({"job_id": row.id, "error": str(exc)})
    return {"advanced": advanced, "errors": errors}


def confirm_job(db, *, user_id: int, job_id: int, confirm: bool) -> dict:
    """Answer the cost gate: True plans the lesson, False cancels the job."""
    job = _get_job(db, user_id, job_id)
    if job.status != "PENDING_APPROVAL":
        raise VideoError("job is not waiting for confirmation")
    if not confirm:
        job.status = "CANCELLED"
        job.updated_at = utcnow()
        kernel.transition_task(
            db, user_id=user_id, task_id=job.task_id, to_status="FAILED", note="cost declined", result="cancelled by user"
        )
    else:
        _set_status(db, job, "PLANNING", "cost confirmed")
    db.commit()
    db.refresh(job)
    return job_dict(job)


def cancel_job(db, *, user_id: int, job_id: int) -> dict:
    """Cancel an unfinished job. Provider cancellations are best-effort."""
    job = _get_job(db, user_id, job_id)
    if job.status in TERMINAL:
        raise VideoError(f"job is already {job.status.lower()}")
    try:
        footage = get_provider(job.provider)
    except VideoError:
        footage = None
    if footage is not None:
        for scene in _load_json(job.scenes, []):
            if scene.get("provider_job_id") and not scene.get("clip"):
                try:
                    footage.cancel(scene["provider_job_id"])
                except Exception:
                    pass
    job.status = "CANCELLED"
    job.updated_at = utcnow()
    kernel.transition_task(
        db, user_id=user_id, task_id=job.task_id, to_status="FAILED", note="cancelled by user", result="cancelled by user"
    )
    db.commit()
    db.refresh(job)
    return job_dict(job)


def delete_job(db, *, user_id: int, job_id: int) -> dict:
    """Delete a finished job and its files. The kernel task keeps the audit."""
    job = _get_job(db, user_id, job_id)
    if job.status not in TERMINAL:
        raise VideoError("only finished jobs can be deleted")
    shutil.rmtree(job_dir(job.id), ignore_errors=True)
    kernel.log_event(db, user_id=user_id, task_id=job.task_id, event_type="video_stage", note="video assets deleted")
    db.delete(job)
    db.commit()
    return {"deleted": job_id}


def list_jobs(db, *, user_id: int) -> list:
    rows = db.query(VideoJob).filter_by(user_id=user_id).order_by(VideoJob.id.desc()).all()
    return [job_dict(row) for row in rows]


def file_path(db, *, user_id: int, job_id: int) -> str:
    """Absolute MP4 path after ownership + readiness checks."""
    job = _get_job(db, user_id, job_id)
    if job.status != "COMPLETED" or not job.output_path:
        raise VideoError("video is not ready yet")
    path = os.path.join(job_dir(job.id), job.output_path)
    if not os.path.exists(path):
        raise VideoError("video file is missing from storage")
    return path


ASK_PROMPT = """You are Saath's Visual Tutor answering a follow-up question about a video lesson you planned. Answer from the lesson below only; say plainly when the question goes beyond it. Keep it short, plain, and step-shaped. Never invent product buttons or unsafe instructions."""

MAX_ASK_LEN = 1000


def ask_about_lesson(db, *, user_id: int, job_id: int, question: str, scene_index=None) -> dict:
    """Answer one follow-up question from the job's own lesson plan."""
    job = _get_job(db, user_id, job_id)
    scenes = _load_json(job.scenes, [])
    if not scenes:
        raise VideoError("lesson is not planned yet")
    clean = (question or "").strip()
    if not clean:
        raise VideoError("question must not be empty")
    if len(clean) > MAX_ASK_LEN:
        raise VideoError("question must be at most 1000 characters")
    if scene_index is not None:
        if not isinstance(scene_index, int) or not 0 <= scene_index < len(scenes):
            raise VideoError("scene is out of range")
        picked = [scenes[scene_index]]
    else:
        picked = scenes
    context = "\n\n".join(f"Step {scene['index'] + 1}: {scene['title']}\n{scene['narration']}" for scene in picked)
    client = model_client.get_model_client()
    prompt = f"Lesson: {job.topic}\n\n{context}\n\nQuestion: {clean}"
    try:
        try:
            response = client.generate(
                system=ASK_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                max_output_tokens=1500,
            )
        except model_client.ModelError as exc:
            if "max_output_tokens" not in str(exc):
                raise
            response = client.generate(
                system=ASK_PROMPT,
                messages=[{"role": "user", "content": prompt + "\n\nAnswer in at most 3 short sentences."}],
                max_output_tokens=1500,
            )
    except Exception as exc:
        raise VideoError(f"the model could not answer: {exc}")
    kernel.log_event(db, user_id=user_id, task_id=job.task_id, event_type="video_stage", note="lesson question answered")
    db.commit()
    return {"answer": response.text, "model": response.model, "scene": scene_index, "task_id": job.task_id}


def keyframes_data(db, *, user_id: int, job_id: int) -> dict:
    """Scene keyframes as data URLs (owner only). Missing files are skipped,
    never faked."""
    job = _get_job(db, user_id, job_id)
    scenes = _load_json(job.scenes, [])
    directory = job_dir(job.id)
    frames = []
    for scene in scenes:
        name = scene.get("keyframe", "")
        if not name:
            continue
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        frames.append(
            {"index": scene["index"], "title": scene.get("title", ""), "image_data_url": f"data:image/png;base64,{encoded}"}
        )
    return {"keyframes": frames}


def captions_data(db, *, user_id: int, job_id: int) -> dict:
    """Captions + transcript after ownership + readiness checks."""
    job = _get_job(db, user_id, job_id)
    if job.status != "COMPLETED":
        raise VideoError("video is not ready yet")
    directory = job_dir(job.id)
    try:
        with open(os.path.join(directory, "captions.srt"), "r", encoding="utf-8") as handle:
            captions = handle.read()
        with open(os.path.join(directory, "transcript.txt"), "r", encoding="utf-8") as handle:
            transcript = handle.read()
    except OSError:
        raise VideoError("captions are missing from storage")
    return {"format": "srt", "captions": captions, "transcript": transcript}
