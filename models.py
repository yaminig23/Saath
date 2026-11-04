"""Database tables for the Saath AI brain (Milestones 1 and 5 through 7).

Tables added here: users, sessions (login tokens), tasks, task_events
(audit log), memories (five layers), consents (per-category permission),
approvals (gated actions), failures (failure reports), notifications
(worker findings), login_attempts (brute-force guard), conversation_threads
and conversation_messages (P6 operator threads), demo_bookings
(labeled demo provider), video_jobs (AI video lessons),
calendar_connections (per-user Google OAuth refresh tokens),
gmail_connections (same, for the send-only Gmail grant). Agent tables
arrive with the milestones that use them.
"""

import json

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class User(Base):
    """One Saath user account. Passwords are stored hashed, never plain."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class Session(Base):
    """One login token. Only the token's hash is stored, never the token."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class Task(Base):
    """One assignment tracked by the kernel."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    agent: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    dependencies: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    deadline: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "objective": self.objective,
            "department": self.department,
            "agent": self.agent,
            "priority": self.priority,
            "dependencies": json.loads(self.dependencies),
            "deadline": self.deadline,
            "status": self.status,
            "result": self.result,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskEvent(Base):
    """One audit-log row: something happened to a task. Never edited."""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "note": self.note,
            "created_at": self.created_at,
        }


class Memory(Base):
    """One remembered fact in one of the five layers (user, shared, agent,
    task, conversation). Sensitivity tier is derived from the category;
    consent gates the sensitive ones."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    agent: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    source_task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "layer": self.layer,
            "category": self.category,
            "tier": self.tier,
            "key": self.key,
            "value": self.value,
            "department": self.department,
            "agent": self.agent,
            "source_task_id": self.source_task_id,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Consent(Base):
    """The user's recorded permission per sensitive category."""

    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "category": self.category,
            "granted": bool(self.granted),
            "updated_at": self.updated_at,
        }


class Approval(Base):
    """One gated action: proposed, then approved/denied/expired exactly once."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    args: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_at: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tool": self.tool,
            "args": json.loads(self.args),
            "status": self.status,
            "note": self.note,
            "result": self.result,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "created_at": self.created_at,
        }


class Failure(Base):
    """One failure report: what failed, for which task, and the honest error."""

    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    agent: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tool: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "agent": self.agent,
            "tool": self.tool,
            "error": self.error,
            "context": json.loads(self.context),
            "created_at": self.created_at,
        }


class LoginAttempt(Base):
    """One failed login (successful logins clear them). Internal table."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    attempted_at: Mapped[str] = mapped_column(String(32), nullable=False)

    def to_dict(self) -> dict:
        """Plain-dictionary view (unused by the API; kept for consistency)."""
        return {"id": self.id, "username": self.username, "attempted_at": self.attempted_at}


class Notification(Base):
    """One worker finding (e.g. a due reminder), awaiting pickup."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    read_at: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    def to_dict(self) -> dict:
        """Plain-dictionary view used in API answers."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "kind": self.kind,
            "text": self.text,
            "created_at": self.created_at,
            "read_at": self.read_at,
        }


class ConversationThread(Base):
    """One multi-turn conversation. Turns live in conversation_messages."""

    __tablename__ = "conversation_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class ConversationMessage(Base):
    """One turn in a thread, optionally linked to the task it produced."""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class DemoBooking(Base):
    """A clearly-labeled demo booking. Reserves nothing, anywhere."""

    __tablename__ = "demo_bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    option: Mapped[str] = mapped_column(Text, nullable=False)
    booking_id: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cancelled_by_task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CalendarConnection(Base):
    """One user's Google Calendar link: OAuth refresh token plus the
    account email for display. Access tokens are minted per call and
    never stored. A rejected refresh marks the row broken (with the
    remedy) instead of deleting the user's data silently."""

    __tablename__ = "calendar_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    broken_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class GmailConnection(Base):
    """One user's Gmail link: OAuth refresh token for the send-only
    grant, plus the account email for display. Separate row from the
    calendar link because Google binds refresh tokens to their scopes."""

    __tablename__ = "gmail_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    broken_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class VideoJob(Base):
    """One AI video lesson job: storyboard, provider clips, assembled file."""

    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    storyboard: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    scenes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    output_path: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cost_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class TrustedContact(Base):
    """A real person the user named: Saath may message them, but only
    with the user's explicit approval each time. Nothing here is ever
    contacted silently."""

    __tablename__ = "trusted_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    relation: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
