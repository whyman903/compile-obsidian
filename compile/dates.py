from __future__ import annotations

from datetime import UTC, date, datetime


def format_frontmatter_datetime(value: date | datetime) -> str:
    """Format a timestamp for human-readable markdown frontmatter."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone()
        value = value.replace(microsecond=0)
        return value.strftime("%Y-%m-%d %H:%M")
    return value.isoformat()


def format_machine_datetime(value: date | datetime) -> str:
    """Format a timestamp for machine-readable JSON/state payloads."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC).replace(microsecond=0)
        return value.isoformat()
    return value.isoformat()


def now_frontmatter() -> str:
    return format_frontmatter_datetime(datetime.now().astimezone())


def now_machine() -> str:
    return format_machine_datetime(datetime.now(UTC))
