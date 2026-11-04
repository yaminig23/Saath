"""Tiny shared helpers (Milestone 1)."""

from datetime import datetime, timezone


def utcnow() -> str:
    """Current UTC time as text, e.g. 2026-10-31T12:00:00+00:00."""
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    """Parse text produced by utcnow() back into a datetime."""
    return datetime.fromisoformat(value)
