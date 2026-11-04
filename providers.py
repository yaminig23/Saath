"""Provider adapters (P6 operator).

Every external service sits behind an adapter with an honest status:
connected (verified by a live check), available (usable without proof
yet — key present or keyless), or missing (with the exact need). An
adapter never claims a connection it has not verified.
"""

import shutil

import httpx

import demo_bookings
import kernel
from config import settings

CHECK_TIMEOUT = 10.0
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class ProviderError(kernel.KernelError):
    """A provider is missing, unreachable, or misbehaving."""


class Provider:
    """One integration: honest status plus an explicit live check."""

    name = ""
    title = ""
    capabilities = []

    def status(self) -> dict:
        raise NotImplementedError

    def check(self) -> dict:
        return {"ok": False, "detail": "no live check available"}


class LocalProvider(Provider):
    """Built-in Saath machinery: always connected."""

    def __init__(self, name: str, title: str, capabilities: list) -> None:
        self.name = name
        self.title = title
        self.capabilities = capabilities

    def status(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "capabilities": self.capabilities,
            "status": "connected",
            "detail": "built into Saath",
        }

    def check(self) -> dict:
        return {"ok": True, "detail": "built in — always available"}


class ModelTextProvider(Provider):
    """Muse Spark text: planning, chat, lessons."""

    name = "muse-text"
    title = "Muse Spark (text)"
    capabilities = ["chat", "planning", "lessons"]

    def status(self) -> dict:
        if settings.meta_api_key.strip():
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "available", "detail": "key present; verified by the Check button",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "missing", "detail": "needs META_API_KEY in .env",
        }

    def check(self) -> dict:
        if not settings.meta_api_key.strip():
            return {"ok": False, "detail": "needs META_API_KEY in .env"}
        # Local import: model_client never imports providers, so no cycle.
        from model_client import get_model_client

        try:
            # 200 tokens, not 20: reasoning models count thinking toward
            # the cap, and a cramped budget returns "incomplete" (M10).
            response = get_model_client().generate(
                system="Reply with the word OK.",
                messages=[{"role": "user", "content": "OK?"}],
                max_output_tokens=200,
            )
        except Exception as exc:
            return {"ok": False, "detail": f"live check failed: {exc}"}
        return {"ok": True, "detail": f"live reply ({response.input_tokens}/{response.output_tokens} tokens)"}


class ModelImageProvider(Provider):
    """Muse Image stills: generated only on explicit request."""

    name = "muse-image"
    title = "Muse Image (stills)"
    capabilities = ["illustrations"]

    def status(self) -> dict:
        if settings.meta_api_key.strip():
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "available", "detail": "key present; generates only on explicit request",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "missing", "detail": "needs META_API_KEY in .env",
        }

    def check(self) -> dict:
        if not settings.meta_api_key.strip():
            return {"ok": False, "detail": "needs META_API_KEY in .env"}
        return {"ok": True, "detail": "key present; generates only on explicit request (no cheap live check)"}


def _wmo_text(code: int) -> str:
    """Plain words for a WMO weather code."""
    if code == 0:
        return "clear sky"
    if code in (1, 2):
        return "partly cloudy"
    if code == 3:
        return "overcast"
    if code in (45, 48):
        return "foggy"
    if code in (51, 53, 55):
        return "drizzle"
    if code in (56, 57):
        return "freezing drizzle"
    if code in (61, 63, 65):
        return "rain"
    if code in (66, 67):
        return "freezing rain"
    if code in (71, 73, 75, 77):
        return "snow"
    if code in (80, 81, 82):
        return "showers"
    if code in (95, 96, 99):
        return "thunderstorm"
    return f"condition code {code}"


class WeatherProvider(Provider):
    """Real weather via Open-Meteo (free, keyless)."""

    name = "weather"
    title = "Weather (Open-Meteo)"
    capabilities = ["current", "today"]

    def status(self) -> dict:
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available", "detail": "free keyless API; verified by the Check button",
        }

    def now(self, place: str) -> dict:
        """Current weather plus today's range for a place name."""
        clean = (place or "").strip()
        if not clean:
            raise ProviderError("place must not be empty")
        try:
            geo = httpx.get(GEOCODE_URL, params={"name": clean, "count": 1}, timeout=CHECK_TIMEOUT)
        except httpx.HTTPError as exc:
            raise ProviderError(f"geocoding failed: {exc}")
        if geo.status_code != 200:
            raise ProviderError(f"geocoding error {geo.status_code}")
        try:
            results = geo.json().get("results") or []
        except ValueError:
            raise ProviderError("geocoding returned invalid JSON")
        if not results:
            raise ProviderError(f"unknown place {clean!r}")
        latitude, longitude = results[0]["latitude"], results[0]["longitude"]
        label = results[0].get("name", clean)
        try:
            forecast = httpx.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
                timeout=CHECK_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"forecast failed: {exc}")
        if forecast.status_code != 200:
            raise ProviderError(f"forecast error {forecast.status_code}")
        try:
            payload = forecast.json()
            current = payload["current"]
            daily = payload["daily"]
            temp = float(current["temperature_2m"])
            code = int(current["weather_code"])
            high = float(daily["temperature_2m_max"][0])
            low = float(daily["temperature_2m_min"][0])
            rain = daily["precipitation_probability_max"][0]
            rain_pct = int(rain) if rain is not None else 0
        except (ValueError, KeyError, TypeError, IndexError):
            raise ProviderError("forecast returned an unexpected shape")
        return {
            "place": label,
            "temp_c": round(temp, 1),
            "code": code,
            "summary": _wmo_text(code),
            "high_c": round(high, 1),
            "low_c": round(low, 1),
            "rain_pct": rain_pct,
        }

    def check(self) -> dict:
        try:
            sky = self.now("London")
        except Exception as exc:
            return {"ok": False, "detail": f"live check failed: {exc}"}
        return {"ok": True, "detail": f"{sky['temp_c']}°C in {sky['place']} ({sky['summary']})"}


class CalendarProviderStatus(Provider):
    """Google Calendar: app keys plus each user's own OAuth link."""

    name = "calendar"
    title = "Google Calendar"
    capabilities = ["read events", "write events"]

    def status(self) -> dict:
        if not (settings.gcal_client_id.strip() and settings.gcal_client_secret.strip()):
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "missing",
                "detail": "needs GOOGLE_CALENDAR_CLIENT_ID + _SECRET in .env (Google Cloud Console, Desktop-app client)",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available", "detail": "app keys present — each user connects their own Google account",
        }

    def check(self) -> dict:
        return {"ok": False, "detail": "calendar checks per user — press Check while logged in"}


class MapsProviderStatus(Provider):
    """Google Maps: billable key for live places and routes."""

    name = "maps"
    title = "Google Maps"
    capabilities = ["search places", "routes"]

    def status(self) -> dict:
        if not settings.maps_api_key.strip():
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "missing",
                "detail": "needs GOOGLE_MAPS_API_KEY in .env (Cloud Console key restricted to Places + Routes APIs)",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available", "detail": "key present; verified by the Check button (spends one tiny request)",
        }

    def check(self) -> dict:
        # Local import: maps never imports providers, so no cycle.
        import maps

        try:
            found = maps.search_places(query="Eiffel Tower")
        except Exception as exc:
            return {"ok": False, "detail": f"live check failed: {exc}"}
        names = [place["name"] for place in found["places"][:2]]
        return {"ok": True, "detail": f"live places work ({', '.join(names) or 'no results'}; one request spent)"}


class DuffelProviderStatus(Provider):
    """Duffel flights: test token for the real search-to-booking loop."""

    name = "duffel"
    title = "Duffel flights"
    capabilities = ["search offers", "book (test)"]

    def status(self) -> dict:
        if not settings.duffel_api_key.strip():
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "missing",
                "detail": "needs DUFFEL_API_KEY in .env (free test token from duffel.com)",
            }
        mode = "test" if settings.duffel_api_key.strip().startswith("duffel_test_") else "LIVE"
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available",
            "detail": f"key present ({mode} mode); verified by the Check button",
        }

    def check(self) -> dict:
        # Local import: duffel never imports providers, so no cycle.
        import duffel

        if not settings.duffel_api_key.strip().startswith("duffel_test_"):
            return {"ok": False, "detail": "check runs on test keys only (live searches can bill)"}
        try:
            found = duffel.search_flights(origin="BOM", destination="DEL", date="2026-12-15")
        except Exception as exc:
            return {"ok": False, "detail": f"live check failed: {exc}"}
        return {"ok": True, "detail": f"live search works ({len(found['offers'])} test offers)"}


class GmailProviderStatus(Provider):
    """Gmail: app keys plus each user's own send-only OAuth link."""

    name = "gmail"
    title = "Gmail (send only)"
    capabilities = ["send"]

    def status(self) -> dict:
        if not (settings.gcal_client_id.strip() and settings.gcal_client_secret.strip()):
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "missing",
                "detail": "needs GOOGLE_CALENDAR_CLIENT_ID + _SECRET in .env (same Google Cloud client as calendar)",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available", "detail": "app keys present — each user connects their own Gmail send grant",
        }

    def check(self) -> dict:
        return {"ok": False, "detail": "gmail checks per user — press Check while logged in"}


class VideoProviderStatus(Provider):
    """Video generation: Runway key plus ffmpeg, each reported honestly."""

    name = "video"
    title = "Video generation"
    capabilities = ["tutorials"]

    def status(self) -> dict:
        if not settings.runway_api_secret.strip():
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "missing", "detail": "needs RUNWAYML_API_SECRET in .env (create a key at dev.runwayml.com)",
            }
        if shutil.which("ffmpeg") is None:
            return {
                "name": self.name, "title": self.title, "capabilities": self.capabilities,
                "status": "available",
                "detail": "Runway key present; single-clip shorts can render, multi-scene assembly needs ffmpeg",
            }
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "available", "detail": "Runway key present and ffmpeg installed",
        }

    def check(self) -> dict:
        if not settings.runway_api_secret.strip():
            return {"ok": False, "detail": "needs RUNWAYML_API_SECRET in .env"}
        return {"ok": True, "detail": "key present; live render not attempted (spends money)"}


class MissingProvider(Provider):
    """An integration interface with no connection. Never claims more."""

    def __init__(self, name: str, title: str, capabilities: list, needs: str) -> None:
        self.name = name
        self.title = title
        self.capabilities = capabilities
        self.needs = needs

    def status(self) -> dict:
        return {
            "name": self.name, "title": self.title, "capabilities": self.capabilities,
            "status": "missing", "detail": self.needs,
        }

    def check(self) -> dict:
        return {"ok": False, "detail": self.needs}


REGISTRY: list = [
    LocalProvider("saath-core", "Saath core", ["tasks", "reminders", "memory", "approvals", "worker"]),
    ModelTextProvider(),
    ModelImageProvider(),
    WeatherProvider(),
    CalendarProviderStatus(),
    demo_bookings.DemoBookingsProvider(),
    MapsProviderStatus(),
    DuffelProviderStatus(),
    GmailProviderStatus(),
    VideoProviderStatus(),
    MissingProvider("tts", "Voice synthesis", ["narration"], "needs a TTS provider key (Meta offers transcription only; Runway has a speech endpoint for later)"),
]


def list_providers() -> list:
    """Every adapter with its honest status. No network calls."""
    return [provider.status() for provider in REGISTRY]


def get_provider(name: str):
    """One adapter by name, or None."""
    for provider in REGISTRY:
        if provider.name == name:
            return provider
    return None


def weather_now(place: str) -> dict:
    """Current weather for a place (live Open-Meteo call)."""
    for provider in REGISTRY:
        if provider.name == "weather":
            return provider.now(place)
    raise ProviderError("weather provider is not registered")
