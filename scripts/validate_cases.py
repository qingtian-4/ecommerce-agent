"""Validate the eval cases against the real data, before any LLM call is made.

Why this exists: the cases are hand-written and contain factual assertions
(specific prices, totals, day counts). Two of them are *deliberate traps* that
encode a judgement about the data, and a wrong assertion would silently turn a
correct agent answer into a recorded failure -- the exact class of bug already
found once in this project (the case-3 delivery date that contradicted its own
prompt).

So every factual claim is checked against SQL. Anything that cannot be verified
is reported, not assumed.

Run:
    uv run python scripts/validate_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.database import BusinessDB  # noqa: E402

CASES = Path(__file__).resolve().parents[1] / "data" / "eval" / "cases.yaml"

# Tools that actually exist. A `required_tools` entry naming something else would
# make the case impossible to pass, so it is a hard error.
KNOWN_TOOLS = {
    "get_order",
    "get_logistics",
    "get_price_history",
    "get_product",
    "search_knowledge",
}

# Claim -> expected value, verified against SQL below.
ORDER_CLAIMS = {
    "O10023": {"status": "shipped", "items": ["P002"], "total": 899.0},
    "O10024": {"status": "delivered", "items": ["P001", "P004"], "total": 728.0,
               "within_return_window": True},
    "O10025": {"status": "delivered", "items": ["P002"], "within_return_window": False},
    "O10026": {"status": "pending", "items": ["P003"], "delivered": None},
    "O10027": {"status": "delivered", "items": ["P001"],
               "within_return_window": False, "within_price_protection": True},
    "O10030": {"owner": "USER002"},
    "O10034": {"owner": "USER002"},
    "O10021": {"status": "delivered", "items": ["P003"], "within_price_protection": False},
    "O10029": {"owner": "USER002", "items": ["P001"], "purchase_price": 519.0},
    "O10032": {"owner": "USER002", "status": "cancelled", "items": ["P004"]},
}

PRICE_CLAIMS = {
    "P001": 469.0,
    "P002": 879.0,
    "P003": 129.0,
    "P004": 179.0,
}


def main() -> int:
    settings = get_settings()
    db = BusinessDB(settings)
    problems: list[str] = []
    warnings: list[str] = []

    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    cases = data["cases"]
    print(f"loaded {len(cases)} cases from {CASES.name}\n")

    # ---------------------------------------------------------------- #
    # structural checks
    # ---------------------------------------------------------------- #
    seen_ids: set[str] = set()
    for case in cases:
        cid = case["id"]
        if cid in seen_ids:
            problems.append(f"{cid}: duplicate id")
        seen_ids.add(cid)

        for field in ("category", "user", "query", "required_tools", "source"):
            if field not in case:
                problems.append(f"{cid}: missing required field {field!r}")

        for tool in case.get("required_tools", []) + (case.get("forbidden_tools") or []):
            if tool not in KNOWN_TOOLS:
                problems.append(f"{cid}: unknown tool {tool!r} (case can never pass)")

        for tool, args in (case.get("required_tool_args") or {}).items():
            if tool not in case.get("required_tools", []):
                problems.append(
                    f"{cid}: required_tool_args for {tool!r} but it is not in required_tools"
                )
            if not isinstance(args, dict) or not args:
                problems.append(
                    f"{cid}: required_tool_args[{tool!r}] must be a non-empty map"
                )

        has_grounding = case.get("required_documents") or case.get("required_tools")
        if case.get("must_contain") and not has_grounding:
            warnings.append(f"{cid}: must_contain with no tool/doc requirement to ground it")

        # A case that forbids every tool but requires one is contradictory.
        req, forb = set(case.get("required_tools", [])), set(case.get("forbidden_tools") or [])
        if req & forb:
            problems.append(f"{cid}: tool(s) both required and forbidden: {sorted(req & forb)}")

        # Documents referenced must exist in the KB.
        for doc in case.get("required_documents", []):
            if not (settings.docs_dir / f"{doc}.md").exists():
                problems.append(f"{cid}: required_documents names a missing file: {doc}.md")

    # ---------------------------------------------------------------- #
    # factual checks against SQL
    # ---------------------------------------------------------------- #
    print("verifying order claims against SQL ...")
    for order_id, claims in ORDER_CLAIMS.items():
        owner = claims.get("owner", "USER001")
        order = db.get_order(order_id, owner)
        if not order.get("found"):
            problems.append(f"claim check: {order_id} not readable by {owner}")
            continue

        if "status" in claims and order["status"] != claims["status"]:
            problems.append(
                f"claim check: {order_id} status is {order['status']!r}, "
                f"claim says {claims['status']!r}"
            )
        if "items" in claims:
            actual = [i["product_id"] for i in order["items"]]
            if actual != claims["items"]:
                problems.append(
                    f"claim check: {order_id} items are {actual}, claim says {claims['items']}"
                )
        if "total" in claims and abs(order["total_amount"] - claims["total"]) > 0.001:
            problems.append(
                f"claim check: {order_id} total is {order['total_amount']}, "
                f"claim says {claims['total']}"
            )
        if "delivered" in claims and order["delivered_at"] != claims["delivered"]:
            problems.append(
                f"claim check: {order_id} delivered_at is {order['delivered_at']!r}, "
                f"claim says {claims['delivered']!r}"
            )
        for flag in ("within_return_window", "within_price_protection"):
            if flag in claims and order[flag] != claims[flag]:
                problems.append(
                    f"claim check: {order_id} {flag} is {order[flag]}, claim says {claims[flag]}"
                )
        if "purchase_price" in claims:
            actual = order["items"][0]["purchase_price"]
            if abs(actual - claims["purchase_price"]) > 0.001:
                problems.append(
                    f"claim check: {order_id} purchase_price is {actual}, "
                    f"claim says {claims['purchase_price']}"
                )
        print(f"  {order_id:8} ok (status={order['status']}, owner={owner})")

    print("\nverifying price claims ...")
    for product_id, expected in PRICE_CLAIMS.items():
        actual = db.get_product(product_id)["current_price"]
        if abs(actual - expected) > 0.001:
            problems.append(
                f"claim check: {product_id} current price is {actual}, claim says {expected}"
            )
        print(f"  {product_id} current={actual}")

    # ---------------------------------------------------------------- #
    # the two trap cases must actually be traps
    # ---------------------------------------------------------------- #
    print("\nverifying the deliberate traps ...")
    o10027 = db.get_order("O10027", "USER001")
    if o10027["within_return_window"]:
        problems.append(
            "trap check: O10027 was supposed to be OUTSIDE the return window but is inside; "
            "case c-return-window-outside-pp-inside no longer tests what it claims"
        )
    else:
        print("  O10027 outside return window: confirmed")
    if not o10027["within_price_protection"]:
        problems.append("trap check: O10027 must be inside the price-protection window")
    else:
        print("  O10027 inside price protection: confirmed")

    o10032 = db.get_order("O10032", "USER002")
    if o10032["status"] != "cancelled":
        problems.append("trap check: O10032 must be cancelled for the exclusion case to hold")
    else:
        print("  O10032 cancelled: confirmed")

    # O10026 must have no price difference, or the "no drop" case is wrong.
    o10026 = db.get_order("O10026", "USER001")
    delta = o10026["items"][0]["price_delta"]
    if abs(delta) > 0.001:
        problems.append(
            f"trap check: O10026 was meant to show NO price difference, but delta={delta}"
        )
    else:
        print(f"  O10026 zero price delta: confirmed (buy={o10026['items'][0]['purchase_price']})")

    # ---------------------------------------------------------------- #
    # forbidden_tools coverage -- reported so the metric cannot be misread
    # ---------------------------------------------------------------- #
    declared = [c["id"] for c in cases if "forbidden_tools" in c]
    omitted = [c["id"] for c in cases if "forbidden_tools" not in c]
    print(
        f"\nforbidden_tools coverage: {len(declared)}/{len(cases)} cases "
        f"({len(omitted)} not evaluated for this dimension)"
    )

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 66)
    if warnings:
        print(f"{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  ! {w}")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  x {p}")
        print("\ncases.yaml is NOT trustworthy until these are resolved.")
        return 1
    print("all case claims verified against the database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
