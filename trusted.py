"""Trusted contacts: real people the user named for escalation.

Adding someone here authorises nothing by itself - every message to a
trusted contact still goes through the normal approval gate (the
consequential gmail_send tool), one explicit yes per send. Listing and
deleting are owner-scoped: nobody sees another user's people.

Covered by tests/test_trusted.py.
"""

import re
from datetime import datetime, timezone

import kernel
from models import TrustedContact

MAX_NAME_LEN = 80
MAX_RELATION_LEN = 80

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TrustedContactError(kernel.KernelError):
    """A trusted-contact request broke a contact rule."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_contact(db, *, user_id: int, name: str, email: str, relation: str = "") -> dict:
    """Store one trusted contact for this user only."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise TrustedContactError("name must not be empty")
    if len(clean_name) > MAX_NAME_LEN:
        raise TrustedContactError("name must be at most 80 characters")
    clean_email = (email or "").strip()
    if not _EMAIL_RE.match(clean_email):
        raise TrustedContactError("email must look like name@example.com")
    clean_relation = (relation or "").strip()
    if len(clean_relation) > MAX_RELATION_LEN:
        raise TrustedContactError("relation must be at most 80 characters")
    row = TrustedContact(user_id=user_id, name=clean_name, email=clean_email, relation=clean_relation, created_at=_now())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "email": row.email, "relation": row.relation}


def list_contacts(db, *, user_id: int) -> list:
    """Every trusted contact this user stored, oldest first."""
    rows = db.query(TrustedContact).filter_by(user_id=user_id).order_by(TrustedContact.id).all()
    return [{"id": row.id, "name": row.name, "email": row.email, "relation": row.relation} for row in rows]


def delete_contact(db, *, user_id: int, contact_id: int) -> dict:
    """Remove one trusted contact. Other users' rows are untouchable."""
    row = db.query(TrustedContact).filter_by(id=contact_id, user_id=user_id).first()
    if row is None:
        raise kernel.TaskNotFoundError("trusted contact not found")
    db.delete(row)
    db.commit()
    return {"id": contact_id, "deleted": True}
