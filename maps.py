"""Google Maps adapter: live places and routes (third real integration).

Two read-only calls behind one spend guard: Places text search for
"dentists near X", and Directions for "how far is the airport". Both
are harmless tools (reads, like weather) — but every call bills
fractions of a cent, so the adapter enforces a local daily cap
(GOOGLE_MAPS_DAILY_CAP) on top of the Cloud Console quotas, which
stay authoritative. Google's status field is mapped to plain
MapsError text; a denied key or an empty result never becomes fake
listings.
"""

import re
from datetime import datetime

import httpx

import kernel
import tools
from config import settings

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = "places.displayName,places.formattedAddress,places.rating,places.currentOpeningHours.openNow,places.types"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.legs.steps.navigationInstruction"

HTTP_TIMEOUT = 30.0
MAX_QUERY_LEN = 200
MAX_PLACES = 5
MODES = ("driving", "walking", "bicycling", "transit")

# {"day": "YYYY-MM-DD", "count": n}. In-memory by design: the Cloud
# Console quota is the real guard; this just stops runaway loops fast.
_spend: dict = {}


class MapsError(kernel.KernelError):
    """Maps refused, is unreachable, or the spend cap is reached."""


def key_configured() -> bool:
    """True when the billable API key is present."""
    return bool(settings.maps_api_key.strip())


def _spend_one() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if _spend.get("day") != today:
        _spend.clear()
        _spend.update({"day": today, "count": 0})
    if _spend["count"] >= settings.maps_daily_cap:
        raise MapsError(
            f"today's Maps budget ({settings.maps_daily_cap} requests) is spent — "
            "raise GOOGLE_MAPS_DAILY_CAP or wait for tomorrow"
        )
    _spend["count"] += 1


def _require_key() -> str:
    key = settings.maps_api_key.strip()
    if not key:
        raise MapsError(
            "live maps need GOOGLE_MAPS_API_KEY in .env "
            "(Cloud Console: Places API + Directions API, key restricted to those two)"
        )
    return key


def _google_error(payload: dict) -> str:
    detail = (payload.get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
    return str(detail or "").strip()[:300]


def search_places(*, query: str, near: str = "") -> dict:
    """Up to 5 real places for a query like "dentists near Andheri",
    via Places API (New). Costs one request (fractions of a cent)."""
    clean = (query or "").strip()
    if not clean:
        raise MapsError("query must not be empty")
    if len(clean) > MAX_QUERY_LEN:
        raise MapsError("query must be at most 200 characters")
    area = (near or "").strip()
    if len(area) > MAX_QUERY_LEN:
        raise MapsError("near must be at most 200 characters")
    full = f"{clean} near {area}" if area else clean
    key = _require_key()
    _spend_one()
    try:
        response = httpx.post(
            PLACES_URL,
            json={"textQuery": full, "maxResultCount": MAX_PLACES},
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELD_MASK},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise MapsError(f"place search failed: {exc}")
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if response.status_code != 200:
        detail = _google_error(payload) or f"HTTP {response.status_code}"
        if response.status_code == 403:
            raise MapsError(f"place search refused: Google rejected the key ({detail})")
        if response.status_code == 429:
            raise MapsError("place search refused: Maps quota exceeded — check the Cloud Console budget")
        raise MapsError(f"place search refused: {detail}")
    places = []
    for item in (payload.get("places") or [])[:MAX_PLACES]:
        if not isinstance(item, dict):
            continue
        name = item.get("displayName") or {}
        opening = item.get("currentOpeningHours") or {}
        places.append(
            {
                "name": str(name.get("text", "") if isinstance(name, dict) else "") or "(unnamed)",
                "address": str(item.get("formattedAddress", "") or ""),
                "rating": item.get("rating"),
                "open_now": opening.get("openNow"),
                "kinds": [kind for kind in (item.get("types") or []) if isinstance(kind, str)][:4],
            }
        )
    return {"query": full, "live": True, "places": places}


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(html: str) -> str:
    return _TAG_RE.sub("", html or "").replace("&nbsp;", " ").strip()


_TRAVEL_MODES = {"driving": "DRIVE", "walking": "WALK", "bicycling": "BICYCLE", "transit": "TRANSIT"}


def _human_distance(meters) -> str:
    try:
        value = float(meters)
    except (TypeError, ValueError):
        return ""
    if value < 1000:
        return f"{int(round(value))} m"
    return f"{value / 1000:.1f} km"


def _human_duration(seconds_text) -> str:
    try:
        total = int(str(seconds_text or "").rstrip("s"))
    except ValueError:
        return ""
    minutes = max(1, round(total / 60))
    if minutes < 60:
        return f"{minutes} mins"
    return f"{minutes // 60} hr {minutes % 60:02d} mins"


def route(*, origin: str, destination: str, mode: str = "driving") -> dict:
    """Real distance + duration plus the first few plain-text steps,
    via the Routes API. Costs one request (fractions of a cent)."""
    clean_origin = (origin or "").strip()
    clean_destination = (destination or "").strip()
    if not clean_origin or not clean_destination:
        raise MapsError("origin and destination must not be empty")
    if len(clean_origin) > MAX_QUERY_LEN or len(clean_destination) > MAX_QUERY_LEN:
        raise MapsError("origin and destination must be at most 200 characters")
    clean_mode = (mode or "driving").strip().lower()
    if clean_mode not in MODES:
        raise MapsError(f"mode must be one of {', '.join(MODES)}")
    key = _require_key()
    _spend_one()
    try:
        response = httpx.post(
            ROUTES_URL,
            json={
                "origin": {"address": clean_origin},
                "destination": {"address": clean_destination},
                "travelMode": _TRAVEL_MODES[clean_mode],
                "computeAlternativeRoutes": False,
                "languageCode": "en",
            },
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": ROUTES_FIELD_MASK},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise MapsError(f"route failed: {exc}")
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if response.status_code != 200:
        detail = (payload.get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
        detail = str(detail or f"HTTP {response.status_code}").strip()[:300]
        if response.status_code == 403:
            raise MapsError(f"route refused: Google rejected the key ({detail})")
        if response.status_code == 429:
            raise MapsError("route refused: Maps quota exceeded — check the Cloud Console budget")
        raise MapsError(f"route refused: {detail}")
    routes = payload.get("routes") or []
    best = routes[0] if routes and isinstance(routes[0], dict) else None
    if best is None:
        return {
            "origin": clean_origin, "destination": clean_destination, "mode": clean_mode,
            "live": True, "distance": "", "duration": "", "steps": [],
            "detail": "Google found no route between those places",
        }
    steps = []
    legs = best.get("legs") or []
    for leg in legs[:1]:
        for step in ((leg or {}).get("steps") or [])[:6]:
            instructions = ((step or {}).get("navigationInstruction") or {}).get("instructions", "")
            text = _plain(str(instructions or ""))
            if text:
                steps.append(text)
    return {
        "origin": clean_origin, "destination": clean_destination, "mode": clean_mode,
        "live": True, "distance": _human_distance(best.get("distanceMeters")),
        "duration": _human_duration(best.get("duration")), "steps": steps,
    }


def _tool_search(db, *, user_id, task, args) -> dict:
    args = args or {}
    return search_places(query=args.get("query", ""), near=args.get("near", ""))


def _tool_route(db, *, user_id, task, args) -> dict:
    args = args or {}
    return route(origin=args.get("origin", ""), destination=args.get("destination", ""), mode=args.get("mode", "driving"))


tools.register(
    "maps_search_places",
    consequential=False,
    handler=_tool_search,
    description="Search real places on Google Maps (each search bills a fraction of a cent).",
)
tools.register(
    "maps_route",
    consequential=False,
    handler=_tool_route,
    description="Real distance and duration between two places (each route bills a fraction of a cent).",
)
