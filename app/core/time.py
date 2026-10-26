from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return naive UTC time for MySQL DATETIME columns."""
    return datetime.now(UTC).replace(tzinfo=None)
