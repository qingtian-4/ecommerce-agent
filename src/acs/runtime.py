"""One turn's wall clock and request limits, shared by nested API clients."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic


class BudgetExceeded(RuntimeError):
    pass


class BudgetJournal:
    """Append-only, fsynced reservations. POSIX lock covers check AND append."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        import fcntl

        with self.path.open("a+", encoding="utf-8") as out:
            fcntl.flock(out, fcntl.LOCK_EX)
            out.seek(0)
            rows = [json.loads(line) for line in out]

            def append(row):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                os.fsync(out.fileno())
                rows.append(row)

            yield rows, append

    def rows(self):
        with self.transaction() as (rows, _):
            return rows


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
