"""Conversation threads (P6 operator).

Multi-turn memory: each thread keeps user/assistant turns linked to the
tasks they produced, so Central answers with context instead of treating
every message as a stranger. History is permission-scoped (owner only)
and capped; the model sees recent turns, never the whole database.
"""

import kernel
from models import ConversationMessage, ConversationThread
from util import utcnow

MAX_TITLE_LEN = 120
MAX_MESSAGE_LEN = 2000
CONTEXT_TURNS = 3


class ThreadError(kernel.KernelError):
    """A thread request broke a thread rule."""


def _owned_thread(db, *, user_id: int, thread_id: int):
    thread = db.query(ConversationThread).filter_by(id=thread_id, user_id=user_id).first()
    if thread is None:
        raise kernel.TaskNotFoundError("thread not found")
    return thread


def create_thread(db, *, user_id: int, title: str = "") -> dict:
    """Start a thread. Titles over the cap are trimmed, never rejected."""
    clean = (title or "").strip()[:MAX_TITLE_LEN]
    now = utcnow()
    row = ConversationThread(user_id=user_id, title=clean or "Conversation", created_at=now, updated_at=now)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "title": row.title, "created_at": row.created_at, "updated_at": row.updated_at}


def list_threads(db, *, user_id: int) -> list:
    """The user's threads, most recently active first."""
    rows = (
        db.query(ConversationThread)
        .filter_by(user_id=user_id)
        .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.desc())
        .all()
    )
    return [{"id": row.id, "title": row.title, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]


def get_thread(db, *, user_id: int, thread_id: int, limit: int = 50) -> dict:
    """One thread with its messages, oldest first (capped at `limit`)."""
    thread = _owned_thread(db, user_id=user_id, thread_id=thread_id)
    rows = (
        db.query(ConversationMessage)
        .filter_by(thread_id=thread.id, user_id=user_id)
        .order_by(ConversationMessage.id)
        .all()
    )
    rows = rows[-max(limit, 1) :]
    return {
        "id": thread.id,
        "title": thread.title,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "messages": [
            {"id": row.id, "role": row.role, "text": row.text, "task_id": row.task_id, "created_at": row.created_at}
            for row in rows
        ],
    }


def append_message(db, *, user_id: int, thread_id: int, role: str, text: str, task_id=None) -> dict:
    """Add one turn and touch the thread. Long assistant replies must be
    trimmed by the caller (their full text lives on the task result)."""
    thread = _owned_thread(db, user_id=user_id, thread_id=thread_id)
    if role not in ("user", "assistant"):
        raise ThreadError("role must be user or assistant")
    clean = (text or "").strip()
    if not clean:
        raise ThreadError("message must not be empty")
    if len(clean) > MAX_MESSAGE_LEN:
        raise ThreadError("message must be at most 2000 characters")
    now = utcnow()
    row = ConversationMessage(
        thread_id=thread.id, user_id=user_id, role=role, text=clean, task_id=task_id, created_at=now
    )
    thread.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "role": row.role, "text": row.text, "task_id": row.task_id, "created_at": row.created_at}


def context_messages(messages: list, *, turns: int = CONTEXT_TURNS) -> list:
    """Recent turns as model messages (oldest first). Pure: no database."""
    picked = [entry for entry in messages if entry.get("role") in ("user", "assistant")][-turns * 2 :]
    return [{"role": entry["role"], "content": entry["text"]} for entry in picked]


def context_text(messages: list, *, turns: int = CONTEXT_TURNS) -> str:
    """Recent turns as plain "role: text" lines for the planner."""
    lines = [f"{entry['role']}: {entry['content']}" for entry in context_messages(messages, turns=turns)]
    return "\n".join(lines)[-1500:]
