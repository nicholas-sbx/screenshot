"""``-v`` / ``--verbose``: how long each step of a capture takes, on stderr
(``flatshot --tray -v`` for every capture the tray app takes)."""

import sys
import time

enabled = False


def log(message: str) -> None:
    if enabled:
        print(f"flatshot: {message}", file=sys.stderr, flush=True)


class Clock:
    """Times the steps of one thing (a capture, a save): each step says how
    long it took since the one before, and since the start."""

    def __init__(self, what: str):
        self.what = what
        self.start = self.last = time.monotonic()

    def step(self, name: str, detail: str = "") -> None:
        now = time.monotonic()
        if enabled:
            log(f"{self.what}: {name} {(now - self.last) * 1000:.0f} ms (at {(now - self.start) * 1000:.0f} ms)"
                + (f", {detail}" if detail else ""))
        self.last = now

    def since_start(self) -> float:
        """Milliseconds since the start."""
        return (time.monotonic() - self.start) * 1000
