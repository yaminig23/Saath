"""Project settings for the Saath AI brain.

Milestone 0: every value comes from an environment variable so secrets never
live in source code. A tiny `.env` file reader is included so local values
can live in one place without installing anything extra.
"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from `.env` without overriding real variables."""
    path = os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value


_load_dotenv()


class Settings:
    """Settings loaded once when the program starts."""

    def __init__(self) -> None:
        self.app_env = os.environ.get("APP_ENV", "dev")
        self.database_url = os.environ.get("DATABASE_URL", "sqlite:///./saath.db")
        self.meta_api_key = os.environ.get("META_API_KEY", "")
        self.model_id = os.environ.get("MODEL_ID", "muse-spark-1.3")
        self.model_mode = os.environ.get("MODEL_MODE", "standard")
        try:
            self.session_ttl_hours = int(os.environ.get("SESSION_TTL_HOURS", "24"))
        except ValueError:
            self.session_ttl_hours = 24
        try:
            self.approval_ttl_hours = int(os.environ.get("APPROVAL_TTL_HOURS", "24"))
        except ValueError:
            self.approval_ttl_hours = 24
        self.outbox_dir = os.environ.get("OUTBOX_DIR", "outbox")
        self.runway_api_secret = os.environ.get("RUNWAYML_API_SECRET", "")
        self.media_dir = os.environ.get("MEDIA_DIR", "media")
        self.ffmpeg_path = os.environ.get("FFMPEG_PATH", "")
        self.ffprobe_path = os.environ.get("FFPROBE_PATH", "")
        self.gcal_client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "")
        self.gcal_client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "")
        self.duffel_api_key = os.environ.get("DUFFEL_API_KEY", "")
        self.duffel_env = os.environ.get("DUFFEL_ENV", "test")
        self.maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        try:
            self.maps_daily_cap = int(os.environ.get("GOOGLE_MAPS_DAILY_CAP", "200"))
        except ValueError:
            self.maps_daily_cap = 200
        if self.maps_daily_cap <= 0:
            self.maps_daily_cap = 200
        raw_auto = os.environ.get("AUTO_APPROVE_TOOLS", "")
        self.auto_approve_tools = [name.strip() for name in raw_auto.split(",") if name.strip()]
        self.worker_enabled = os.environ.get("WORKER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
        try:
            self.worker_interval_seconds = int(os.environ.get("WORKER_INTERVAL_SECONDS", "60"))
        except ValueError:
            self.worker_interval_seconds = 60
        if self.worker_interval_seconds <= 0:
            self.worker_interval_seconds = 60
        if "contributor" in self.model_id.lower():
            raise ValueError("MODEL_ID must never be a Contributor-tier ID (training risk); use a Standard ID")
        if self.model_mode.lower() != "standard":
            raise ValueError("MODEL_MODE must be 'standard' (other tiers may train on your data)")


settings = Settings()
