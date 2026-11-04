"""Gmail adapter: Saath's second real provider integration.

Same Desktop-OAuth shape as the calendar link, but a separate
send-only grant: the user connects again for Gmail, Saath stores a
per-user refresh token, and every send mints a short-lived access
token on the spot. The consent asks only to send mail and see the
account email — Saath cannot read the inbox, and there is no read
tool on purpose.

Sends always pass the approval gate (the `gmail_send` tool is
consequential). Google's reply carries the real message ID as proof,
or the send fails honestly with a failure report.
"""

import base64
import re
import secrets
import time
import urllib.parse
from email.message import EmailMessage

import httpx

import kernel
import tools
from config import settings
from models import GmailConnection
from util import utcnow

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
SEND_URL = "https://www.googleapis.com/gmail/v1/users/me/messages/send"
# Least privilege: send mail only, plus the account email for display.
SCOPES = "https://www.googleapis.com/auth/gmail.send openid email"

HTTP_TIMEOUT = 30.0
STATE_TTL_S = 600

MAX_TO_LEN = 254
MAX_SUBJECT_LEN = 200
MAX_BODY_LEN = 20000

# state -> {"user_id": int, "redirect_uri": str, "expires": float}.
_pending: dict = {}


class GmailError(kernel.KernelError):
    """Gmail refused, is unreachable, or is not connected."""


def _client_id() -> str:
    # One Google Cloud OAuth client serves every Google integration;
    # the settings keep their original calendar-era names.
    return settings.gcal_client_id.strip()


def _client_secret() -> str:
    return settings.gcal_client_secret.strip()


def client_configured() -> bool:
    """True when the app's OAuth keys are present (not the user's link)."""
    return bool(_client_id() and _client_secret())


def _require_client() -> None:
    if not client_configured():
        raise GmailError(
            "Gmail needs GOOGLE_CALENDAR_CLIENT_ID + GOOGLE_CALENDAR_CLIENT_SECRET in .env "
            "(Google Cloud Console: enable the Gmail API on the same project, Desktop-app client ID)"
        )


def _check_redirect(redirect_uri: str) -> str:
    """Loopback callbacks only: tokens must never leak to another host."""
    clean = (redirect_uri or "").strip()
    try:
        parsed = urllib.parse.urlparse(clean)
    except ValueError:
        raise GmailError("redirect must be this machine's /gmail/callback address")
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise GmailError("redirect must be this machine's /gmail/callback address")
    if parsed.path != "/gmail/callback":
        raise GmailError("redirect must be this machine's /gmail/callback address")
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
            "client_id": _client_id(),
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
        raise GmailError(f"{label} failed: {exc}")
    try:
        payload = response.json()
    except ValueError:
        raise GmailError(f"{label} returned an unreadable reply")
    if response.status_code != 200 or not isinstance(payload, dict):
        code = payload.get("error", "") if isinstance(payload, dict) else ""
        detail = payload.get("error_description", "") if isinstance(payload, dict) else ""
        why = f"{code}: {detail}".strip(": ") or f"HTTP {response.status_code}"
        raise GmailError(f"{label} refused: {why}")
    return payload


def connect_with_code(db, *, state: str, code: str) -> dict:
    """Finish a login started by auth_url. The state binds the code to
    the user who asked, so the keyless callback cannot be hijacked."""
    item = _pending.pop(state or "", None)
    if item is None or item["expires"] <= time.time():
        raise GmailError("that login link expired — start Connect again")
    if not (code or "").strip():
        raise GmailError("Google sent no code — close the tab and start Connect again")
    _require_client()
    payload = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": item["redirect_uri"],
        },
        "Google sign-in",
    )
    refresh = str(payload.get("refresh_token", "") or "")
    if not refresh:
        raise GmailError(
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
    row = db.query(GmailConnection).filter_by(user_id=item["user_id"]).first()
    if row is None:
        row = GmailConnection(
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
    """Forget this user's Gmail link (sent mail stays sent)."""
    row = db.query(GmailConnection).filter_by(user_id=user_id).first()
    if row is None:
        return {"disconnected": False, "detail": "no Gmail link to remove"}
    db.delete(row)
    db.commit()
    return {"disconnected": True, "detail": "Gmail link removed"}


def status(db, *, user_id: int) -> dict:
    """Honest per-user state. No network calls."""
    if not client_configured():
        return {
            "client_configured": False, "connected": False, "email": "",
            "broken": "", "detail": "needs GOOGLE_CALENDAR_CLIENT_ID + _SECRET in .env",
        }
    row = db.query(GmailConnection).filter_by(user_id=user_id).first()
    if row is None:
        return {
            "client_configured": True, "connected": False, "email": "",
            "broken": "", "detail": "app keys present — connect your Google account for Gmail",
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
    row = db.query(GmailConnection).filter_by(user_id=user_id).first()
    if row is None:
        raise GmailError("Gmail is not connected — use Connect first")
    if row.broken_note:
        raise GmailError(row.broken_note)
    _require_client()
    try:
        payload = _post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": row.refresh_token,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
            },
            "Google sign-in refresh",
        )
    except GmailError as exc:
        if "invalid_grant" in str(exc):
            row.broken_note = (
                "Google rejected the stored sign-in (revoked or expired) — "
                "use Connect again to relink Gmail"
            )
            row.updated_at = utcnow()
            db.commit()
            raise GmailError(row.broken_note)
        raise
    access = str(payload.get("access_token", "") or "")
    if not access:
        raise GmailError("Google sign-in refresh returned no token")
    return access


_TO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_send_fields(*, to: str, subject: str, body: str) -> dict:
    clean_to = (to or "").strip()
    if not clean_to or len(clean_to) > MAX_TO_LEN or not _TO_RE.match(clean_to):
        raise GmailError("to must be one valid email address")
    clean_subject = (subject or "").strip()
    if not clean_subject:
        raise GmailError("subject must not be empty")
    if len(clean_subject) > MAX_SUBJECT_LEN:
        raise GmailError("subject must be at most 200 characters")
    clean_body = (body or "").strip()
    if not clean_body:
        raise GmailError("body must not be empty")
    if len(clean_body) > MAX_BODY_LEN:
        raise GmailError("body must be at most 20000 characters")
    return {"to": clean_to, "subject": clean_subject, "body": clean_body}


def send_email(db, *, user_id: int, to: str, subject: str, body: str) -> dict:
    """Send one real email from the user's Gmail. Returns Google's
    message ID as proof — or raises, never a fake receipt."""
    fields = _check_send_fields(to=to, subject=subject, body=body)
    token = _access_token(db, user_id=user_id)
    message = EmailMessage()
    message["To"] = fields["to"]
    message["Subject"] = fields["subject"]
    message.set_content(fields["body"])
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    try:
        response = httpx.post(
            SEND_URL, json={"raw": raw},
            headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GmailError(f"sending failed: {exc}")
    if response.status_code == 401:
        raise GmailError("Google rejected the sign-in mid-call — try again, then reconnect if it repeats")
    if response.status_code != 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = (payload.get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
        raise GmailError(f"sending refused: {str(detail or response.status_code)[:300]}")
    try:
        sent = response.json()
    except ValueError:
        raise GmailError("sending returned an unreadable reply")
    message_id = str(sent.get("id", "") or "")
    if not message_id:
        raise GmailError("Google confirmed without a message ID — check Sent Mail before retrying")
    return {"message_id": message_id, "to": fields["to"], "subject": fields["subject"]}


def check(db, *, user_id: int) -> dict:
    """Live per-user check for the Providers screen. Sending a real
    email just to check would be wrong, so the check verifies the
    sign-in refreshes and reports the grant honestly."""
    row = db.query(GmailConnection).filter_by(user_id=user_id).first()
    if row is None:
        return {"ok": False, "detail": "Gmail is not connected — use Connect first"}
    if row.broken_note:
        return {"ok": False, "detail": row.broken_note}
    try:
        _access_token(db, user_id=user_id)
    except GmailError as exc:
        return {"ok": False, "detail": str(exc)}
    who = row.email or "your Google account"
    return {"ok": True, "detail": f"send-only grant works for {who} (no email sent by this check)"}


def _tool_send(db, *, user_id, task, args) -> dict:
    """Consequential tool: a real email leaves the user's Gmail."""
    args = args or {}
    return send_email(db, user_id=user_id, to=args.get("to", ""), subject=args.get("subject", ""), body=args.get("body", ""))


tools.register(
    "gmail_send",
    consequential=True,
    handler=_tool_send,
    description="Send a real email from my Gmail (needs my approval).",
)
