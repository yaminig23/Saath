"""Central Assistant (Milestones 2 through 4, 9, and 10).

The Central Assistant is the one assistant the user talks to. It either
answers directly (via the model) or delegates to department managers:

- Reminder work: "remind me to <do X> <when>" (a real deadline is required)
- Briefing work: "give me my briefing" and similar cues

Multi-part requests ("do X and do Y") are split into assignments that go
to the Daily Life manager as one batch; the manager runs the specialists,
verifies their results, and reports back. Central verifies the manager's
report against its stored task row before answering. Nothing is claimed
unless it is stored: a reminder without an explicit time is never
created, and clauses the router does not understand are reported, not
faked.

Routing (ROUTER_NAME "router-1"): the live model plans the split from
structured output whenever it can; the rule-based router (explicit cue
words plus a small strict time parser, all UTC) backstops failures and
serves offline mode. Model output is never trusted raw: every assignment
is validated before anything is created.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import constitution
import kernel
import manager
import memory
import threads
from model_client import get_model_client
from util import parse_utc

SYSTEM_PROMPT = constitution.build_system_prompt("central-assistant")

CENTRAL_DEPARTMENT = "general"
CENTRAL_AGENT = "central-assistant"
ROUTER_NAME = "router-1"

# Direct answers are short by design; the cap keeps simple turns fast
# and cheap. Reasoning tokens count toward the same cap, so it stays
# well above a plain reply (the planner uses 2000, lessons 4000).
DIRECT_MAX_OUTPUT_TOKENS = 1500

# Clauses containing one of these (and no reminder cue) go to the organiser.
BRIEFING_CUES = (
    "briefing",
    "brief me",
    "my day",
    "organise",
    "organize",
    "due today",
    "what's due",
    "what is due",
    "summary",
)

# Split points between assignments: sentence punctuation, or a comma/"and"
# followed by a word that starts a new request. Plain "and" inside one
# request ("milk and eggs") does not split.
_SPLIT_PATTERN = re.compile(
    r"[.!?;]+|(?:,|\band\b)\s+"
    r"(?=(?:remind|brief|give|show|get|tell|what|organise|organize|summary)\b)",
    re.IGNORECASE,
)

_REMINDER_PATTERN = re.compile(r"remind(?:er| me)?(?: to)?\s*[:\-]?\s*(.+)", re.IGNORECASE | re.DOTALL)

_ISO_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)\s*(Z|\+00:00)?")
_RELATIVE_PATTERN = re.compile(
    r"\b(today|tomorrow)(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE
)
_IN_PATTERN = re.compile(r"\bin\s+(\d+)\s*(minutes?|hours?)\b", re.IGNORECASE)
_OFFSET_PATTERN = re.compile(r"\s*([+-]\d{2}:?\d{2})")


def _clean_objective(text: str) -> str:
    """Tidy leftover words after the time expression has been removed."""
    text = re.sub(r"\s+", " ", text).strip(" .,;:!?-")
    text = re.sub(r"\s+(on|at|by|for)$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .,;:!?-")


def _find_deadline(clause: str, now: datetime) -> tuple:
    """Find an explicit UTC deadline in a clause.

    Understands ISO datetimes ("2026-11-05T09:00:00+00:00"), "today/tomorrow
    at <time>", and "in N minutes/hours". Returns (iso_text, cleaned_clause);
    iso_text is "" when no unambiguous UTC time is present. Non-UTC offsets
    are refused rather than guessed.
    """
    match = _ISO_PATTERN.search(clause)
    if match:
        tail = _OFFSET_PATTERN.match(clause[match.end() :])
        if tail and tail.group(1) not in ("+00:00", "+0000"):
            match = None  # non-UTC offset: refuse to guess
    if match:
        day, clock = match.group(1), match.group(2)
        if len(clock) == 5:
            clock += ":00"
        try:
            parse_utc(f"{day}T{clock}+00:00")
        except ValueError:
            match = None
        else:
            cleaned = _clean_objective(clause[: match.start()] + " " + clause[match.end() :])
            return f"{day}T{clock}+00:00", cleaned
    match = _RELATIVE_PATTERN.search(clause)
    if match:
        day_word, hour_text, minute_text, meridiem = match.groups()
        hour, minute = int(hour_text), int(minute_text or 0)
        valid = minute <= 59 and (
            (meridiem is None and hour <= 23) or (meridiem is not None and 1 <= hour <= 12)
        )
        if valid:
            if meridiem is not None:
                hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
            day = now.date() + (timedelta(days=1) if day_word.lower() == "tomorrow" else timedelta(0))
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
            cleaned = _clean_objective(clause[: match.start()] + " " + clause[match.end() :])
            return moment.isoformat(), cleaned
    match = _IN_PATTERN.search(clause)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith("hour") else timedelta(minutes=amount)
        moment = (now + delta).replace(microsecond=0)
        cleaned = _clean_objective(clause[: match.start()] + " " + clause[match.end() :])
        return moment.isoformat(), cleaned
    return "", clause


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": ["reminder", "daily-organiser"]},
                    "objective": {"type": "string"},
                    "deadline": {"type": "string"},
                },
                "required": ["agent", "objective", "deadline"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assignments", "notes"],
    "additionalProperties": False,
}

PLAN_FORMAT = {"type": "json_schema", "name": "chat_plan", "schema": PLAN_SCHEMA, "strict": True}

# Plans are small JSON, but the model's reasoning tokens count toward the
# same cap (hundreds on easy calls), so this budget stays generous: a
# cramped cap returns status "incomplete" instead of a plan.
PLAN_MAX_OUTPUT_TOKENS = 2000

PLAN_PROMPT = """You are the Saath Central Assistant's planner. Read the user's message and split it into specialist assignments. Reply with the plan only.

Current UTC time: %%NOW%%. Every deadline you output must be ISO-8601 UTC (YYYY-MM-DDTHH:MM:SS+00:00), computed from that time.

Assignment kinds:
- {"agent": "reminder", "objective": "<what to do, no time words>", "deadline": "<ISO UTC>"} for dated nudges. Only emit when the message names an explicit time (today, tomorrow, a date, in N hours, and so on). Strip time words from the objective.
- {"agent": "daily-organiser", "objective": "", "deadline": ""} for briefing requests (briefing, my day, what is due, summary of the day, and so on).

Rules:
- Split multi-part requests ("do X and Y") into one assignment each, in order.
- Do not split one request joined by "and" ("milk and eggs" is one reminder).
- A reminder without an explicit time is NOT an assignment: explain in notes what is missing.
- Anything you do not understand goes in notes, never as a fake assignment.
- Plain questions or chat with no actionable request: empty assignments, empty notes."""

PLAN_PROMPT = PLAN_PROMPT + "\n\n" + constitution.CONSTITUTION_TAGLINE


def _validate_plan(raw) -> tuple:
    """Check a model-produced plan. Returns (assignments, notes).

    Invalid assignments are dropped with an explanatory note; a plan
    that is structurally unusable raises ValueError (caller falls back
    to the rule-based router).
    """
    if not isinstance(raw, dict):
        raise ValueError("plan is not an object")
    assignments_raw = raw.get("assignments")
    notes_raw = raw.get("notes")
    if not isinstance(assignments_raw, list) or not isinstance(notes_raw, list):
        raise ValueError("plan needs assignments and notes lists")
    assignments = []
    notes = [note for note in notes_raw if isinstance(note, str) and note.strip()]
    for item in assignments_raw:
        if not isinstance(item, dict):
            notes.append("dropped an unreadable assignment")
            continue
        agent = item.get("agent")
        if agent == "reminder":
            objective = (item.get("objective") or "").strip()
            deadline = (item.get("deadline") or "").strip()
            if not objective or not deadline:
                notes.append("a reminder was missing its message or time")
                continue
            try:
                parse_utc(deadline)
            except ValueError:
                notes.append(f"reminder time {deadline!r} was not usable")
                continue
            assignments.append({"agent": "reminder", "objective": objective, "deadline": deadline})
        elif agent == "daily-organiser":
            assignments.append({"agent": "daily-organiser"})
        else:
            notes.append(f"unknown specialist {agent!r} ignored")
    return assignments, notes


def _plan_with_model(client, text: str, *, now: datetime) -> dict:
    """Ask the model to plan the split. Returns the plan plus usage."""
    response = client.generate(
        system=PLAN_PROMPT.replace("%%NOW%%", now.isoformat()),
        messages=[{"role": "user", "content": text}],
        response_format=PLAN_FORMAT,
        max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
    )
    try:
        raw = json.loads(response.text)
    except ValueError:
        raise ValueError("planner did not return JSON")
    assignments, notes = _validate_plan(raw)
    return {
        "assignments": assignments,
        "notes": notes,
        "planned_by": client.name,
        "plan_tokens": (response.input_tokens, response.output_tokens),
    }


def _route_rules(text: str, *, now: datetime) -> dict:
    """Split a message into specialist assignments plus honest notes.

    Rule-based router: deterministic cue words plus a strict time parser.
    Returns {"assignments": [...], "notes": [...]}. An assignment is
    {"agent": ..., "objective": ..., "deadline": ...} (reminder) or
    {"agent": "daily-organiser"} (briefing). Notes explain clauses that
    need a time, need a message, or were not understood at all.
    """
    assignments, notes = [], []
    clauses = [part.strip() for part in _SPLIT_PATTERN.split(text) if part.strip()]
    for clause in clauses:
        lowered = clause.lower()
        if "remind" in lowered:
            found = _REMINDER_PATTERN.search(clause)
            remainder = found.group(1) if found else ""
            deadline, objective = _find_deadline(remainder, now)
            if deadline and objective:
                assignments.append({"agent": "reminder", "objective": objective, "deadline": deadline})
            elif not objective:
                notes.append(f"I need to know what to remind you about in '{clause}'.")
            else:
                notes.append(
                    f"I need a time for '{clause}' "
                    "(for example 'tomorrow at 9am' or '2026-11-05T09:00:00+00:00')."
                )
        elif any(cue in lowered for cue in BRIEFING_CUES):
            assignments.append({"agent": "daily-organiser"})
        else:
            notes.append(f"I didn't understand '{clause}'.")
    return {"assignments": assignments, "notes": notes, "planned_by": "rules", "plan_tokens": (0, 0)}


def _route(text: str, *, now: datetime, client=None, context: str = "") -> dict:
    """Plan the split: live model first, rule-based router as backstop.

    The model plans whenever the client supports it; any planning
    failure (network, bad JSON, unusable plan) falls back to the rules
    so chat never breaks. The fallback is recorded in evidence.
    Recent conversation context (when given) guides the model only;
    the rule backstop always sees just the new message.
    """
    client = client if client is not None else get_model_client()
    if getattr(client, "supports_planning", False):
        try:
            planner_text = text
            if (context or "").strip():
                planner_text = f"Recent conversation:\n{context.strip()}\n\nNew message: {text}"
            return _plan_with_model(client, planner_text, now=now)
        except Exception:
            plan = _route_rules(text, now=now)
            plan["planned_by"] = "rules-fallback"
            return plan
    plan = _route_rules(text, now=now)
    plan["planned_by"] = "rules"
    return plan


def _verify_manager(db, *, user_id: int, outcome: dict):
    """Re-read the manager's task row: trust the report, verify the record.

    Returns None when the report checks out, otherwise a plain-words error.
    """
    try:
        row = kernel.get_task(db, user_id, outcome["task_id"])
    except kernel.TaskNotFoundError:
        return "manager task row is missing"
    if row.status != "COMPLETED":
        return f"manager task is {row.status}, not COMPLETED"
    if not row.result or row.result != outcome["reply"]:
        return "manager report does not match its stored result"
    return None


def _answer_directly(db, *, user_id: int, text: str, history=None, notes=None) -> dict:
    """Handle one chat turn with no delegation: task row + model reply.

    `history` is earlier turns as model messages (oldest first); the
    current message is always last. Router `notes` (when any) ride
    along so the model can address what the router could not route
    instead of answering blind.
    """
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=text,
        department=CENTRAL_DEPARTMENT,
        agent=CENTRAL_AGENT,
    )
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="central assistant started"
    )
    content = text
    clean_notes = [note for note in (notes or []) if (note or "").strip()]
    if clean_notes:
        content += "\n\nRouter notes (address these if relevant):\n" + "\n".join(f"- {note}" for note in clean_notes)
    messages = list(history or []) + [{"role": "user", "content": content}]
    system = SYSTEM_PROMPT
    if memory.user_language(db, user_id=user_id) == "hi":
        system += "\n\nReply in Hindi (Devanagari script). Keep Saath's warm, simple tone."
    try:
        response = get_model_client().generate(
            system=system, messages=messages, max_output_tokens=DIRECT_MAX_OUTPUT_TOKENS
        )
    except Exception as exc:
        kernel.transition_task(
            db, user_id=user_id, task_id=task.id, to_status="FAILED", note=f"model error: {exc}"
        )
        raise
    evidence = (
        f"model={response.model} input_chars={response.input_chars} output_chars={response.output_chars} "
        f"input_tokens={response.input_tokens} output_tokens={response.output_tokens}"
    )
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="central assistant replied",
        result=response.text,
        evidence=evidence,
    )
    return {
        "task_id": task.id,
        "reply": response.text,
        "model": response.model,
        "input_chars": response.input_chars,
        "output_chars": response.output_chars,
        "children": [],
    }


def _delegate(db, *, user_id: int, text: str, plan: dict) -> dict:
    """Handle one chat turn by delegating to the Daily Life manager."""
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=text,
        department=CENTRAL_DEPARTMENT,
        agent=CENTRAL_AGENT,
    )
    kernel.transition_task(
        db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="central assistant started"
    )
    try:
        # M4: one department exists, so every assignment goes to the Daily
        # Life manager as a single batch. Each future department adds one
        # handoff here, and Central combines the managers' verified reports.
        outcome = manager.handle_department(db, user_id=user_id, assignments=plan["assignments"])
    except Exception as exc:
        kernel.transition_task(
            db, user_id=user_id, task_id=task.id, to_status="FAILED", note=f"manager error: {exc}"
        )
        raise
    problem = _verify_manager(db, user_id=user_id, outcome=outcome)
    kernel.add_dependencies(
        db,
        user_id=user_id,
        task_id=task.id,
        depends_on=[outcome["task_id"]],
        note=f"delegated to task {outcome['task_id']} (daily-life-manager)",
    )
    if problem is None:
        reply = outcome["reply"]
    else:
        reply = f"Daily Life manager failed: {problem}"
    if plan["notes"]:
        reply += "\n" + "\n".join(f"Note: {note}" for note in plan["notes"])
    plan_input_tokens, plan_output_tokens = plan["plan_tokens"]
    evidence = (
        f"manager={outcome['task_id']} manager_ok={problem is None} "
        f"assignments={len(plan['assignments'])} succeeded={outcome['succeeded']} failed={outcome['failed']} "
        f"children={[outcome['task_id']]} input_chars={len(text)} output_chars={len(reply)} "
        f"planned_by={plan['planned_by']} plan_input_tokens={plan_input_tokens} plan_output_tokens={plan_output_tokens}"
    )
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="central assistant combined results",
        result=reply,
        evidence=evidence,
    )
    return {
        "task_id": task.id,
        "reply": reply,
        "model": ROUTER_NAME,
        "input_chars": len(text),
        "output_chars": len(reply),
        "children": [outcome["task_id"]],
    }


def chat(db, *, user_id: int, message: str, thread_id=None) -> dict:
    """Handle one chat turn: delegate to the manager or answer directly.

    With thread_id, the turn joins that thread: recent turns guide the
    planner and the reply, and both sides are stored (replies trimmed to
    2000 characters in the thread; their full text lives on the task).
    Returns task_id, reply, model, input_chars, output_chars, children,
    and thread_id. Raises kernel.TaskRuleError if the message is empty.
    """
    text = (message or "").strip()
    if not text:
        raise kernel.TaskRuleError("message must not be empty")
    thread_messages = []
    if thread_id is not None:
        thread_messages = threads.get_thread(db, user_id=user_id, thread_id=thread_id)["messages"]
    history = threads.context_messages(thread_messages)
    plan = _route(text, now=datetime.now(timezone.utc), context=threads.context_text(thread_messages))
    if not plan["assignments"]:
        result = _answer_directly(db, user_id=user_id, text=text, history=history, notes=plan["notes"])
    else:
        result = _delegate(db, user_id=user_id, text=text, plan=plan)
    if thread_id is not None:
        threads.append_message(db, user_id=user_id, thread_id=thread_id, role="user", text=text[:2000], task_id=result["task_id"])
        threads.append_message(
            db, user_id=user_id, thread_id=thread_id, role="assistant",
            text=result["reply"][:2000], task_id=result["task_id"],
        )
        result["thread_id"] = thread_id
    else:
        result["thread_id"] = None
    return result
