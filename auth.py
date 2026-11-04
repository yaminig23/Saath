"""Login, sessions, and password security (Milestone 1).

Two ideas keep this safe while using only Python's standard library:

- Passwords are scrambled with scrypt before storing. The plain password
  is never saved anywhere, so a stolen database still protects users.
- Login tokens are long random strings, and only their SHA-256 fingerprint
  is stored. A stolen database does not reveal any live token.
"""

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from config import settings
from models import LoginAttempt
from models import Session as SessionRow
from models import User
from util import parse_utc, utcnow

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256
MAX_LOGIN_FAILURES = 5
LOCKOUT_WINDOW_MINUTES = 15
HASH_VERSION = "1"

# A valid scrypt string burned on unknown-user logins so they cost the
# same time as a real password check (no cheap user enumeration).
_DUMMY_HASH = ""


class AuthError(ValueError):
    """Something about registration or login was rejected."""


class LockedOutError(AuthError):
    """Too many failed logins; try again later."""


def _dummy_hash() -> str:
    """A parseable scrypt string, built once, for timing-safe rejections."""
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASH


def hash_password(password: str) -> str:
    """Scramble a password with a fresh random salt. Returns storable text."""
    salt = secrets.token_bytes(16)
    scrambled = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(scrambled).decode("ascii")
    return f"scrypt${HASH_VERSION}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against its stored scramble. Never crashes."""
    try:
        _, _, salt_b64, hash_b64 = stored.split("$", 3)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)


def _hash_token(token: str) -> str:
    """Fingerprint of a login token (what we store in the database)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_user(db, username: str, password: str) -> User:
    """Create a user account. Raises AuthError if the input is rejected."""
    if not USERNAME_RE.match(username or ""):
        raise AuthError("username must be 3-40 letters, digits, _ or -")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise AuthError("password must be at least 8 characters")
    if len(password or "") > MAX_PASSWORD_LEN:
        raise AuthError("password must be at most 256 characters")
    if db.query(User).filter_by(username=username).first() is not None:
        raise AuthError("username is already taken")
    user = User(username=username, password_hash=hash_password(password), created_at=utcnow())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _window_start(now: str) -> str:
    """Oldest timestamp still inside the lockout window."""
    return (parse_utc(now) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)).isoformat()


def _recent_failures(db, username: str, now: str) -> int:
    return (
        db.query(LoginAttempt)
        .filter(LoginAttempt.username == username, LoginAttempt.attempted_at >= _window_start(now))
        .count()
    )


def _locked_out(db, username: str, now: str) -> bool:
    return _recent_failures(db, username, now) >= MAX_LOGIN_FAILURES


def _record_failure(db, username: str, now: str) -> None:
    db.add(LoginAttempt(username=username, attempted_at=now))
    db.commit()


def login_user(db, username: str, password: str) -> tuple:
    """Check credentials and issue a login token. Returns (token, expires_at).

    Raises LockedOutError after too many recent failures (known and
    unknown users alike); unknown users cost a full password check so
    timing reveals nothing.
    """
    username = username or ""
    password = password or ""
    now = utcnow()
    # Tidy up: forget failures outside the window on every login attempt.
    db.query(LoginAttempt).filter(LoginAttempt.attempted_at < _window_start(now)).delete()
    db.commit()
    if _locked_out(db, username, now):
        raise LockedOutError("too many failed attempts, try again later")
    user = db.query(User).filter_by(username=username).first()
    if user is None:
        verify_password(password, _dummy_hash())
        _record_failure(db, username, now)
        raise AuthError("wrong username or password")
    if not verify_password(password, user.password_hash):
        _record_failure(db, username, now)
        raise AuthError("wrong username or password")
    # Success clears the count; also drop this user's expired tokens.
    db.query(LoginAttempt).filter_by(username=username).delete()
    db.query(SessionRow).filter(SessionRow.user_id == user.id, SessionRow.expires_at < now).delete()
    token = secrets.token_urlsafe(32)
    expires_at = (parse_utc(now) + timedelta(hours=settings.session_ttl_hours)).isoformat()
    db.add(SessionRow(user_id=user.id, token_hash=_hash_token(token), expires_at=expires_at, created_at=now))
    db.commit()
    return token, expires_at


def _token_from_header(authorization: str) -> str:
    """Pull the token out of an 'Authorization: Bearer <token>' header."""
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def user_from_header(db, authorization: str):
    """Return the logged-in user for a request header, or None."""
    token = _token_from_header(authorization or "")
    if not token:
        return None
    row = db.query(SessionRow).filter_by(token_hash=_hash_token(token)).first()
    if row is None:
        return None
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        return None
    return db.query(User).filter_by(id=row.user_id).first()


def logout_token(db, authorization: str) -> None:
    """Throw away one login token. Logging out twice is harmless."""
    token = _token_from_header(authorization or "")
    if not token:
        return
    row = db.query(SessionRow).filter_by(token_hash=_hash_token(token)).first()
    if row is not None:
        db.delete(row)
        db.commit()
