"""Google Calendar adapter: Saath's first real provider integration.

Desktop-OAuth flow against a loopback callback: the app asks for an
auth URL, the user approves in their browser, Google redirects to
/calendar/callback on this machine, and Saath exchanges the code for
a per-user refresh token. Reads and writes mint a short-lived access
token from the refresh token on every call, so no long-lived
credential sits in memory.

Honest states: app keys missing -> the adapter is missing; no
connection row -> not connected; a rejected refresh marks the row
broken with the exact remedy (reconnect). Google errors surface as
plain GcalError text, never a fake event.
"""

import secrets
import time
import urllib.parse
from datetime import datetime

import httpx

import kernel
import tools
from config import settings
from models import CalendarConnection
from util import utcnow

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
# Least privilege: read/write events plus your email address for display —
# never calendar settings, sharing, or anything else.
SCOPES = "https://www.googleapis.com/auth/calendar.events openid email"

HTTP_TIMEOUT = 30.0
STATE_TTL_S = 600

MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 2000
MAX_LOCATION_LEN = 200
MAX_TIMEZONE_LEN = 64

# state -> {"user_id": int, "redirect_uri": str, "expires": float}.
# In-memory by design: pending logins evaporate on restart, and the
# stored refresh token (the durable credential) lives in the database.
_pending: dict = {}


class GcalError(kernel.KernelError):
    """Google Calendar refused, is unreachable, or is not connected."""


def client_configured() -> bool:
    """True when the app's OAuth keys are present (not the user's link)."""
    return bool(settings.gcal_client_id.strip() and settings.gcal_client_secret.strip())


def _require_client() -> None:
    if not client_configured():
        raise GcalError(
            "Google Calendar needs GOOGLE_CALENDAR_CLIENT_ID + GOOGLE_CALENDAR_CLIENT_SECRET in .env "
            "(Google Cloud Console: enable the Calendar API, OAuth consent screen, Desktop-app client ID)"
        )


def _check_redirect(redirect_uri: str) -> str:
    """Loopback callbacks only: tokens must never leak to another host."""
    clean = (redirect_uri or "").strip()
    try:
        parsed = urllib.parse.urlparse(clean)
    except ValueError:
        raise GcalError("redirect must be this machine's /calendar/callback address")
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise GcalError("redirect must be this machine's /calendar/callback address")
    if parsed.path != "/calendar/callback":
        raise GcalError("redirect must be this machine's /calendar/callback address")
    return clean


def auth_url(*, user_id: int, redirect_uri: str) -> dict:
    """Start a login: returns the Google approval URL for this user."""
    _require_client()
    clean_redirect = _check_redirect(redirect_uri)
    now = time.time()
    expired = [state for state, item in _pending.items() if item["expires"] <= now]
    for state in expired:
        del _pending[state]
    state = secrets.token_urlsafe(24)
    _pending[state] = {"user_id": user_id, "redirect_uri": clean_redirect, "expires": now + STATE_TTL_S}
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.gcal_client_id.strip(),
            "redirect_uri": clean_redirect,
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return {"url": f"{AUTH_URL}?{params}"}


def _post_form(url: str, data: dict, label: str) -> dict:
    try:
        response = httpx.post(url, data=data, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GcalError(f"{label} failed: {exc}")
    try:
        payload = response.json()
    except ValueError:
        raise GcalError(f"{label} returned an unreadable reply")
    if response.status_code != 200 or not isinstance(payload, dict):
        code = payload.get("error", "") if isinstance(payload, dict) else ""
        detail = payload.get("error_description", "") if isinstance(payload, dict) else ""
        why = f"{code}: {detail}".strip(": ") or f"HTTP {response.status_code}"
        raise GcalError(f"{label} refused: {why}")
    return payload


def connect_with_code(db, *, state: str, code: str) -> dict:
    """Finish a login started by auth_url. The state binds the code to
    the user who asked, so the keyless callback cannot be hijacked."""
    item = _pending.pop(state or "", None)
    if item is None or item["expires"] <= time.time():
        raise GcalError("that login link expired — start Connect again")
    if not (code or "").strip():
        raise GcalError("Google sent no code — close the tab and start Connect again")
    _require_client()
    payload = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "client_id": settings.gcal_client_id.strip(),
            "client_secret": settings.gcal_client_secret.strip(),
            "redirect_uri": item["redirect_uri"],
        },
        "Google sign-in",
    )
    refresh = str(payload.get("refresh_token", "") or "")
    if not refresh:
        raise GcalError(
            "Google sent no reusable sign-in — remove Saath at "
            "myaccount.google.com/permissions, then Connect again"
        )
    email = ""
    access = str(payload.get("access_token", "") or "")
    if access:
        try:
            info = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"}, timeout=HTTP_TIMEOUT)
            if info.status_code == 200:
                email = str(info.json().get("email", "") or "")[:254]
        except httpx.HTTPError:
            email = ""
    now = utcnow()
    row = db.query(CalendarConnection).filter_by(user_id=item["user_id"]).first()
    if row is None:
        row = CalendarConnection(
            user_id=item["user_id"], email=email, refresh_token=refresh,
            broken_note="", created_at=now, updated_at=now,
        )
        db.add(row)
    else:
        row.email = email
        row.refresh_token = refresh
        row.broken_note = ""
        row.updated_at = now
    db.commit()
    return {"connected": True, "email": email}


def disconnect(db, *, user_id: int) -> dict:
    """Forget this user's Google link (their Google events stay put)."""
    row = db.query(CalendarConnection).filter_by(user_id=user_id).first()
    if row is None:
        return {"disconnected": False, "detail": "no Google Calendar link to remove"}
    db.delete(row)
    db.commit()
    return {"disconnected": True, "detail": "Google Calendar link removed"}


def status(db, *, user_id: int) -> dict:
    """Honest per-user state. No network calls."""
    if not client_configured():
        return {
            "client_configured": False, "connected": False, "email": "",
            "broken": "", "detail": "needs GOOGLE_CALENDAR_CLIENT_ID + _SECRET in .env",
        }
    row = db.query(CalendarConnection).filter_by(user_id=user_id).first()
    if row is None:
        return {
            "client_configured": True, "connected": False, "email": "",
            "broken": "", "detail": "app keys present — connect your Google account",
        }
    if row.broken_note:
        return {
            "client_configured": True, "connected": False, "email": row.email,
            "broken": row.broken_note, "detail": row.broken_note,
        }
    return {
        "client_configured": True, "connected": True, "email": row.email,
        "broken": "", "detail": f"connected as {row.email or 'your Google account'}",
    }


def _access_token(db, *, user_id: int) -> str:
    row = db.query(CalendarConnection).filter_by(user_id=user_id).first()
    if row is None:
        raise GcalError("Google Calendar is not connected — use Connect first")
    if row.broken_note:
        raise GcalError(row.broken_note)
    _require_client()
    try:
        payload = _post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": row.refresh_token,
                "client_id": settings.gcal_client_id.strip(),
                "client_secret": settings.gcal_client_secret.strip(),
            },
            "Google sign-in refresh",
        )
    except GcalError as exc:
        if "invalid_grant" in str(exc):
            row.broken_note = (
                "Google rejected the stored sign-in (revoked or expired) — "
                "use Connect again to relink your account"
            )
            row.updated_at = utcnow()
            db.commit()
            raise GcalError(row.broken_note)
        raise
    access = str(payload.get("access_token", "") or "")
    if not access:
        raise GcalError("Google sign-in refresh returned no token")
    return access


def _google_error(response, label: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"{label} error {response.status_code}"
    message = (payload.get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
    message = str(message or f"HTTP {response.status_code}").strip()
    return f"{label} refused: {message[:300]}"


def _check_event_fields(*, title: str, date: str, start: str, end: str, time_zone: str,
                        description: str, location: str) -> dict:
    clean_title = (title or "").strip()
    if not clean_title:
        raise GcalError("title must not be empty")
    if len(clean_title) > MAX_TITLE_LEN:
        raise GcalError("title must be at most 200 characters")
    try:
        datetime.strptime((date or "").strip(), "%Y-%m-%d")
    except ValueError:
        raise GcalError("date must be YYYY-MM-DD")
    for label, value in (("start", start), ("end", end)):
        try:
            datetime.strptime((value or "").strip(), "%H:%M")
        except ValueError:
            raise GcalError(f"{label} must be HH:MM (24-hour)")
    if (start or "").strip() >= (end or "").strip():
        raise GcalError("overnight events are not supported yet — end must be after start")
    zone = (time_zone or "").strip()
    if not zone or len(zone) > MAX_TIMEZONE_LEN:
        raise GcalError("time_zone must be your timezone name (e.g. Asia/Kolkata)")
    clean_description = (description or "").strip()
    if len(clean_description) > MAX_DESCRIPTION_LEN:
        raise GcalError("description must be at most 2000 characters")
    clean_location = (location or "").strip()
    if len(clean_location) > MAX_LOCATION_LEN:
        raise GcalError("location must be at most 200 characters")
    return {
        "title": clean_title, "date": (date or "").strip(),
        "start": (start or "").strip(), "end": (end or "").strip(),
        "time_zone": zone, "description": clean_description, "location": clean_location,
    }


def create_event(db, *, user_id: int, title: str, date: str, start: str, end: str,
                 time_zone: str, description: str = "", location: str = "") -> dict:
    """Create one real event on the user's primary calendar. Returns
    Google's event ID and link as proof — or raises, never a fake."""
    fields = _check_event_fields(
        title=title, date=date, start=start, end=end,
        time_zone=time_zone, description=description, location=location,
    )
    token = _access_token(db, user_id=user_id)
    body = {
        "summary": fields["title"],
        "description": fields["description"],
        "location": fields["location"],
        "start": {"dateTime": f"{fields['date']}T{fields['start']}:00", "timeZone": fields["time_zone"]},
        "end": {"dateTime": f"{fields['date']}T{fields['end']}:00", "timeZone": fields["time_zone"]},
    }
    try:
        response = httpx.post(
            EVENTS_URL, json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GcalError(f"event creation failed: {exc}")
    if response.status_code == 401:
        raise GcalError("Google rejected the sign-in mid-call — try again, then reconnect if it repeats")
    if response.status_code not in (200, 201):
        raise GcalError(_google_error(response, "event creation"))
    try:
        made = response.json()
    except ValueError:
        raise GcalError("event creation returned an unreadable reply")
    event_id = str(made.get("id", "") or "")
    if not event_id:
        raise GcalError("Google confirmed without an event ID — check your calendar before retrying")
    return {
        "event_id": event_id,
        "link": str(made.get("htmlLink", "") or ""),
        "title": fields["title"], "date": fields["date"],
        "start": fields["start"], "end": fields["end"], "time_zone": fields["time_zone"],
    }


def list_day(db, *, user_id: int, date: str, time_zone: str) -> dict:
    """Today's (or any day's) real events, earliest first."""
    try:
        day = datetime.strptime((date or "").strip(), "%Y-%m-%d")
    except ValueError:
        raise GcalError("date must be YYYY-MM-DD")
    zone = (time_zone or "").strip()
    if not zone or len(zone) > MAX_TIMEZONE_LEN:
        raise GcalError("time_zone must be your timezone name (e.g. Asia/Kolkata)")
    from zoneinfo import ZoneInfo

    try:
        local = ZoneInfo(zone)
    except Exception:
        raise GcalError(f"unknown time_zone {zone!r} — use e.g. Asia/Kolkata")
    day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=local)
    day_end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=local)
    token = _access_token(db, user_id=user_id)
    clean = (date or "").strip()
    try:
        response = httpx.get(
            EVENTS_URL,
            params={
                "timeMin": day_start.isoformat(),
                "timeMax": day_end.isoformat(),
                "timeZone": zone,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 50,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GcalError(f"reading your day failed: {exc}")
    if response.status_code == 401:
        raise GcalError("Google rejected the sign-in mid-call — try again, then reconnect if it repeats")
    if response.status_code != 200:
        raise GcalError(_google_error(response, "reading your day"))
    try:
        items = response.json().get("items", [])
    except ValueError:
        raise GcalError("reading your day returned an unreadable reply")
    events = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start = item.get("start", {}) or {}
        end = item.get("end", {}) or {}
        events.append(
            {
                "id": str(item.get("id", "") or ""),
                "title": str(item.get("summary", "") or "(no title)"),
                "start": str(start.get("dateTime", start.get("date", "")) or ""),
                "end": str(end.get("dateTime", end.get("date", "")) or ""),
                "link": str(item.get("htmlLink", "") or ""),
            }
        )
    return {"date": clean, "time_zone": zone, "events": events}


def check(db, *, user_id: int) -> dict:
    """Live per-user check for the Providers screen: reads today."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        found = list_day(db, user_id=user_id, date=today, time_zone="UTC")
    except GcalError as exc:
        return {"ok": False, "detail": str(exc)}
    row = db.query(CalendarConnection).filter_by(user_id=user_id).first()
    who = (row.email if row and row.email else "your Google account")
    count = len(found["events"])
    return {"ok": True, "detail": f"connected as {who} ({count} event(s) today, UTC)"}


def _tool_add_event(db, *, user_id, task, args) -> dict:
    """Consequential tool: a real event lands on the user's calendar."""
    args = args or {}
    return create_event(
        db,
        user_id=user_id,
        title=args.get("title", ""),
        date=args.get("date", ""),
        start=args.get("start", ""),
        end=args.get("end", ""),
        time_zone=args.get("time_zone", ""),
        description=args.get("description", ""),
        location=args.get("location", ""),
    )


def _tool_read_day(db, *, user_id, task, args) -> dict:
    """Harmless tool: read one day's real events."""
    args = args or {}
    return list_day(db, user_id=user_id, date=args.get("date", ""), time_zone=args.get("time_zone", ""))


tools.register(
    "calendar_add_event",
    consequential=True,
    handler=_tool_add_event,
    description="Add a real event to my Google Calendar (needs my approval).",
)
tools.register(
    "calendar_read_day",
    consequential=False,
    handler=_tool_read_day,
    description="Read one day's real events from my Google Calendar.",
)
