"""Tool framework (Milestone 6).

Tools are how agents touch the world outside the task tables. Each tool
is registered as harmless (runs at once) or consequential (pauses its
task into AWAITING_USER and waits for the user's approval, unless the
tool is named in AUTO_APPROVE_TOOLS — then it executes at once, still
audited). Approvals
expire, are single-use, and every request, decision, and execution is
audited on the task's event trail. Failures leave a failure report; a
failed action is never recorded as completed.

M6 ships two real tools: task_stats (harmless, read-only counts) and
export_calendar (consequential: personal data leaves the database as a
calendar file in the outbox).
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import kernel
from config import settings
from models import Approval, Failure
from util import parse_utc, utcnow

APPROVAL_STATUSES = ("pending", "approved", "denied", "expired")

MAX_NOTE_LEN = 1000
MAX_ARGS_LEN = 4000


class ToolError(kernel.KernelError):
    """A tool request broke a tool rule."""


@dataclass
class ToolSpec:
    """One registered tool: its name, gate, handler, and plain description."""

    name: str
    consequential: bool
    handler: object
    description: str = ""


TOOLS = {}


def register(name, *, consequential, handler, description=""):
    """Register (or replace) a tool. Tests use this to inject failing tools
    and must remove their test tools afterwards."""
    TOOLS[name] = ToolSpec(name=name, consequential=consequential, handler=handler, description=description)


def get_outbox_dir() -> str:
    """Absolute outbox path (OUTBOX_DIR setting, relative to the project)."""
    configured = settings.outbox_dir or "outbox"
    if os.path.isabs(configured):
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), configured)


def _task_stats(db, *, user_id, task, args) -> dict:
    """Count the user's tasks by state (harmless, read-only)."""
    counts = {"active": 0, "completed": 0, "failed": 0}
    for row in kernel.list_tasks(db, user_id):
        if row.status == "COMPLETED":
            counts["completed"] += 1
        elif row.status == "FAILED":
            counts["failed"] += 1
        else:
            counts["active"] += 1
    return counts


def _escape_ics(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _export_calendar(db, *, user_id, task, args) -> dict:
    """Write the user's reminders to a calendar (.ics) file in the outbox.

    Reminders without a readable deadline are counted as skipped, never
    guessed. Returns the file path plus honest event counts.
    """
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Saath//Reminders//EN"]
    events = skipped = 0
    for row in kernel.list_tasks(db, user_id):
        if row.department != "daily-life" or row.agent != "reminder":
            continue
        moment = None
        if row.deadline:
            try:
                parsed = parse_utc(row.deadline)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                moment = parsed.astimezone(timezone.utc)
            except ValueError:
                moment = None
        if moment is None:
            skipped += 1
            continue
        events += 1
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:saath-{row.id}@local",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{moment.strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{_escape_ics(row.objective)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    outbox = get_outbox_dir()
    os.makedirs(outbox, exist_ok=True)
    path = os.path.join(outbox, f"reminders-{task.id}-{stamp}.ics")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\r\n".join(lines) + "\r\n")
    return {"path": os.path.abspath(path), "events": events, "skipped": skipped}


register("task_stats", consequential=False, handler=_task_stats, description="Count my tasks by state.")
register(
    "export_calendar",
    consequential=True,
    handler=_export_calendar,
    description="Export my reminders to a calendar file in the outbox.",
)


def validate_auto_approve_tools(names) -> None:
    """Fail fast on typos: every allowlist name must be a registered tool.

    Demo tools are never allowlisted: the demo must always show the
    approval click.
    """
    demo = [name for name in names if name.startswith("demo_")]
    if demo:
        raise ValueError(f"demo tool(s) cannot be auto-approved: {', '.join(demo)}")
    unknown = [name for name in names if name not in TOOLS]
    if unknown:
        raise ValueError(f"unknown tool(s) in AUTO_APPROVE_TOOLS: {', '.join(unknown)}")


validate_auto_approve_tools(settings.auto_approve_tools)


def record_failure(db, *, user_id, task_id, agent="", tool="", error, context=None) -> dict:
    """Write a failure report row. Available to every agent and tool."""
    row = Failure(
        user_id=user_id,
        task_id=task_id,
        agent=agent or "",
        tool=tool or "",
        error=(error or "").strip() or "unknown error",
        context=json.dumps(context or {}),
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.to_dict()


def request_tool(db, *, user_id, task_id, tool_name, args=None) -> dict:
    """Run a tool for an IN_PROGRESS task.

    Harmless tools execute at once. Consequential tools pause the task
    into AWAITING_USER and return the pending approval's ID instead,
    unless the tool is allowlisted in AUTO_APPROVE_TOOLS — then the
    approval is granted at once and the tool executes immediately.
    """
    task = kernel.get_task(db, user_id, task_id)
    if task.status != "IN_PROGRESS":
        raise ToolError("tools run on IN_PROGRESS tasks")
    spec = TOOLS.get(tool_name or "")
    if spec is None:
        raise ToolError(f"unknown tool {tool_name!r}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("args must be an object")
    try:
        packed = json.dumps(args)
    except (TypeError, ValueError):
        raise ToolError("args must be JSON-serializable")
    if len(packed) > MAX_ARGS_LEN:
        raise ToolError("args too large")
    if not spec.consequential:
        try:
            result = spec.handler(db, user_id=user_id, task=task, args=args)
        except Exception as exc:
            report = record_failure(
                db, user_id=user_id, task_id=task.id, agent=task.agent, tool=spec.name,
                error=str(exc), context={"args": args},
            )
            kernel.transition_task(
                db, user_id=user_id, task_id=task.id, to_status="FAILED",
                note=f"{spec.name} failed", result=f"failed: {exc}", evidence=f"failure={report['id']}",
            )
            raise ToolError(f"{spec.name} failed (report {report['id']}): {exc}")
        kernel.log_event(db, user_id=user_id, task_id=task.id, event_type="tool_call", note=f"{spec.name} ok")
        return {"status": "done", "result": result}
    expires = (datetime.now(timezone.utc) + timedelta(hours=settings.approval_ttl_hours)).isoformat()
    row = Approval(
        user_id=user_id, task_id=task.id, tool=spec.name, args=packed,
        status="pending", expires_at=expires, created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="AWAITING_USER",
        note=f"awaiting approval {row.id} for {spec.name}",
    )
    kernel.log_event(
        db, user_id=user_id, task_id=task.id, event_type="approval_requested", note=f"{spec.name} approval {row.id}"
    )
    if spec.name in settings.auto_approve_tools:
        return decide_approval(
            db, user_id=user_id, approval_id=row.id, approve=True,
            note="auto-approved by AUTO_APPROVE_TOOLS policy",
        )
    return {"status": "awaiting_approval", "approval_id": row.id}


def _fail_task(db, *, user_id, task_id, note, result, evidence=""):
    kernel.transition_task(
        db, user_id=user_id, task_id=task_id, to_status="FAILED", note=note, result=result, evidence=evidence,
    )


def decide_approval(db, *, user_id, approval_id, approve, note="") -> dict:
    """Approve or deny a pending approval, exactly once.

    Approving resumes the task and executes the tool; the task completes
    with the tool's result, or fails with a report if execution breaks.
    Denying fails the task honestly. Expired, reused, or orphaned
    approvals are rejected; expiry also fails the paused task so nothing
    sticks in AWAITING_USER forever.
    """
    row = db.query(Approval).filter_by(id=approval_id, user_id=user_id).first()
    if row is None:
        raise kernel.TaskNotFoundError("approval not found")
    if row.status != "pending":
        raise ToolError(f"approval already {row.status}")
    task = kernel.get_task(db, user_id, row.task_id)
    now = utcnow()
    if parse_utc(row.expires_at) <= datetime.now(timezone.utc):
        row.status = "expired"
        row.decided_at = now
        db.commit()
        _fail_task(db, user_id=user_id, task_id=task.id, note=f"approval {row.id} expired", result="approval expired")
        raise ToolError("approval expired")
    if task.status != "AWAITING_USER":
        row.status = "expired"
        row.decided_at = now
        db.commit()
        raise ToolError("task is no longer awaiting approval")
    note = (note or "").strip()
    if len(note) > MAX_NOTE_LEN:
        raise ToolError("note must be at most 1000 characters")
    if not approve:
        row.status = "denied"
        row.note = note
        row.decided_at = now
        db.commit()
        kernel.log_event(
            db, user_id=user_id, task_id=task.id, event_type="approval_decided", note=f"approval {row.id} denied"
        )
        _fail_task(db, user_id=user_id, task_id=task.id, note=f"approval {row.id} denied", result="denied by user")
        return {"status": "denied", "approval_id": row.id}
    row.status = "approved"
    row.note = note
    row.decided_at = now
    db.commit()
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note=f"approval {row.id} approved"
    )
    kernel.log_event(
        db, user_id=user_id, task_id=task.id, event_type="approval_decided", note=f"approval {row.id} approved"
    )
    spec = TOOLS.get(row.tool)
    task = kernel.get_task(db, user_id, row.task_id)
    args = json.loads(row.args)
    if spec is None:
        report = record_failure(
            db, user_id=user_id, task_id=task.id, agent=task.agent, tool=row.tool,
            error=f"unknown tool {row.tool!r}", context={"approval_id": row.id},
        )
        row.result = f"failed: unknown tool {row.tool!r}"
        db.commit()
        _fail_task(
            db, user_id=user_id, task_id=task.id, note=f"{row.tool} failed",
            result=f"failed: unknown tool {row.tool!r}", evidence=f"approval={row.id} failure={report['id']}",
        )
        raise ToolError(f"{row.tool} failed (report {report['id']}): unknown tool")
    try:
        result = spec.handler(db, user_id=user_id, task=task, args=args)
    except Exception as exc:
        report = record_failure(
            db, user_id=user_id, task_id=task.id, agent=task.agent, tool=spec.name,
            error=str(exc), context={"args": args, "approval_id": row.id},
        )
        row.result = f"failed: {exc}"
        db.commit()
        kernel.log_event(db, user_id=user_id, task_id=task.id, event_type="tool_call", note=f"{spec.name} failed")
        _fail_task(
            db, user_id=user_id, task_id=task.id, note=f"{spec.name} failed",
            result=f"failed: {exc}", evidence=f"approval={row.id} failure={report['id']}",
        )
        raise ToolError(f"{spec.name} failed (report {report['id']}): {exc}")
    summary = json.dumps(result, sort_keys=True)
    row.result = summary
    db.commit()
    kernel.log_event(db, user_id=user_id, task_id=task.id, event_type="tool_call", note=f"{spec.name} ok")
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="COMPLETED",
        note=f"{spec.name} executed", result=summary, evidence=f"approval={row.id} tool={spec.name}",
    )
    return {"status": "executed", "approval_id": row.id, "result": result}


def list_approvals(db, *, user_id, status="") -> list:
    """The user's approvals, oldest first, optionally filtered by status."""
    status = (status or "").strip()
    if status and status not in APPROVAL_STATUSES:
        raise ToolError(f"unknown status {status!r}")
    query = db.query(Approval).filter_by(user_id=user_id)
    if status:
        query = query.filter_by(status=status)
    return [row.to_dict() for row in query.order_by(Approval.id).all()]


def list_failures(db, *, user_id) -> list:
    """The user's failure reports, oldest first."""
    rows = db.query(Failure).filter_by(user_id=user_id).order_by(Failure.id).all()
    return [row.to_dict() for row in rows]
