"""Interpret a platform's explicit Retry-After without retaining its headers."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_seconds(value):
    if value.isdigit() and len(value) <= 10:
        return int(value)
    try:
        return max(
            0,
            int(
                (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )
    except (ValueError, TypeError, OverflowError):
        return 0
