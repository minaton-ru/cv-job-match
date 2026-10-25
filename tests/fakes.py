from datetime import datetime


class FakeClock:
    """Clock that always returns the time it was given."""

    def __init__(self, now: datetime) -> None:
        """Create the clock.

        Args:
            now: The time every call returns.
        """
        self.now = now

    def now_utc(self) -> datetime:
        """Return the fixed time.

        Returns:
            The time the clock was created with.
        """
        return self.now


class FakeDependency:
    """Stand-in for a dependency whose interface a later ticket defines."""

    def __init__(self, name: str) -> None:
        """Create the stand-in.

        Args:
            name: Which dependency it stands in for.
        """
        self.name = name
