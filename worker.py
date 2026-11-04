"""Background worker (Milestone 7).

An in-process scheduler that watches deadlines: every pass scans each
user's pending reminders and records a notification for every newly due
one, unless the user's quiet hours say to hold them. Notifications are
the delivery channel inside this prototype (push channels arrive later
as gated tools); the worker never claims to have sent anything anywhere.

The module is the replaceable interface from the plan: tick() holds all
the work logic on an injected session, so a future persistent worker
reuses it unchanged and only swaps the loop. Status reporting is honest
by construction: running is true only while the loop thread is alive.
"""

import re
import threading
from datetime import datetime, timezone

import kernel
import memory
import reminders
import video
from config import settings
from db import get_session_factory
from models import Notification, User
from util import parse_utc, utcnow

KIND_REMINDER_DUE = "reminder_due"

_QUIET_PATTERN = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")

_tick_lock = threading.Lock()
_state_lock = threading.Lock()
_state = {
    "interval_seconds": 60,
    "ticks_total": 0,
    "last_tick_at": "",
    "last_report": {},
    "last_error": "",
}
_thread = None
_stop_event = None


def _in_quiet_hours(value: str, now: datetime) -> bool:
    """True when UTC `now` falls in a "HH:MM-HH:MM" window (may cross midnight)."""
    match = _QUIET_PATTERN.match((value or "").strip())
    if not match:
        return False
    start_hour, start_minute, end_hour, end_minute = (int(part) for part in match.groups())
    if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
        return False
    current = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _tick_user(db, *, user_id: int, moment: datetime, moment_text: str, report: dict) -> None:
    routines = memory.recall(db, user_id=user_id, role="worker", layers=["user"])
    quiet = next((item["value"] for item in routines if item["key"] == "quiet-hours"), "")
    due = [
        item
        for item in reminders.list_due(db, user_id=user_id, now=moment_text)
        if item["status"] == "PENDING"
    ]
    report["checked"] += len(due)
    if quiet and _in_quiet_hours(quiet, moment):
        report["skipped_quiet"] += len(due)
        return
    for item in due:
        existing = (
            db.query(Notification)
            .filter_by(user_id=user_id, task_id=item["id"], kind=KIND_REMINDER_DUE, read_at="")
            .first()
        )
        if existing is not None:
            continue
        db.add(
            Notification(
                user_id=user_id,
                task_id=item["id"],
                kind=KIND_REMINDER_DUE,
                text=f"Reminder due: {item['objective']} (was due {item['deadline']})",
                created_at=utcnow(),
                read_at="",
            )
        )
        report["notified"] += 1
    db.commit()


def tick(db, *, now: str = "") -> dict:
    """One worker pass over every user. Returns the pass report and never raises."""
    moment_text = (now or "").strip() or utcnow()
    moment = parse_utc(moment_text)
    report = {"users": 0, "checked": 0, "notified": 0, "skipped_quiet": 0, "errors": [], "video_advanced": 0, "video_errors": []}
    for user in db.query(User).order_by(User.id).all():
        report["users"] += 1
        try:
            _tick_user(db, user_id=user.id, moment=moment, moment_text=moment_text, report=report)
        except Exception as exc:
            db.rollback()
            report["errors"].append({"user_id": user.id, "error": str(exc)})
    try:
        video_report = video.advance_due_jobs(db)
        report["video_advanced"] = video_report["advanced"]
        report["video_errors"] = video_report["errors"]
    except Exception as exc:
        db.rollback()
        report["errors"].append({"error": str(exc)})
    return report


def tick_once() -> dict:
    """Run one pass on a fresh session (what the loop calls)."""
    session = get_session_factory()()
    try:
        return tick(session)
    finally:
        session.close()


def _record_tick(report: dict) -> None:
    with _state_lock:
        _state["ticks_total"] += 1
        _state["last_tick_at"] = utcnow()
        _state["last_report"] = report
        _state["last_error"] = "; ".join(item["error"] for item in report["errors"])


def _loop(interval_seconds: int, stop_event) -> None:
    while not stop_event.wait(interval_seconds):
        try:
            with _tick_lock:
                report = tick_once()
        except Exception as exc:
            report = {"users": 0, "checked": 0, "notified": 0, "skipped_quiet": 0, "errors": [{"error": str(exc)}]}
        _record_tick(report)


def start(interval_seconds: int = 60) -> bool:
    """Start the loop thread. Returns False when it is already running."""
    global _thread, _stop_event
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _state["interval_seconds"] = interval_seconds
        _stop_event = threading.Event()
        _thread = threading.Thread(target=_loop, args=(interval_seconds, _stop_event), daemon=True)
        _thread.start()
        return True


def stop() -> None:
    """Stop the loop thread (waits briefly, then lets the daemon go)."""
    global _thread, _stop_event
    with _state_lock:
        thread, event = _thread, _stop_event
        _thread, _stop_event = None, None
    if event is not None:
        event.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5)


def start_if_enabled() -> bool:
    """Start the loop when WORKER_ENABLED says so. Runs at server boot."""
    if not settings.worker_enabled:
        return False
    return start(settings.worker_interval_seconds)


def status() -> dict:
    """Liveness plus the last pass report. Running means the thread is alive."""
    with _state_lock:
        thread = _thread
        body = dict(_state)
    body["running"] = thread is not None and thread.is_alive()
    return body


def manual_tick(db) -> dict:
    """Run one pass now on the given session (same code the loop calls)."""
    with _tick_lock:
        report = tick(db)
    _record_tick(report)
    return report


def list_notifications(db, *, user_id: int) -> list:
    """The user's notifications, oldest first."""
    rows = db.query(Notification).filter_by(user_id=user_id).order_by(Notification.id).all()
    return [row.to_dict() for row in rows]


def mark_read(db, *, user_id: int, notification_id: int) -> dict:
    """Mark one notification read (idempotent)."""
    row = db.query(Notification).filter_by(id=notification_id, user_id=user_id).first()
    if row is None:
        raise kernel.TaskNotFoundError("notification not found")
    if not row.read_at:
        row.read_at = utcnow()
        db.commit()
        db.refresh(row)
    return row.to_dict()
