"""Daily Life manager (Milestone 4).

The middle level of the hierarchy: the Central Assistant hands whole
batches of Daily Life work to this manager, which splits each batch into
one child task per specialist (Reminder Agent, Daily Organiser Agent),
verifies every claimed result against its stored task row, and combines
the verified outcomes into one department reply for Central.

Verification is the manager's real job, not a formality: a reminder only
counts when its stored row carries the assigned objective and deadline
and is still waiting (PENDING); a briefing only counts when its task is
COMPLETED with a non-empty stored result. Anything else is reported as
a failure, never passed upward as success.
"""

import kernel
import organiser
import reminders

MANAGER_DEPARTMENT = "daily-life"
MANAGER_AGENT = "daily-life-manager"


def _describe_assignment(assignment: dict) -> str:
    """One assignment in short plain words (for task objectives)."""
    if assignment["agent"] == "reminder":
        return f"reminder '{assignment['objective']}'"
    return assignment["agent"]


def _verify_result(db, *, user_id: int, assignment: dict, result: dict):
    """Check a specialist's claimed result against its stored task row.

    Returns None when the claim checks out, otherwise a plain-words error.
    """
    try:
        row = kernel.get_task(db, user_id, result["task_id"])
    except kernel.TaskNotFoundError:
        return "specialist task row is missing"
    if assignment["agent"] == "reminder":
        if row.objective != assignment["objective"] or row.deadline != assignment["deadline"]:
            return "stored reminder does not match the assignment"
        if row.status != "PENDING":
            return f"reminder is {row.status}, expected PENDING"
        return None
    if assignment["agent"] == "daily-organiser":
        if row.agent != "daily-organiser":
            return "briefing task was handled by the wrong agent"
        if row.status != "COMPLETED" or not row.result:
            return "briefing task is not completed with a stored result"
        return None
    return f"unknown agent {assignment['agent']!r}"


def _run_assignment(db, *, user_id: int, assignment: dict) -> dict:
    """Execute one assignment via its specialist and verify the outcome.

    Never raises: failures are structured results so the combined reply
    can report them honestly. Every created child task keeps its ID in
    the result so it stays linked even when verification fails.
    """
    agent = assignment["agent"]
    if agent not in ("reminder", "daily-organiser"):
        return {"agent": agent, "ok": False, "task_id": None, "error": f"unknown agent {agent!r}"}
    try:
        if agent == "reminder":
            child = reminders.create_reminder(
                db, user_id=user_id, message=assignment["objective"], deadline=assignment["deadline"]
            )
            result = {
                "agent": agent,
                "task_id": child["id"],
                "objective": child["objective"],
                "deadline": child["deadline"],
            }
        else:
            briefing = organiser.daily_briefing(db, user_id=user_id)
            result = {
                "agent": agent,
                "task_id": briefing["task_id"],
                "summary": briefing["summary"],
            }
    except Exception as exc:
        return {"agent": agent, "ok": False, "task_id": None, "error": str(exc)}
    problem = _verify_result(db, user_id=user_id, assignment=assignment, result=result)
    if problem is None:
        result["ok"] = True
    else:
        result["ok"] = False
        result["error"] = problem
    return result


def _describe_result(result: dict) -> str:
    """One assignment's verified outcome in plain words."""
    if not result["ok"]:
        return f"{result['agent']} failed: {result['error']}"
    if result["agent"] == "reminder":
        return f"Reminder set: '{result['objective']}' due {result['deadline']} (task {result['task_id']})."
    return f"Briefing ready (task {result['task_id']}):\n{result['summary']}"


def _compose_response(results: list) -> str:
    """Combine verified results into one department reply."""
    if len(results) == 1 and results[0]["ok"]:
        return _describe_result(results[0])
    succeeded = sum(1 for item in results if item["ok"])
    word = "assignment" if len(results) == 1 else "assignments"
    lines = [f"Done — {len(results)} {word}, {succeeded} succeeded:"]
    lines.extend(f"{index}. {_describe_result(item)}" for index, item in enumerate(results, 1))
    return "\n".join(lines)


def handle_department(db, *, user_id: int, assignments: list) -> dict:
    """Run one batch of Daily Life assignments through the specialists.

    Returns a dict with task_id, ok (every assignment verified), reply,
    results, children, succeeded, and failed. The manager's own work is a
    real audited task linked to every specialist child it created.
    """
    summary = ", ".join(_describe_assignment(item) for item in assignments)
    objective = f"Daily Life: {summary}"
    if len(objective) > kernel.MAX_OBJECTIVE_LEN:
        objective = objective[: kernel.MAX_OBJECTIVE_LEN - 3] + "..."
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=objective,
        department=MANAGER_DEPARTMENT,
        agent=MANAGER_AGENT,
    )
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="daily life manager started"
    )
    results = [_run_assignment(db, user_id=user_id, assignment=item) for item in assignments]
    children = [item["task_id"] for item in results if item["task_id"] is not None]
    if children:
        kernel.add_dependencies(
            db,
            user_id=user_id,
            task_id=task.id,
            depends_on=children,
            note="delegated to " + ", ".join(f"task {item['task_id']} ({item['agent']})" for item in results if item["task_id"] is not None),
        )
    reply = _compose_response(results)
    succeeded = sum(1 for item in results if item["ok"])
    evidence = (
        f"assignments={len(results)} succeeded={succeeded} failed={len(results) - succeeded} "
        f"children={children} input_chars={len(summary)} output_chars={len(reply)}"
    )
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="daily life manager combined results",
        result=reply,
        evidence=evidence,
    )
    return {
        "task_id": task.id,
        "ok": succeeded == len(results),
        "reply": reply,
        "results": results,
        "children": children,
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
    }
