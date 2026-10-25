"""Access to the current time."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time."""

    def now_utc(self) -> datetime:
        """Return the current time.

        Returns:
            The current time as an aware datetime in UTC.
        """
        ...


class SystemClock:
    """Clock that reads the system time."""

    def now_utc(self) -> datetime:
        """Return the current system time.

        Returns:
            The current time as an aware datetime in UTC.
        """
        return datetime.now(UTC)
