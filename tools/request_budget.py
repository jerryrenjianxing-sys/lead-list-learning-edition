"""One request/deadline budget shared by every transport layer of a probe."""

import time


class RequestBudget:
    def __init__(self, deadline, maximum=3):
        self.deadline = deadline
        self.maximum = maximum
        self.requests = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Probe network budget exhausted")
        return remaining

    def take(self):
        if self.requests >= self.maximum:
            raise TimeoutError("Probe request budget exhausted")
        self.requests += 1
        return self.remaining()
