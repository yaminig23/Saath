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

The manager also runs read-only tool lookups (agent "tool") as audited
child tasks via the tool-runner: registered harmless tools only, each
result summarised in plain words and verified against its stored row.
"""

import json

import kernel
import organiser
import reminders
import tools

MANAGER_DEPARTMENT = "daily-life"
MANAGER_AGENT = "daily-life-manager"
TOOL_RUNNER_AGENT = "tool-runner"


def _describe_assignment(assignment: dict) -> str:
    """One assignment in short plain words (for task objectives)."""
    if assignment["agent"] == "reminder":
        return f"reminder '{assignment['objective']}'"
    if assignment["agent"] == "tool":
        return f"{assignment['objective']} lookup"
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
    if assignment["agent"] == "tool":
        if row.agent != TOOL_RUNNER_AGENT:
            return "tool task was handled by the wrong agent"
        if row.status != "COMPLETED" or not row.result:
            return "tool task is not completed with a stored result"
        if row.result != result.get("summary"):
            return "tool report does not match its stored result"
        return None
    return f"unknown agent {assignment['agent']!r}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _summarise_places(result: dict) -> str:
    places = result.get("places")
    if not isinstance(places, list):
        raise ValueError("maps_search_places returned an unreadable result")
    query = result.get("query", "")
    if not places:
        return f"Google found no live places for '{query}'."
    lines = [f"Live places for '{query}':"]
    shown = 0
    for place in places[:5]:
        if not isinstance(place, dict):
            continue
        shown += 1
        bit = str(place.get("name", "") or "(unnamed)")
        address = str(place.get("address", "") or "").strip()
        if address:
            bit += f" — {address}"
        extras = []
        if place.get("rating") is not None:
            extras.append(f"rated {place['rating']}")
        if place.get("open_now") is True:
            extras.append("open now")
        elif place.get("open_now") is False:
            extras.append("closed now")
        if extras:
            bit += f" ({', '.join(extras)})"
        lines.append(f"{shown}. {bit}")
    if not shown:
        return f"Google found no live places for '{query}'."
    return "\n".join(lines)


def _summarise_route(result: dict) -> str:
    detail = result.get("Detail")
    if detail:
        return str(detail)
    origin = str(result.get("origin", "") or "")
    destination = str(result.get("destination", "") or "")
    head = f"{origin} to {destination}".strip() or "Route"
    bits = [str(result.get("distance", "") or "").strip(), str(result.get("duration", "") or "").strip()]
    bits = [bit for bit in bits if bit]
    if bits:
        head += f": {' '.join(bits)}"
    head += f" by {result.get('mode', 'driving')} (live Google data)."
    lines = [head]
    steps = [step.strip() for step in (result.get("steps") or []) if isinstance(step, str) and step.strip()]
    for index, step in enumerate(steps[:6], 1):
        lines.append(f"{index}. {step}")
    return "\n".join(lines)


def _summarise_day(result: dict) -> str:
    events = result.get("events")
    if not isinstance(events, list):
        raise ValueError("calendar_read_day returned an unreadable result")
    date = result.get("date", "")
    zone = result.get("time_zone", "")
    if not events:
        return f"Nothing on {date} ({zone}) — the day is free."
    lines = [f"{_plural(len(events), 'event')} on {date} ({zone}):"]
    for event in events[:10]:
        if not isinstance(event, dict):
            continue
        start = str(event.get("start", "") or "")
        when = start[11:16] if len(start) > 16 else start
        title = str(event.get("title", "") or "(no title)")
        lines.append(f"- {when} — {title}" if when else f"- {title}")
    if len(events) > 10:
        lines.append(f"...and {len(events) - 10} more.")
    return "\n".join(lines)


def _summarise_stats(result: dict) -> str:
    try:
        active = int(result.get("active", 0))
        completed = int(result.get("completed", 0))
        failed = int(result.get("failed", 0))
    except (TypeError, ValueError):
        raise ValueError("task_stats returned an unreadable result")
    return (
        f"You have {_plural(active, 'active task')}, "
        f"{_plural(completed, 'completed task')}, {_plural(failed, 'failed task')}."
    )


def _summarise_tool(name: str, result) -> str:
    """One tool result in plain words. Raises ValueError when unreadable."""
    if not isinstance(result, dict):
        raise ValueError(f"{name} returned an unreadable result")
    if name == "maps_search_places":
        return _summarise_places(result)
    if name == "maps_route":
        return _summarise_route(result)
    if name == "calendar_read_day":
        return _summarise_day(result)
    if name == "task_stats":
        return _summarise_stats(result)
    return f"{name} returned: {json.dumps(result, sort_keys=True)[:500]}"


def _run_tool(db, *, user_id: int, assignment: dict, trace_id: str = "") -> dict:
    """Run one read-only tool call as an audited child task.

    Never raises: every outcome (including refusal to run) is a
    structured result. Harmless registered tools only — anything else
    is refused before it can execute.
    """
    name = assignment.get("objective", "")
    params = assignment.get("params") or {}
    spec = tools.TOOLS.get(name)
    if spec is None:
        return {"agent": "tool", "ok": False, "task_id": None, "error": f"tool {name!r} is not available"}
    if spec.consequential:
        return {
            "agent": "tool",
            "ok": False,
            "task_id": None,
            "error": f"tool {name!r} needs your approval and cannot run via chat",
        }
    objective = f"{name} {json.dumps(params, sort_keys=True)}"
    if len(objective) > kernel.MAX_OBJECTIVE_LEN:
        objective = objective[: kernel.MAX_OBJECTIVE_LEN - 3] + "..."
    child = kernel.create_task(
        db, user_id=user_id, objective=objective, department=MANAGER_DEPARTMENT, agent=TOOL_RUNNER_AGENT
    )
    kernel.transition_task(db, user_id=user_id, task_id=child.id, to_status="IN_PROGRESS", note=f"tool {name} started")
    try:
        outcome = tools.request_tool(db, user_id=user_id, task_id=child.id, tool_name=name, args=params)
    except Exception as exc:
        try:
            row = kernel.get_task(db, user_id, child.id)
            if row.status == "IN_PROGRESS":
                kernel.transition_task(
                    db, user_id=user_id, task_id=child.id, to_status="FAILED",
                    note=f"{name} failed", result=f"failed: {exc}",
                )
        except Exception:
            pass
        return {"agent": "tool", "ok": False, "task_id": child.id, "error": f"{name} failed: {exc}"}
    try:
        summary = _summarise_tool(name, outcome["result"])
    except ValueError as exc:
        kernel.transition_task(
            db, user_id=user_id, task_id=child.id, to_status="FAILED",
            note=f"{name} unreadable", result=f"failed: {exc}",
        )
        return {"agent": "tool", "ok": False, "task_id": child.id, "error": str(exc)}
    kernel.transition_task(
        db, user_id=user_id, task_id=child.id, to_status="COMPLETED",
        note=f"{name} done", result=summary, evidence=f"trace={trace_id} tool={name}",
    )
    result = {"agent": "tool", "task_id": child.id, "summary": summary}
    problem = _verify_result(db, user_id=user_id, assignment=assignment, result=result)
    if problem is None:
        result["ok"] = True
    else:
        result["ok"] = False
        result["error"] = problem
    return result


def _run_assignment(db, *, user_id: int, assignment: dict, trace_id: str = "") -> dict:
    """Execute one assignment via its specialist and verify the outcome.

    Never raises: failures are structured results so the combined reply
    can report them honestly. Every created child task keeps its ID in
    the result so it stays linked even when verification fails.
    """
    agent = assignment.get("agent", "")
    if agent not in ("reminder", "daily-organiser", "tool"):
        return {"agent": agent, "ok": False, "task_id": None, "error": f"unknown agent {agent!r}"}
    if agent == "tool":
        return _run_tool(db, user_id=user_id, assignment=assignment, trace_id=trace_id)
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
    if result["agent"] == "tool":
        return f"Lookup done (task {result['task_id']}):\n{result['summary']}"
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


def handle_department(db, *, user_id: int, assignments: list, trace_id: str = "") -> dict:
    """Run one batch of Daily Life assignments through the specialists.

    Returns a dict with task_id, ok (every assignment verified), reply,
    results, children, succeeded, and failed. The manager's own work is a
    real audited task linked to every specialist child it created.
    `trace_id` (from the chat turn) is stamped on the evidence trail.
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
    results = [_run_assignment(db, user_id=user_id, assignment=item, trace_id=trace_id) for item in assignments]
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
        f"trace={trace_id} assignments={len(results)} succeeded={succeeded} failed={len(results) - succeeded} "
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
