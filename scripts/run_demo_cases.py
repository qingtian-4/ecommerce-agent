"""Run the four MVP demo cases through the agent in the terminal.

This exists so the agent can be debugged without the UI in the way. It is also
the fastest way to check a prompt or tool change.

    uv run python scripts/run_demo_cases.py            # all four
    uv run python scripts/run_demo_cases.py --case 3   # just case 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402

CASES = [
    {
        "id": 1,
        "name": "纯 RAG（政策问答）",
        "user": "USER001",
        "message": "无线耳机退货期限多久？",
        "expect": "应调用 search_knowledge 并命中退换货政策；不应调用 order/logistics 工具",
    },
    {
        "id": 2,
        "name": "纯 Tool（订单 + 物流）",
        "user": "USER001",
        "message": "订单 O10023 现在到哪了？",
        "expect": "应调用 get_order 与 get_logistics；通常不需要 search_knowledge",
    },
    {
        "id": 3,
        "name": "RAG + Tool 组合（最重要）",
        "user": "USER001",
        "message": "订单 O10024 是我昨天收到的耳机，现在还能退吗？",
        "expect": (
            "应调用 get_order（必要时 get_product），再调用 search_knowledge，"
            "结合订单事实与退货政策回答"
        ),
    },
    {
        "id": 4,
        "name": "越权 / 无法处理",
        "user": "USER001",
        "message": "帮我查一下我朋友的订单 O10030。",
        "expect": "get_order 应返回 forbidden，且回答不得泄露 O10030 的任何信息",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=int, help="run only this case id")
    args = parser.parse_args()

    settings = get_settings()
    cases = [c for c in CASES if args.case in (None, c["id"])]

    for case in cases:
        print("=" * 78)
        print(f"CASE {case['id']}: {case['name']}")
        print(f"user    : {case['user']}")
        print(f"message : {case['message']}")
        print(f"expect  : {case['expect']}")
        print("-" * 78)

        agent = CustomerServiceAgent(settings=settings, current_user_id=case["user"])
        result = agent.chat(case["message"])

        print(f"ANSWER  : {result.answer}")
        print(f"summary : {result.decision_summary()}")
        print(f"steps={result.steps} tool_calls={result.tool_calls} stop={result.stop_reason}")

        calls = [e for e in result.trace if e.type == "tool_call"]
        print("tools called:")
        for event in calls:
            print(f"  - {event.label}({event.detail.get('arguments')})")

        if result.retrieved_documents:
            print("retrieved documents:")
            for doc in result.retrieved_documents:
                print(
                    f"  - [{doc['score']:.4f}] {doc['title']} "
                    f"({doc['chunk_id']}) {doc['content'][:40].replace(chr(10), ' ')}..."
                )
        else:
            print("retrieved documents: (none)")

        if result.errors:
            print(f"errors  : {result.errors}")

        totals = result.totals
        print(
            f"cost    : tokens={totals.get('total_tokens')} "
            f"latency={totals.get('latency_ms')}ms "
            f"cost={totals.get('cost_cny')} CNY"
        )
        print()
        print("trace json:")
        print(json.dumps([e.as_dict() for e in result.trace], ensure_ascii=False, indent=2)[:1200])
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
