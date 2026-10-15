"""One turn's wall clock and request limits, shared by nested API clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RunBudget:
    timeout_s: float
    max_requests: int
    started: float = field(default_factory=monotonic)
    requests: int = 0

    def remaining(self) -> float:
        remaining = self.timeout_s - (monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("整轮处理时间已达上限")
        return remaining

    def request(self) -> float:
        remaining = self.remaining()
        if self.requests >= self.max_requests:
            raise BudgetExceeded("整轮 API 请求数已达上限")
        self.requests += 1
        return remaining

    def as_dict(self) -> dict:
        return {"timeout_s": self.timeout_s, "max_api_requests": self.max_requests,
                "api_requests": self.requests}
