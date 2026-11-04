"""Daily Organiser Agent (Milestone 3).

The Daily Organiser reads the user's real Daily Life tasks and writes a
short briefing: what is overdue, what is due later today, and how many
items are still open. It also recalls the user's routines and preferences
through the memory permission matrix (normal tier only: sensitive details
never leak into a briefing). The briefing is stored as a genuine audited
task (department "daily-life", agent "daily-organiser") so it can be
reviewed later like any other work.

The briefing text is composed deterministically from real data. When the
live Muse model is connected, _compose_briefing() is the one place that
can ask the model to phrase the same facts; the numbers themselves always
stay computed, never generated.
"""

import kernel
import memory
import reminders
from util import parse_utc, utcnow

ORGANISER_DEPARTMENT = "daily-life"
ORGANISER_AGENT = "daily-organiser"


def _compose_briefing(*, date: str, due: list, today: list, open_count: int, routines=()) -> str:
    """Turn real reminder rows into a short plain-text briefing."""
    attention = len(due) + len(today)
    lines = [f"Daily briefing for {date} ({attention} item(s) need attention).", ""]
    if due:
        lines.append(f"Overdue ({len(due)}):")
        lines.extend(f"- {item['objective']} (was due {item['deadline']})" for item in due)
        lines.append("")
    if today:
        lines.append(f"Due later today ({len(today)}):")
        lines.extend(f"- {item['objective']} (due {item['deadline']})" for item in today)
        lines.append("")
    if not due and not today:
        lines.append("All clear. No overdue or due-today reminders.")
        lines.append("")
    lines.append(f"Open daily-life items: {open_count}.")
    if routines:
        lines.append("")
        lines.append(f"Routines & preferences ({len(routines)}):")
        lines.extend(f"- {item['key']}: {item['value']}" for item in routines)
    return "\n".join(lines)


def daily_briefing(db, *, user_id: int, now: str = "") -> dict:
    """Write today's briefing and store it as an audited task.

    Returns a dict with task_id, summary, date, and the three counts.
    `now` defaults to the real current time; callers may pass an explicit
    ISO-8601 UTC time instead.
    """
    moment_text = (now or "").strip() or utcnow()
    moment = parse_utc(moment_text)
    day = moment.date().isoformat()

    due = reminders.list_due(db, user_id=user_id, now=moment_text)
    due_ids = {item["id"] for item in due}
    today = []
    open_count = 0
    for task in kernel.list_tasks(db, user_id):
        if task.department != ORGANISER_DEPARTMENT:
            continue
        if task.status in reminders.SETTLED_STATUSES:
            continue
        open_count += 1
        if task.id in due_ids or not task.deadline:
            continue
        try:
            moment_due = parse_utc(task.deadline)
        except ValueError:
            continue
        if moment_due > moment and moment_due.date().isoformat() == day:
            today.append(task.to_dict())
    today.sort(key=lambda item: item["deadline"])

    routines = memory.recall(
        db,
        user_id=user_id,
        role="specialist",
        department=ORGANISER_DEPARTMENT,
        agent=ORGANISER_AGENT,
        layers=["user"],
    )
    summary = _compose_briefing(date=day, due=due, today=today, open_count=open_count, routines=routines)
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Daily briefing for {day}",
        department=ORGANISER_DEPARTMENT,
        agent=ORGANISER_AGENT,
    )
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="daily organiser started"
    )
    evidence = f"due={len(due)} today={len(today)} open={open_count}"
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="daily organiser wrote briefing",
        result=summary,
        evidence=evidence,
    )
    return {
        "task_id": task.id,
        "summary": summary,
        "date": day,
        "due_count": len(due),
        "today_count": len(today),
        "open_count": open_count,
    }
