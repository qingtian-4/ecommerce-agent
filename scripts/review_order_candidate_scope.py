"""Frozen request-scope and malformed-payload acceptance probes; no network/models."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import sys
from contextlib import closing, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(os.environ.get("ACS_REVIEW_RUNTIME_ROOT", Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.order_candidates import order_candidate_response  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

LABELS = {"pending": "待发货", "shipped": "已发货", "delivered": "已签收",
          "cancelled": "已取消"}
SAFE_ANSWER = "工具数据暂时不完整，请重新核对订单信息。"


def malformed(payload, name):
    result = copy.deepcopy(payload)
    order = result["orders"][0]
    item = order["items"][0]
    if name == "missing_order_id":
        del order["order_id"]
    elif name == "non_dict_order":
        result["orders"][0] = None
    elif name == "items_dict":
        order["items"] = {"unexpected": item}
    elif name == "items_string":
        order["items"] = "unexpected"
    elif name == "item_null":
        order["items"] = [None]
    elif name == "sku_list":
        item["sku"] = ["invalid"]
    elif name == "sku_string":
        item["sku"] = "invalid"
    elif name == "missing_items":
        del order["items"]
    elif name == "empty_items":
        order["items"] = []
    elif name == "quantity_bool":
        item["quantity"] = True
    elif name == "colour_list":
        item["sku"]["color"] = ["白色"]
    elif name == "item_id_text":
        item["id"] = "invalid"
    else:
        raise ValueError(name)
    return result


def audit(answer, case, truth):
    """Read displayed facts, independently of fact_render metadata or renderer helpers."""
    rows, columns = [], None
    for line in answer.splitlines():
        if not line.startswith("| "):
            continue
        cells = [v.strip() for v in line.strip().strip("|").split("|")]
        if "订单号" in cells:
            columns = cells
        elif columns and not all(re.fullmatch(r"[- :]+", c) for c in cells):
            rows.append(dict(zip(columns, cells, strict=True)))
    correct = 0
    keys = []
    for row in rows:
        matched = next((t for t in truth if all((
            row.get("订单号") == t["order_id"], row.get("明细编号") == str(t["item_id"]),
            row.get("商品") == t["product_name"], row.get("SKU") == t["sku_id"],
            row.get("颜色") == t["color"], row.get("数量") == str(t["quantity"]),
            row.get("状态") == LABELS[t["status"]], row.get("下单日期") == t["created_at"],
        ))), None)
        correct += matched is not None
        if matched:
            keys.append((matched["order_id"], matched["item_id"]))
    claims = len(rows)
    for pattern, expected in (
        (r"订单数：(\d+)笔", len({t["order_id"] for t in truth})),
        (r"本次查到\s*(\d+)\s*笔订单", len({t["order_id"] for t in truth})),
        (r"商品件数：(\d+)件", sum(t["quantity"] for t in truth)),
    ):
        for value in re.findall(pattern, answer):
            claims += 1
            correct += int(value) == expected
    for oid, sku, colour in re.findall(r"订单 (S\d+) 的 ([\w-]+) 是([^。]+)。", answer):
        claims += 1
        matched = next((t for t in truth if (t["order_id"], t["sku_id"], t["color"])
                        == (oid, sku, colour)), None)
        correct += matched is not None
        if matched:
            keys.append((matched["order_id"], matched["item_id"]))
    if case["id"] == "legacy-orders01" and not rows:
        assert "S20008、S20003" in answer and "（黑色）" in answer
        claims, correct = 2, 1  # Frozen original's two grouped SKU-colour claims only.
    expected = case["expected"]
    selected = {tuple(k) for k in expected["selected_keys"]}
    ids = set(re.findall(r"S\d{5}", answer))
    facts = correct / claims if claims else None
    complete = (set(keys) == selected and len(keys) == len(selected) and facts == 1)
    if expected["kind"] in {"order_count", "item_count"}:
        unit = "订单数" if expected["kind"] == "order_count" else "商品件数"
        complete = bool(re.search(unit + "：" + str(expected["count"]) + r"[笔件]", answer))
        complete = complete and not rows and facts == 1
    if expected["require_clarification"]:
        complete = complete and bool(re.search(r"请(?:确认|提供).*订单号.*明细", answer))
    if expected["max_chars"] is not None:
        complete = complete and len(answer) <= expected["max_chars"]
    return {"fact_claims": claims, "correct_claims": correct, "fact_precision": facts,
            "task_complete": complete, "displayed_rows": len(rows),
            "irrelevant_orders": sorted(ids - set(expected["scope_order_ids"])),
            "extra_item_rows": len(set(keys) - selected),
            "unwanted_rows": len(rows) if expected["kind"] in {"order_count", "item_count"}
            else 0, "answer_chars": len(answer)}


def business_state(store):
    with closing(store._connect()) as conn:
        tables = ("return_tasks", "return_evidence", "return_previews", "return_confirmations",
                  "return_requests", "return_audit", "return_reviews")
        rows = {t: [list(row) for row in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                for t in tables}
    return {"business": data_fingerprint(store.db_path), "returns": rows}


def run(cases_path, output, *, full_only=False):
    output.mkdir(parents=True, exist_ok=False)
    frozen = json.loads(cases_path.read_text())
    full_queries = {c["query"] for c in frozen["cases"]
                    if c["id"] in {"list-all", "return-all", "short-full-control"}}
    records = []

    def denied(*args, **kwargs):
        raise AssertionError("Network prohibited in P1 scope review")

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    # Calibration uses both known good answers and deliberately wrong/incomplete ones.
    first = frozen["cases"][0]
    good = first["model_answer"]
    calibration = {
        "canonical": audit(good, first, frozen["truth_rows"]),
        "empty": audit("", first, frozen["truth_rows"]),
        "wrong_colour": audit(good.replace("白色", "错误颜色", 1), first, frozen["truth_rows"]),
        "missing_row": audit("\n".join(good.splitlines()[:-1]), first, frozen["truth_rows"]),
        "correct_but_irrelevant": audit(good, frozen["cases"][1], frozen["truth_rows"]),
        "wrong_count": audit("订单数：999笔。", frozen["cases"][8], frozen["truth_rows"]),
    }
    assert calibration["canonical"]["task_complete"]
    assert all(not c["task_complete"] for k, c in calibration.items() if k != "canonical")
    assert calibration["wrong_colour"]["fact_precision"] < 1
    assert calibration["correct_but_irrelevant"]["fact_precision"] == 1
    save("calibration.json", calibration)
    with TemporaryDirectory(prefix="acs-p1-scope-") as temp, patch.object(
        socket.socket, "connect", denied,
    ), patch.object(socket.socket, "connect_ex", denied):
        source = Path(temp) / "source"
        build_scenario(source)
        for case in frozen["cases"] + [{"id": n, "query": first["query"],
                                        "model_answer": SAFE_ANSWER, "malformed": True}
                                       for n in frozen["malformed"]]:
            for enabled in (False, True):
                directory = Path(temp) / f"{case['id']}-{enabled}"
                shutil.copytree(source, directory)
                settings = scenario_settings(Settings(
                    _env_file=None, llm_api_key="", embedding_api_key="",
                    order_candidate_render_enabled=enabled, return_state_check_enabled=False,
                ), directory)
                store = SessionStore(settings.db_path)
                before = business_state(store)
                assert before["business"] == frozen["source_fingerprint"]
                registry = ToolRegistry(settings)
                if case.get("malformed"):
                    bad = malformed(frozen["truth_payload"], case["id"])
                    registry._tools["list_orders"].func = (
                        lambda bad=bad, **kwargs: copy.deepcopy(bad)
                    )
                llm = ScriptedLLM([
                    ScriptedResponse(wants_tools=True, tool_calls=[
                        ScriptedToolCall("orders", "list_orders", "{}"),
                    ]), ScriptedResponse(text=case["model_answer"]),
                    ScriptedResponse(text="好的，您可以稍后再选择。"),
                ])
                agent = CustomerServiceAgent(settings=settings, llm=llm, registry=registry)
                service = ChatService(settings, store)
                sid = store.create("USER001")
                record = {"case": case["id"], "enabled": enabled,
                          "malformed": bool(case.get("malformed")), "exception": None}
                try:
                    allowed = case["query"] in full_queries
                    policy = patch("acs.chat_service.order_candidate_response", side_effect=(
                        lambda *args, allowed=allowed, **kwargs:
                        order_candidate_response(*args, **kwargs) if allowed else None
                    )) if full_only else nullcontext()
                    with policy:
                        final = list(service.run_turn(case["query"], "USER001", sid,
                                                       agent=agent))[-1]
                    answer = final.result.answer
                    snapshot = store.load(sid, "USER001")
                    record.update(answer=answer, stop_reason=final.result.stop_reason,
                                  rendered=any(e.type == "fact_render" for e in final.result.trace),
                                  first_input_chars=final.result.totals["model_input_chars"],
                                  synchronized=(agent.messages[-1]["content"] == answer
                                                == snapshot["messages"][-1]["content"]
                                                == snapshot["last_result"]["answer"]
                                                == final.result.trace[-1].detail["text"]))
                    if not case.get("malformed"):
                        record["metrics"] = audit(answer, case, frozen["truth_rows"])
                        next_turn = list(service.run_turn("我先看看，稍后再选。", "USER001", sid,
                                                          agent=agent))[-1]
                        record["next_input_chars"] = next_turn.result.totals["model_input_chars"][0]
                except Exception as exc:  # Preserve failed runs rather than selecting retries.
                    last = store.load(sid, "USER001")["last_result"]
                    record.update(exception=type(exc).__name__, error=str(exc),
                                  stop_reason=last["stop_reason"])
                record.update(model_calls=len(llm.calls),
                              business_unchanged=before == business_state(store),
                              task_absent=store.get_return_task(sid, "USER001")["task"] is None)
                if case.get("malformed"):
                    record["safe_decline"] = (record["exception"] is None
                                              and record["stop_reason"] == "answered"
                                              and record["answer"] == SAFE_ANSWER
                                              and not record["rendered"]
                                              and record["business_unchanged"]
                                              and record["task_absent"])
                assert record["business_unchanged"] and record["task_absent"]
                records.append(record)
    (output / "raw.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                             for r in records))
    pairs = []
    for case in frozen["cases"]:
        off, on = [r for r in records if r["case"] == case["id"]]
        assert off["exception"] is None and on["exception"] is None
        assert off["first_input_chars"][0] == on["first_input_chars"][0]
        assert off["model_calls"] == on["model_calls"] == 3
        was_correct = off["metrics"]["fact_precision"] == 1 and off["metrics"]["task_complete"]
        pairs.append({"case": case["id"], "off": off["metrics"], "on": on["metrics"],
                      "overwritten_correct": was_correct and off["answer"] != on["answer"],
                      "harmful_override": was_correct and not on["metrics"]["task_complete"],
                      "first_input_delta": [v - u for u, v in zip(
                          off["first_input_chars"], on["first_input_chars"], strict=True)],
                      "next_context_delta": on["next_input_chars"] - off["next_input_chars"]})
    summary = {"baseline": frozen["baseline_commit"], "runtime_root": str(ROOT),
               "policy": "ideal-full-only" if full_only else "runtime",
               "source_sha256": hashlib.sha256(
                   (ROOT / "src/acs/order_candidates.py").read_bytes()).hexdigest(),
               "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
               "pairs": pairs, "malformed": [r for r in records if r["malformed"]],
               "paid_calls": 0, "real_tokens": None,
               "usage_note": "ScriptedLLM placeholder usage; characters are not token estimates",
               "business_unchanged": all(r["business_unchanged"] for r in records)}
    save("summary.json", summary)
    print(json.dumps({"pairs": len(pairs), "harmful_overrides": sum(
        p["harmful_override"] for p in pairs),
                      "safe_declines_on": sum(r.get("safe_decline", False) for r in records
                                               if r["malformed"] and r["enabled"]),
                      "records": len(records), "paid_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--full-only", action="store_true",
                        help="Experimental oracle whitelist of three frozen full-list requests")
    args = parser.parse_args()
    run(args.cases, args.out, full_only=args.full_only)
