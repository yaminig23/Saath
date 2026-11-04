"""Reminder Agent (Milestone 3).

The Reminder Agent owns dated nudges inside the Daily Life & Productivity
department. A reminder is a genuine kernel task (department "daily-life",
agent "reminder") with a required deadline. Nothing here is simulated:
due means a real stored deadline at or before the real current time.
"""

import kernel
from util import parse_utc, utcnow

REMINDER_DEPARTMENT = "daily-life"
REMINDER_AGENT = "reminder"

# A reminder in one of these statuses needs no further attention.
SETTLED_STATUSES = ("COMPLETED", "FAILED")


def create_reminder(db, *, user_id: int, message: str, deadline: str, priority: str = "normal") -> dict:
    """Create a reminder due at `deadline` (ISO-8601 UTC text).

    Raises kernel.TaskRuleError if the message is empty, the deadline is
    missing, or the kernel rejects the priority/deadline format.
    """
    text = (message or "").strip()
    if not text:
        raise kernel.TaskRuleError("message must not be empty")
    if not (deadline or "").strip():
        raise kernel.TaskRuleError("deadline must not be empty (ISO-8601 UTC, e.g. 2026-11-01T09:00:00+00:00)")
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=text,
        department=REMINDER_DEPARTMENT,
        agent=REMINDER_AGENT,
        priority=priority,
        deadline=deadline,
    )
    return task.to_dict()


def list_due(db, *, user_id: int, now: str = "") -> list:
    """Return the user's reminders due at `now`, oldest deadline first.

    Due means: a reminder task with a deadline at or before `now` that is
    not settled (COMPLETED or FAILED). `now` defaults to the real current
    time; callers may pass an explicit ISO-8601 UTC time instead.
    """
    moment = parse_utc((now or "").strip() or utcnow())
    due = []
    for task in kernel.list_tasks(db, user_id):
        if task.department != REMINDER_DEPARTMENT or task.agent != REMINDER_AGENT:
            continue
        if task.status in SETTLED_STATUSES:
            continue
        if not task.deadline:
            continue
        try:
            moment_due = parse_utc(task.deadline)
        except ValueError:
            continue
        if moment_due <= moment:
            due.append(task)
    due.sort(key=lambda item: parse_utc(item.deadline))
    return [task.to_dict() for task in due]
