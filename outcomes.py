"""Outcome planner (P6 operator).

Goals become owned outcomes: a parent task stays open while child step
tasks complete, the solution path is recorded in evidence, revisions
supersede honestly (old steps BLOCKED, never deleted), and follow-ups
ride the real reminder machinery. The model proposes; validation and
the kernel dispose. Consequence-heavy steps still execute through the
existing gated tools, so approvals keep working untouched.
"""

import json
import re

import kernel
import model_client
import providers
import reminders
from model_client import ModelError

OUTCOME_DEPARTMENT = "general"
OUTCOME_AGENT = "outcome-planner"
STEP_AGENT = "outcome-step"

MAX_GOAL_LEN = 500
MAX_CONTEXT_LEN = 2000
MAX_CHANGE_LEN = 1000
MAX_PLACE_LEN = 120

PLAN_MAX_OUTPUT_TOKENS = 2000

STEP_KINDS = ("task", "reminder", "guidance", "human_help", "external")

_STEP_ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "detail": {"type": "string"},
        "kind": {"type": "string"},
        "deadline": {"type": "string"},
    },
    "required": ["title", "detail", "kind", "deadline"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": _STEP_ITEM},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["steps", "questions"],
    "additionalProperties": False,
}

PLAN_FORMAT = {"type": "json_schema", "name": "outcome_plan", "schema": PLAN_SCHEMA, "strict": True}

REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {"type": "integer"}},
        "add": {"type": "array", "items": _STEP_ITEM},
        "note": {"type": "string"},
    },
    "required": ["keep", "add", "note"],
    "additionalProperties": False,
}

REVISE_FORMAT = {"type": "json_schema", "name": "outcome_revise", "schema": REVISE_SCHEMA, "strict": True}

PLAN_PROMPT = """You are Saath's outcome planner. Turn the user's goal into 2-6 concrete steps and up to 3 clarifying questions. Reply with the plan JSON only.

Step kinds (pick exactly one each):
- task: something Saath or the user can just do.
- reminder: a dated nudge; needs a deadline in ISO-8601 UTC, or "" when no date is known yet.
- guidance: teach or guide the user through it together.
- human_help: needs a real person (say who in detail).
- external: needs a service Saath cannot reach (name it in detail).

Rules: only use these five kinds; keep titles short; never invent bookings, availability, or completions."""

REVISE_PROMPT = """You are Saath's outcome planner revising an open plan. Given the current steps and the user's change, reply with keep (step task IDs to keep), add (new steps, same shape as planning), and note (one line on what changed). Never drop a COMPLETED step. Reply with the revision JSON only."""


class OutcomeError(kernel.KernelError):
    """An outcome request broke an outcome rule."""


def _validate_steps(raw_steps) -> tuple:
    """Check model-produced steps. Returns (steps, dropped_count)."""
    steps = []
    dropped = 0
    for entry in raw_steps or []:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        title = str(entry.get("title", "")).strip()
        detail = str(entry.get("detail", "")).strip()
        kind = str(entry.get("kind", "")).strip()
        deadline = str(entry.get("deadline", "")).strip()
        if not title or kind not in STEP_KINDS:
            dropped += 1
            continue
        steps.append({"title": title[:200], "detail": detail[:1000], "kind": kind, "deadline": deadline[:32]})
        if len(steps) >= 8:
            break
    return steps, dropped


def _derive_path(kinds) -> str:
    """The solution path, derived deterministically from the step kinds."""
    kinds = list(kinds)
    if "external" in kinds:
        return "E-offline"
    if "human_help" in kinds:
        return "D-human"
    if "guidance" in kinds:
        return "B-together"
    return "A-do"


def _stub_plan(goal: str) -> tuple:
    """Honest offline framework (the stub model name marks it downstream)."""
    steps = [
        {
            "title": f"Define done for: {goal[:80]}",
            "detail": "Write one sentence describing the finished outcome.",
            "kind": "task",
            "deadline": "",
        },
        {
            "title": "List the pieces",
            "detail": "Break the goal into small pieces you can do one at a time.",
            "kind": "guidance",
            "deadline": "",
        },
        {
            "title": "Do the first piece",
            "detail": "Complete the easiest piece, then ask Saath to plan the rest with the live model.",
            "kind": "task",
            "deadline": "",
        },
    ]
    return steps, ["What does the finished outcome look like?"]


def _create_step(db, *, user_id: int, step: dict) -> dict:
    """One plan step becomes a real task (or a real reminder with a date)."""
    objective = f"{step['title']}: {step['detail']}"[:400]
    if step["kind"] == "reminder" and step["deadline"]:
        try:
            return reminders.create_reminder(db, user_id=user_id, message=objective, deadline=step["deadline"])
        except kernel.TaskRuleError:
            pass  # bad date from the model: fall through to a plain dateless step
    task = kernel.create_task(
        db, user_id=user_id, objective=objective, department=OUTCOME_DEPARTMENT, agent=STEP_AGENT, priority="normal"
    )
    return task.to_dict()


def _read_kind_map(evidence: str) -> dict:
    """Recover the step-id → kind map stored in outcome evidence."""
    try:
        marker = "kinds="
        position = evidence.rfind(marker)
        if position < 0:
            return {}
        return json.loads(evidence[position + len(marker) :].split(" ", 1)[0])
    except (ValueError, AttributeError):
        return {}


_REVISION_MARK = re.compile(r"^revision \d+:")


def _revision_count(db, *, user_id: int, outcome_id: int) -> int:
    """How many revisions ran before, counted from "revision N:" audit notes."""
    events = kernel.list_events(db, user_id, outcome_id)
    return sum(1 for event in events if _REVISION_MARK.match(event.note or ""))


def plan_goal(db, *, user_id: int, goal: str, context: str = "", place: str = "") -> dict:
    """Turn a goal into an owned outcome: open parent, real child steps,
    recorded questions, and a derived solution path in evidence."""
    clean_goal = (goal or "").strip()
    if not clean_goal:
        raise OutcomeError("goal must not be empty")
    if len(clean_goal) > MAX_GOAL_LEN:
        raise OutcomeError("goal must be at most 500 characters")
    clean_context = (context or "").strip()
    if len(clean_context) > MAX_CONTEXT_LEN:
        raise OutcomeError("context must be at most 2000 characters")
    clean_place = (place or "").strip()
    if len(clean_place) > MAX_PLACE_LEN:
        raise OutcomeError("place must be at most 120 characters")
    weather_note = ""
    if clean_place:
        try:
            sky = providers.weather_now(clean_place)
            weather_note = (
                f"Weather in {sky['place']}: {sky['summary']}, {sky['temp_c']}°C "
                f"(high {sky['high_c']}°C, rain {sky['rain_pct']}%)."
            )
        except Exception as exc:
            weather_note = f"Weather unavailable ({exc}); planning without it."
    client = model_client.get_model_client()
    parent = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Outcome: {clean_goal[:160]}",
        department=OUTCOME_DEPARTMENT,
        agent=OUTCOME_AGENT,
        priority="normal",
    )
    kernel.transition_task(db, user_id=user_id, task_id=parent.id, to_status="IN_PROGRESS", note="outcome planning started")
    try:
        if not client.supports_planning:
            steps, questions = _stub_plan(clean_goal)
            model_name = client.name
            token_note = "tokens=0/0"
        else:
            message = f"Goal: {clean_goal}"
            if clean_context:
                message += f"\n\nContext: {clean_context}"
            if weather_note:
                message += f"\n\n{weather_note}"
            response = client.generate(
                system=PLAN_PROMPT,
                messages=[{"role": "user", "content": message}],
                response_format=PLAN_FORMAT,
                max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
            )
            try:
                raw = json.loads(response.text)
            except ValueError:
                raise ModelError("planner did not return JSON")
            if not isinstance(raw, dict):
                raise ModelError("planner did not return a plan")
            steps, _dropped = _validate_steps(raw.get("steps"))
            if not steps:
                raise ModelError("planner returned no usable steps")
            questions = [
                question.strip()[:300] for question in (raw.get("questions") or []) if isinstance(question, str) and question.strip()
            ][:3]
            model_name = response.model
            token_note = f"tokens={response.input_tokens}/{response.output_tokens}"
        created = []
        for step in steps:
            child = _create_step(db, user_id=user_id, step=step)
            created.append({"task_id": child["id"], "title": step["title"], "kind": step["kind"]})
        kernel.add_dependencies(
            db,
            user_id=user_id,
            task_id=parent.id,
            depends_on=[item["task_id"] for item in created],
            note="outcome plan with " + ", ".join(f"task {item['task_id']} ({item['kind']})" for item in created),
        )
        for question in questions:
            kernel.log_event(db, user_id=user_id, task_id=parent.id, event_type="transition", note=f"open question: {question}")
    except Exception as exc:
        kernel.transition_task(
            db, user_id=user_id, task_id=parent.id, to_status="FAILED",
            note="outcome planning failed", result=f"failed: {exc}",
        )
        raise
    path = _derive_path([step["kind"] for step in steps])
    kinds = {str(item["task_id"]): item["kind"] for item in created}
    parent_row = kernel.get_task(db, user_id, parent.id)
    parent_row.evidence = (
        f"model={model_name} path={path} steps={len(created)} questions={len(questions)} "
        f"{token_note} kinds={json.dumps(kinds, sort_keys=True)}"
    )
    db.commit()
    return {
        "task_id": parent.id,
        "goal": clean_goal,
        "steps": created,
        "questions": questions,
        "path": path,
        "model": model_name,
        "weather_note": weather_note,
    }


def revise_outcome(db, *, user_id: int, outcome_id: int, change: str) -> dict:
    """Revise an open outcome: keep steps, add steps, BLOCK superseded
    PENDING steps (history is never deleted)."""
    parent = kernel.get_task(db, user_id, outcome_id)
    if parent.agent != OUTCOME_AGENT:
        raise OutcomeError("only outcome plans can be revised")
    if parent.status != "IN_PROGRESS":
        raise OutcomeError("only open outcomes can be revised")
    clean_change = (change or "").strip()
    if not clean_change:
        raise OutcomeError("change must not be empty")
    if len(clean_change) > MAX_CHANGE_LEN:
        raise OutcomeError("change must be at most 1000 characters")
    try:
        child_ids = [int(item) for item in json.loads(parent.dependencies)]
    except ValueError:
        child_ids = []
    children = []
    for child_id in child_ids:
        try:
            children.append(kernel.get_task(db, user_id, child_id))
        except kernel.TaskNotFoundError:
            continue
    completed = {child.id for child in children if child.status == "COMPLETED"}
    failed = [child for child in children if child.status == "FAILED"]
    client = model_client.get_model_client()
    if not client.supports_planning:
        keep = [child.id for child in children]
        add = [
            {
                "title": f"Handle: {clean_change[:80]}",
                "detail": clean_change[:500],
                "kind": "task",
                "deadline": "",
            }
        ]
        note = "stub revision"
        model_name = client.name
    else:
        current = "\n".join(f"- [{child.id}] {child.status} {child.objective[:120]}" for child in children) or "(no steps)"
        response = client.generate(
            system=REVISE_PROMPT,
            messages=[{"role": "user", "content": f"Current steps:\n{current}\n\nChange: {clean_change}"}],
            response_format=REVISE_FORMAT,
            max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
        )
        try:
            raw = json.loads(response.text)
        except ValueError:
            raise ModelError("reviser did not return JSON")
        if not isinstance(raw, dict):
            raise ModelError("reviser did not return a revision")
        known = {child.id for child in children}
        raw_keep = raw.get("keep") or []
        ignored = [item for item in raw_keep if item not in known]
        keep = sorted(set(item for item in raw_keep if isinstance(item, int) and item in known) | completed)
        add, _dropped = _validate_steps(raw.get("add"))
        note = str(raw.get("note", "")).strip()[:300] or "plan revised"
        if ignored:
            note += f" (ignored unknown steps {ignored})"
        model_name = response.model
    blocked = []
    for child in children:
        if child.id not in keep and child.status == "PENDING":
            kernel.transition_task(db, user_id=user_id, task_id=child.id, to_status="BLOCKED", note="superseded by revision")
            blocked.append(child.id)
    created = []
    for step in add:
        child = _create_step(db, user_id=user_id, step=step)
        created.append({"task_id": child["id"], "title": step["title"], "kind": step["kind"]})
    if created:
        kernel.add_dependencies(
            db,
            user_id=user_id,
            task_id=parent.id,
            depends_on=[item["task_id"] for item in created],
            note="revision added " + ", ".join(f"task {item['task_id']}" for item in created),
        )
    kinds = {key: value for key, value in _read_kind_map(parent.evidence).items() if int(key) in keep}
    for item in created:
        kinds[str(item["task_id"])] = item["kind"]
    path = "C-alternative" if failed else _derive_path(kinds.values())
    revision = _revision_count(db, user_id=user_id, outcome_id=parent.id) + 1
    kernel.log_event(db, user_id=user_id, task_id=parent.id, event_type="transition", note=f"revision {revision}: {note}")
    parent_row = kernel.get_task(db, user_id, parent.id)
    parent_row.evidence = (
        f"{parent_row.evidence} revision={revision} path={path} kinds={json.dumps(kinds, sort_keys=True)}"
    )
    db.commit()
    return {
        "task_id": parent.id,
        "kept": keep,
        "added": created,
        "blocked": blocked,
        "path": path,
        "note": note,
        "model": model_name,
    }


def schedule_followup(db, *, user_id: int, outcome_id: int, when: str, note: str) -> dict:
    """Attach a real reminder to an outcome, so the worker follows up."""
    parent = kernel.get_task(db, user_id, outcome_id)
    if parent.agent != OUTCOME_AGENT:
        raise OutcomeError("only outcome plans take follow-ups")
    clean_note = (note or "").strip()
    if not clean_note:
        raise OutcomeError("note must not be empty")
    reminder = reminders.create_reminder(db, user_id=user_id, message=clean_note[:400], deadline=(when or "").strip())
    kernel.add_dependencies(
        db, user_id=user_id, task_id=parent.id, depends_on=[reminder["id"]], note=f"follow-up task {reminder['id']}"
    )
    return {"outcome_id": parent.id, "reminder": reminder}
