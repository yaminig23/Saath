"""Deterministic task kernel (Milestone 1).

The kernel owns every task rule. Later the AI may *propose* actions, but only
this plain code can change task state - and every change is written to the
audit log (task_events). No agent can skip statuses, touch another user's
tasks, or silently rewrite history.
"""

import json

from models import Task, TaskEvent
from util import parse_utc, utcnow

STATUSES = ("PENDING", "IN_PROGRESS", "AWAITING_USER", "BLOCKED", "FAILED", "COMPLETED")

# Every legal status change. Anything missing here is rejected.
TRANSITIONS = {
    "PENDING": ("IN_PROGRESS", "BLOCKED"),
    "IN_PROGRESS": ("AWAITING_USER", "BLOCKED", "FAILED", "COMPLETED"),
    "AWAITING_USER": ("IN_PROGRESS", "BLOCKED", "FAILED"),
    "BLOCKED": ("PENDING", "IN_PROGRESS", "FAILED"),
    "FAILED": ("PENDING",),
    "COMPLETED": (),
}

PRIORITIES = ("low", "normal", "high", "urgent")

MAX_OBJECTIVE_LEN = 2000
MAX_TAG_LEN = 80

# Deepest allowed delegation chain (leaf task = 0). Central -> manager ->
# specialists sits at depth 2, leaving room for one temporary-agent level.
MAX_DELEGATION_DEPTH = 3


class KernelError(ValueError):
    """Base class for every kernel rejection."""


class TaskNotFoundError(KernelError):
    """No such task for this user (also used to hide other users' tasks)."""


class TaskRuleError(KernelError):
    """The request broke a task rule (bad status, priority, or input)."""


def _owned_task(db, user_id: int, task_id: int) -> Task:
    task = db.query(Task).filter_by(id=task_id, user_id=user_id).first()
    if task is None:
        raise TaskNotFoundError("task not found")
    return task


def _log_event(db, task_id: int, event_type: str, from_status: str, to_status: str, note: str) -> None:
    db.add(
        TaskEvent(
            task_id=task_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            note=note or "",
            created_at=utcnow(),
        )
    )
    db.commit()


def create_task(
    db,
    *,
    user_id: int,
    objective: str,
    department: str = "",
    agent: str = "",
    priority: str = "normal",
    dependencies=None,
    deadline: str = "",
) -> Task:
    """Create a task in PENDING state and audit its creation."""
    objective = (objective or "").strip()
    if not objective:
        raise TaskRuleError("objective must not be empty")
    if len(objective) > MAX_OBJECTIVE_LEN:
        raise TaskRuleError("objective must be at most 2000 characters")
    if priority not in PRIORITIES:
        raise TaskRuleError(f"priority must be one of {', '.join(PRIORITIES)}")
    dependencies = list(dependencies or [])
    if any(not isinstance(dep, int) for dep in dependencies):
        raise TaskRuleError("dependencies must be a list of task IDs")
    deadline = (deadline or "").strip()
    if deadline:
        try:
            parse_utc(deadline)
        except ValueError:
            raise TaskRuleError("deadline must be ISO-8601 UTC text, e.g. 2026-11-01T09:00:00+00:00")
    department = (department or "").strip()
    if len(department) > MAX_TAG_LEN:
        raise TaskRuleError("department must be at most 80 characters")
    agent = (agent or "").strip()
    if len(agent) > MAX_TAG_LEN:
        raise TaskRuleError("agent must be at most 80 characters")
    now = utcnow()
    task = Task(
        user_id=user_id,
        objective=objective,
        department=department,
        agent=agent,
        priority=priority,
        dependencies=json.dumps(dependencies),
        deadline=deadline,
        status="PENDING",
        result="",
        evidence="",
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _log_event(db, task.id, "created", "", "PENDING", "")
    return task


def transition_task(db, *, user_id: int, task_id: int, to_status: str, note: str = "", result: str = "", evidence: str = "") -> Task:
    """Move a task to a new status if the kernel allows it. Audits the move."""
    task = _owned_task(db, user_id, task_id)
    if to_status not in STATUSES:
        raise TaskRuleError(f"unknown status {to_status!r}; must be one of {', '.join(STATUSES)}")
    if to_status not in TRANSITIONS[task.status]:
        raise TaskRuleError(f"illegal transition {task.status} -> {to_status}")
    from_status = task.status
    task.status = to_status
    task.updated_at = utcnow()
    if result:
        task.result = result
    if evidence:
        task.evidence = evidence
    _log_event(db, task.id, "transition", from_status, to_status, note or "")
    db.commit()
    db.refresh(task)
    return task


def _read_dependencies(task: Task) -> list:
    """Parse a task's dependency list, rejecting corrupt values."""
    try:
        current = json.loads(task.dependencies)
    except ValueError:
        raise TaskRuleError("task has unreadable dependencies")
    if not isinstance(current, list) or any(not isinstance(dep, int) for dep in current):
        raise TaskRuleError("task has unreadable dependencies")
    return current


def _transitive_dependencies(db, user_id: int, task_id: int) -> set:
    """Every task ID reachable by following dependencies (breadth-first)."""
    seen = set()
    queue = [task_id]
    while queue:
        current_id = queue.pop(0)
        if current_id in seen:
            continue
        seen.add(current_id)
        queue.extend(dep for dep in _read_dependencies(_owned_task(db, user_id, current_id)) if dep not in seen)
    seen.discard(task_id)
    return seen


def task_depth(db, user_id: int, task_id: int) -> int:
    """Delegation depth: 0 for a task with no dependencies, else 1 plus the
    deepest dependency's depth. Loops are rejected, not followed forever."""
    return _task_depth(db, user_id, task_id, set())


def _task_depth(db, user_id: int, task_id: int, seen: set) -> int:
    if task_id in seen:
        raise TaskRuleError("dependency loop detected")
    seen = set(seen) | {task_id}
    current = _read_dependencies(_owned_task(db, user_id, task_id))
    if not current:
        return 0
    return 1 + max(_task_depth(db, user_id, dep, seen) for dep in current)


def add_dependencies(db, *, user_id: int, task_id: int, depends_on: list, note: str = "") -> Task:
    """Link child tasks to a parent: the parent now depends on each child.

    Used when the Central Assistant delegates work to specialists. Both
    sides are audited: a "delegated" event on the parent and an "assigned"
    event on each newly linked child. Already-linked IDs are ignored.
    Loops and chains deeper than MAX_DELEGATION_DEPTH are rejected.
    """
    task = _owned_task(db, user_id, task_id)
    if not isinstance(depends_on, list) or any(not isinstance(dep, int) for dep in depends_on):
        raise TaskRuleError("depends_on must be a list of task IDs")
    current = _read_dependencies(task)
    fresh = []
    for dep in depends_on:
        if dep == task_id:
            raise TaskRuleError("a task cannot depend on itself")
        _owned_task(db, user_id, dep)
        if dep not in current and dep not in fresh:
            fresh.append(dep)
    if not fresh:
        return task
    for dep in fresh:
        if task_id in _transitive_dependencies(db, user_id, dep):
            raise TaskRuleError("dependency would create a loop")
    deepest = max(task_depth(db, user_id, dep) for dep in current + fresh)
    if deepest + 1 > MAX_DELEGATION_DEPTH:
        raise TaskRuleError(f"delegation too deep (max {MAX_DELEGATION_DEPTH})")
    task.dependencies = json.dumps(current + fresh)
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    _log_event(db, task.id, "delegated", "", "", note or f"now depends on tasks {fresh}")
    for dep in fresh:
        _log_event(db, dep, "assigned", "", "", f"assigned from task {task_id}")
    return task


EVENT_TYPES = ("created", "transition", "delegated", "assigned", "approval_requested", "approval_decided", "tool_call", "video_stage")


def log_event(db, *, user_id: int, task_id: int, event_type: str, note: str = "") -> None:
    """Append a custom audit event after checking task ownership."""
    _owned_task(db, user_id, task_id)
    if event_type not in EVENT_TYPES:
        raise TaskRuleError(f"unknown event type {event_type!r}")
    _log_event(db, task_id, event_type, "", "", note or "")


def get_task(db, user_id: int, task_id: int) -> Task:
    """Fetch one of the user's tasks."""
    return _owned_task(db, user_id, task_id)


def list_tasks(db, user_id: int) -> list:
    """List all of the user's tasks, oldest first."""
    return db.query(Task).filter_by(user_id=user_id).order_by(Task.id).all()


def list_events(db, user_id: int, task_id: int) -> list:
    """List a task's audit trail (ownership is checked first)."""
    _owned_task(db, user_id, task_id)
    return db.query(TaskEvent).filter_by(task_id=task_id).order_by(TaskEvent.id).all()
