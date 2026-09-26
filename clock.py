"""
clock.py - The single source of truth for time (design doc, Section 1.4).

Every timestamp in the system comes from now() below. Keeping this in one
place means:
  - every time is Kenyan time (EAT, UTC+03:00) with its timezone attached,
  - the value kept in memory and the value saved in the database are
    exactly identical (see the note on microseconds below),
  - tests can later replace this one function to "fast-forward" time.
"""

from datetime import datetime

from config import TIMEZONE


def now() -> datetime:
    """
    Current date and time in Kenya, rounded down to whole seconds.

    Why drop microseconds? The database stores times as text with whole
    seconds ("2026-09-24T14:05:07+03:00"). If memory kept 14:05:07.482913
    but the database kept 14:05:07, a car's duration would change slightly
    after a server restart. Dropping them here keeps memory and database
    identical.
    """
    return datetime.now(TIMEZONE).replace(microsecond=0)


def to_text(moment: datetime | None) -> str | None:
    """Convert a datetime to ISO 8601 text for the database (None stays None)."""
    if moment is None:
        return None
    return moment.isoformat()


def from_text(text: str | None) -> datetime | None:
    """Convert ISO 8601 text from the database back to a datetime (None stays None)."""
    if text is None:
        return None
    return datetime.fromisoformat(text)