"""Memory rules (Milestone 5).

Deterministic kernel-grade code: plain rules, no AI. One `memories` table
holds all five layers, separated by labels:

- user: preferences and routines about the user.
- shared: facts authorised agents may use.
- agent: one agent's expertise and past findings (learned only from
  completed tasks, never invented).
- task: progress notes attached to a task row.
- conversation: notes attached to a chat turn (chat turns are tasks too).

Need-to-know enforcement: every item carries a sensitivity tier derived
from its category, and PERMISSION_MATRIX decides which role sees which
layers, tiers, and departments. Consent gates sensitive categories for
every role; the owner endpoints always show the owner's own data (needed
to view, correct, and delete it).
"""

from datetime import datetime, timezone

import kernel
from models import Consent, Memory
from util import parse_utc, utcnow

LAYERS = ("user", "shared", "agent", "task", "conversation")

TIERS = ("normal", "sensitive")

# Categories whose memories need recorded consent before anyone (owner
# excepted) may store or agents may read them.
SENSITIVE_CATEGORIES = ("health", "location", "contacts", "financial")

MAX_KEY_LEN = 80
MAX_CATEGORY_LEN = 40
MAX_VALUE_LEN = 4000
MAX_TAG_LEN = 80

# Role -> what its recall() may see. Unknown roles see nothing (fail closed).
# "departments": "all" crosses departments, "own" means the requester's own
# department plus global ("") items. "own_agent_only" restricts the agent
# layer to the requester's own agent name.
PERMISSION_MATRIX = {
    "central": {
        "layers": {
            "user": ("normal", "sensitive"),
            "shared": ("normal", "sensitive"),
            "agent": ("normal", "sensitive"),
            "task": ("normal", "sensitive"),
            "conversation": ("normal", "sensitive"),
        },
        "departments": "all",
        "own_agent_only": False,
    },
    "manager": {
        "layers": {
            "user": ("normal", "sensitive"),
            "shared": ("normal", "sensitive"),
            "agent": ("normal", "sensitive"),
            "task": ("normal", "sensitive"),
            "conversation": ("normal", "sensitive"),
        },
        "departments": "own",
        "own_agent_only": False,
    },
    "specialist": {
        "layers": {
            "user": ("normal",),
            "shared": ("normal", "sensitive"),
            "agent": ("normal", "sensitive"),
            "task": ("normal", "sensitive"),
            "conversation": ("normal", "sensitive"),
        },
        "departments": "own",
        "own_agent_only": True,
    },
    "temporary": {
        "layers": {"shared": ("normal",)},
        "departments": "own",
        "own_agent_only": False,
    },
    "worker": {
        "layers": {"user": ("normal",)},
        "departments": "own",
        "own_agent_only": False,
    },
}


class MemoryError(kernel.KernelError):
    """A memory request broke a memory rule."""


class MemoryNotFoundError(kernel.TaskNotFoundError):
    """No such memory for this user (also used to hide others' memories)."""


def _consent_granted(db, user_id: int, category: str) -> bool:
    row = db.query(Consent).filter_by(user_id=user_id, category=category).first()
    return bool(row and row.granted)


def _tier_for(category: str) -> str:
    return "sensitive" if category in SENSITIVE_CATEGORIES else "normal"


def store_memory(
    db,
    *,
    user_id: int,
    layer: str,
    category: str,
    key: str,
    value: str,
    department: str = "",
    agent: str = "",
    source_task_id=None,
    expires_at: str = "",
) -> dict:
    """Store one memory, enforcing layer, consent, and learning rules."""
    if layer not in LAYERS:
        raise MemoryError(f"unknown layer {layer!r}; must be one of {', '.join(LAYERS)}")
    category = (category or "").strip().lower()
    if not category:
        raise MemoryError("category must not be empty")
    if len(category) > MAX_CATEGORY_LEN:
        raise MemoryError("category must be at most 40 characters")
    key = (key or "").strip()
    if not key:
        raise MemoryError("key must not be empty")
    if len(key) > MAX_KEY_LEN:
        raise MemoryError("key must be at most 80 characters")
    value = (value or "").strip()
    if not value:
        raise MemoryError("value must not be empty")
    if len(value) > MAX_VALUE_LEN:
        raise MemoryError("value must be at most 4000 characters")
    department = (department or "").strip()
    if len(department) > MAX_TAG_LEN:
        raise MemoryError("department must be at most 80 characters")
    agent = (agent or "").strip()
    if len(agent) > MAX_TAG_LEN:
        raise MemoryError("agent must be at most 80 characters")
    tier = _tier_for(category)
    if tier == "sensitive" and not _consent_granted(db, user_id, category):
        raise MemoryError(f"consent required for '{category}' memories (grant it first)")
    if layer == "agent":
        if not agent:
            raise MemoryError("agent layer needs the owning agent's name")
        if source_task_id is None:
            raise MemoryError("agent learning needs the completed source task's ID")
        source = kernel.get_task(db, user_id, source_task_id)
        if source.status != "COMPLETED":
            raise MemoryError("agent learning needs a COMPLETED source task")
    elif layer in ("task", "conversation"):
        if agent:
            raise MemoryError("agent name is only for the agent layer")
        if source_task_id is None:
            raise MemoryError(f"{layer} layer needs its task's ID")
        kernel.get_task(db, user_id, source_task_id)
    else:
        if agent:
            raise MemoryError("agent name is only for the agent layer")
        if source_task_id is not None:
            raise MemoryError(f"{layer} layer takes no source task")
    expires_at = (expires_at or "").strip()
    if expires_at:
        try:
            parse_utc(expires_at)
        except ValueError:
            raise MemoryError("expires_at must be ISO-8601 UTC text, e.g. 2026-11-01T09:00:00+00:00")
    existing = db.query(Memory).filter_by(user_id=user_id, layer=layer, key=key).first()
    if existing is not None:
        raise MemoryError(f"memory '{key}' already exists in the {layer} layer (correct or delete it first)")
    now = utcnow()
    row = Memory(
        user_id=user_id,
        layer=layer,
        category=category,
        tier=tier,
        key=key,
        value=value,
        department=department,
        agent=agent,
        source_task_id=source_task_id,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.to_dict()


def recall(db, *, user_id: int, role: str, department: str = "", agent: str = "", layers=None) -> list:
    """Return the memories a role may see, oldest first.

    Filters by the permission matrix, expiry, and consent. `layers`
    optionally narrows the recall to the layers the caller needs.
    Unknown roles see nothing.
    """
    entry = PERMISSION_MATRIX.get(role or "")
    if entry is None:
        return []
    if layers is None:
        wanted = set(entry["layers"])
    else:
        wanted = set(layers)
        unknown = wanted - set(LAYERS)
        if unknown:
            raise MemoryError(f"unknown layer(s) {sorted(unknown)}; must be one of {', '.join(LAYERS)}")
        wanted &= set(entry["layers"])
    now = datetime.now(timezone.utc)
    found = []
    rows = db.query(Memory).filter_by(user_id=user_id).order_by(Memory.id).all()
    for row in rows:
        if row.layer not in wanted:
            continue
        if row.tier not in entry["layers"][row.layer]:
            continue
        if entry["departments"] == "own" and row.department not in ("", department):
            continue
        if entry["own_agent_only"] and row.layer == "agent" and row.agent != agent:
            continue
        if row.expires_at:
            try:
                moment = parse_utc(row.expires_at)
            except ValueError:
                continue
            if moment <= now:
                continue
        if row.tier == "sensitive" and not _consent_granted(db, user_id, row.category):
            continue
        found.append(row.to_dict())
    return found


def user_language(db, *, user_id: int) -> str:
    """The user's chosen reply language: "hi" when they stored
    language=hindi, else "en". Missing or odd values fall back to
    English — never raises, so chat always answers something."""
    try:
        row = (
            db.query(Memory)
            .filter_by(user_id=user_id, layer="user", category="preferences", key="language")
            .first()
        )
    except Exception:
        return "en"
    if row is None:
        return "en"
    return "hi" if (row.value or "").strip().lower().startswith("hi") else "en"


def list_memories(db, *, user_id: int) -> list:
    """Owner view: every stored memory, oldest first, with consent status."""
    rows = db.query(Memory).filter_by(user_id=user_id).order_by(Memory.id).all()
    found = []
    for row in rows:
        body = row.to_dict()
        body["blocked_by_consent"] = row.tier == "sensitive" and not _consent_granted(db, user_id, row.category)
        found.append(body)
    return found


def _owned_memory(db, user_id: int, memory_id: int) -> Memory:
    row = db.query(Memory).filter_by(id=memory_id, user_id=user_id).first()
    if row is None:
        raise MemoryNotFoundError("memory not found")
    return row


def correct_memory(db, *, user_id: int, memory_id: int, value: str) -> dict:
    """Correct one memory's value (category and key never change)."""
    row = _owned_memory(db, user_id, memory_id)
    value = (value or "").strip()
    if not value:
        raise MemoryError("value must not be empty")
    if len(value) > MAX_VALUE_LEN:
        raise MemoryError("value must be at most 4000 characters")
    row.value = value
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row.to_dict()


def delete_memory(db, *, user_id: int, memory_id: int) -> dict:
    """Delete one memory; deletion propagates across layers by key.

    Returns {"deleted": [ids]} covering every layer the key appeared in.
    """
    row = _owned_memory(db, user_id, memory_id)
    doomed = db.query(Memory).filter_by(user_id=user_id, key=row.key).order_by(Memory.id).all()
    deleted = [item.id for item in doomed]
    for item in doomed:
        db.delete(item)
    db.commit()
    return {"deleted": deleted}


def set_consent(db, *, user_id: int, category: str, granted: bool) -> dict:
    """Record the user's grant or revoke for one category."""
    category = (category or "").strip().lower()
    if not category:
        raise MemoryError("category must not be empty")
    row = db.query(Consent).filter_by(user_id=user_id, category=category).first()
    if row is None:
        row = Consent(user_id=user_id, category=category, granted=int(bool(granted)), updated_at=utcnow())
        db.add(row)
    else:
        row.granted = int(bool(granted))
        row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row.to_dict()


def list_consents(db, *, user_id: int) -> list:
    """Every recorded consent choice, oldest category first."""
    rows = db.query(Consent).filter_by(user_id=user_id).order_by(Consent.category).all()
    return [row.to_dict() for row in rows]
