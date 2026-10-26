"""Explicit owner-approved Web profile, separate from capped benchmark ledgers.

Reuse the existing reservation, usage, HTTP and business implementations. Only
the interactive profile omits cumulative money/request limits; per-turn native
guards, request validation, unknown-receipt stops and the grant deadline remain.
"""

from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi.responses import JSONResponse

from .episode_budget import PRICE, EpisodeLedger
from .episode_transport import EpisodeCallback, EpisodeSyncTransport
from .langgraph_poc import deepseek_model
from .response_evidence import ResponseEvidence
from .runtime import BudgetExceeded, BudgetJournal
from .web_service import create_app

EPISODE = "owner-interactive-web"


class InteractiveLedger(EpisodeLedger):
    def __init__(self, path, *, expires_at, clock=time.time):
        deadline = datetime.fromisoformat(expires_at).timestamp()
        if clock() >= deadline:
            raise BudgetExceeded("Interactive authorization expired")
        self.journal, self.clock = BudgetJournal(path), clock
        self.cap_cny, self.max_requests = Decimal("Infinity"), math.inf
        policy = dict(
            type="policy",
            purpose=EPISODE,
            cap_cny=None,
            max_requests=None,
            expires_at=expires_at,
            price=PRICE,
            reservation_refundable=False,
            input_bound="2 * actual serialized HTTP UTF-8 bytes + 4096",
        )
        with self.journal.transaction() as (rows, append):
            if not rows:
                append(policy)
                append(
                    dict(
                        type="episode",
                        episode=EPISODE,
                        started=clock(),
                        deadline=deadline,
                        limits=dict(
                            requests=None,
                            tools=None,
                            steps=None,
                            model_timeout_s=60,
                            episode_timeout_s=deadline - clock(),
                        ),
                    )
                )
            elif rows[0] != policy:
                raise ValueError("Interactive grant differs or this is a capped experiment ledger")

    def _state(self, rows, episode):
        state = super()._state(rows, episode)
        return state | {
            "limits": {k: math.inf if v is None else v for k, v in state["limits"].items()}
        }


def load_grant(path, root):
    grant = json.loads(path.read_text())
    if (
        grant.get("authorized") is not True
        or grant.get("purpose") != EPISODE
        or not grant.get("user_instruction_reference")
        or grant.get("cap_cny") is not None
        or grant.get("max_requests") is not None
        or grant.get("state_dir") != str(root.resolve())
    ):
        raise ValueError("An explicit local interactive grant is required")
    # Instantiating the profile validates deadline and persisted policy, without HTTP.
    return grant


@contextmanager
def interactive_model(root, settings, ledger):
    # Imported from the existing CLI to preserve its price and private capture protocol.
    from scripts.local_return_poc import RequestCapture, verify_price

    verify_price(root, settings)
    ledger.remaining(EPISODE)
    ledger.evidence = ResponseEvidence(root / ".private-evidence", secrets=(settings.llm_api_key,))
    settings = settings.model_copy(
        update={
            "llm_temperature": 0,
            "llm_max_output_tokens": 1024,
            "llm_thinking": False,
            "llm_timeout_s": 60,
        }
    )
    with httpx.Client(
        transport=EpisodeSyncTransport(
            ledger,
            EPISODE,
            "agent",
            lambda: RequestCapture(ledger, ledger.evidence),
            streaming=True,
        )
    ) as client:
        yield deepseek_model(
            settings, http_client=client, callbacks=[EpisodeCallback(ledger, EPISODE)]
        ).model_copy(update={"streaming": True})


def interactive_app(root, factory, *, origin, secret_values=()):
    app = create_app(
        root,
        factory,
        origin=origin,
        secret_values=secret_values,
        runtime_mode="deepseek",
        answer_streaming=True,
    )

    @app.middleware("http")
    async def describe_owner_profile(request, call_next):
        response = await call_next(request)
        # Presentation metadata only. Business/auth/SSE routes stay with the base app.
        if request.url.path != "/api/bootstrap" or response.status_code != 200:
            return response
        data = json.loads(b"".join([part async for part in response.body_iterator]))
        data["runtime"].update(
            label="DeepSeek · 实时自由对话",
            notice="已接入真实 DeepSeek，正文与工具进度通过 SSE 实时更新。"
            "本次本机测试不设累计调用/金额上限，费用持续记账；单轮保护仍保留。"
            "商品与订单是合成示例，正式申请须单独点击确认。",
            cumulative_request_limit=None,
            cumulative_cost_limit=None,
        )
        result = JSONResponse(data, status_code=response.status_code)
        for key, value in response.headers.items():
            if key.lower() not in {"content-length", "content-type"}:
                result.headers[key] = value
        return result

    return app
