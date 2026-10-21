"""The audit log (FR-ADM-5): who changed what, with before and after values."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import AuditLog


def record(
    db: AsyncSession,
    *,
    actor_member_id: int | None,
    action: str,
    before: Any = None,
    after: Any = None,
    **details: Any,
) -> AuditLog:
    """Add an audit row to the session; it is saved with the caller's commit."""
    payload = {"before": before, "after": after, **details}
    entry = AuditLog(actor_member_id=actor_member_id, action=action, payload=_jsonable(payload))
    db.add(entry)
    return entry


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = [_jsonable(item) for item in value]
        return sorted(items) if isinstance(value, set | frozenset) else items
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
