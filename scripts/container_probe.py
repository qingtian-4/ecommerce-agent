"""Run inside the built image: real-provider query, persisted workflow, bounds and recovery."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from threading import Barrier

from PIL import Image

from acs.chat_service import ChatService
from acs.config import get_settings
from acs.readiness import check_ready
from acs.returns import ReturnService
from acs.scenarios import data_fingerprint
from acs.sessions import ConversationBusy, SessionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "read", "index", "migration"))
    args = parser.parse_args()
    settings = get_settings()
    marker = settings.db_path.parent / "acceptance-marker.json"
    if args.mode == "write" and marker.exists():
        raise FileExistsError("验收卷已有标记，不重复创建申请；请用read复查或另建独立卷")
    store = SessionStore(settings.db_path)
    returns = ReturnService(settings, store)
    if args.mode == "index":
        from acs.rag import KnowledgeBase

        retrieval = settings.model_copy(update={"knowledge_mode": "retrieval"})
        kb = KnowledgeBase(retrieval)
        kb.build()
        checks = {"built_and_ready": check_ready(retrieval)["ready"]}
        try:
            check_ready(retrieval.model_copy(update={"embedding_model": "stale-model"}))
            checks["stale_index_rejected"] = False
        except RuntimeError:
            checks["stale_index_rejected"] = True
        print(json.dumps({"checks": checks, "usage_pricing": "embedding cost incomplete"}))
        raise SystemExit(not all(checks.values()))
    if args.mode == "migration":
        path = settings.db_path.parent / "legacy-fixture.db"
        with (
            closing(sqlite3.connect(settings.db_path)) as src,
            closing(sqlite3.connect(path)) as dst,
        ):
            src.backup(dst)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("DROP TABLE order_item_skus")
            conn.execute("DROP TABLE product_skus")
            conn.execute("DELETE FROM acs_migrations WHERE version=5")
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT IN ('acs_migrations','sqlite_sequence')"
                )
            ]
            before = {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in tables}
        SessionStore(path)
        with closing(sqlite3.connect(path)) as conn:
            checks = {
                "legacy_rows_preserved": before
                == {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in tables},
                "upgraded_to_v5": conn.execute(
                    "SELECT MAX(version) FROM acs_migrations"
                ).fetchone()[0]
                == 5,
            }
        print(json.dumps({"checks": checks, "preserved_tables": len(tables)}))
        raise SystemExit(not all(checks.values()))
    if args.mode == "read":
        expected = json.loads(marker.read_text())
        saved = store.load(expected["session_id"], "USER001")
        task = store.get_return_task(expected["session_id"], "USER001")["task"]
        request = next(
            r for r in returns.list_requests("USER001") if r["request_id"] == expected["request_id"]
        )
        media = returns.review_evidence(request["request_id"], reviewer=settings.admin_username)
        checks = {
            "history_preserved": len(saved["history"]) == expected["history_count"],
            "task_preserved": task["request_id"] == expected["request_id"],
            "request_and_review_preserved": request["status"] == "approved",
            "original_evidence_preserved": hashlib.sha256(media["data"]).hexdigest()
            == expected["evidence_sha256"],
            "business_preserved": data_fingerprint(settings.db_path) == expected["business"],
        }
        print(json.dumps({"checks": checks, "ready": check_ready(settings)}))
        raise SystemExit(not all(checks.values()))
    before = data_fingerprint(settings.db_path)
    chat = ChatService(settings, store)
    update = None
    for event in chat.run_turn("查询O10024的状态、商品和实付金额，先不办理操作。", "USER001"):
        update = event
    if update is None or update.result.stop_reason != "answered":
        raise RuntimeError("Container real-provider turn did not complete")
    sid = update.session_id
    operation = "[容器验收可信操作] 质量草稿、上传和明确确认"
    tid = store.begin_turn(sid, "USER001", operation)
    from acs.database import BusinessDB

    order = BusinessDB(settings).get_order("O10024", "USER001")
    item = order["items"][0]
    store.update_return_task(
        sid,
        "USER001",
        tid,
        {
            "order_id": "O10024",
            "item_id": item["id"],
            "quantity": 1,
            "reason": "quality",
            "condition": "opened",
            "quality_non_user_damage": True,
            "problem_description": "合成容器验收：正常充电后无法开机",
        },
    )
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "#446688").save(image, "PNG")
    evidence = returns.attach_evidence(sid, "USER001", tid, image.getvalue())
    preview = returns.preview(sid, "USER001", tid)
    if not preview["ready"]:
        raise RuntimeError(preview["reasons"])
    request = returns.confirm_and_submit(sid, "USER001", preview["preview_id"], tid)
    assert returns.confirm_and_submit(sid, "USER001", preview["preview_id"], tid) == request
    text = "容器验收：明确确认创建待审核申请，尚未退款。"
    store.finish_turn(
        sid,
        "USER001",
        tid,
        [{"role": "user", "content": operation}, {"role": "assistant", "content": text}],
        {"answer": text, "stop_reason": "answered"},
    )
    assert returns.authenticate_reviewer(
        settings.admin_username, settings.admin_password.get_secret_value()
    )
    returns.review(
        request["request_id"],
        "approved",
        "合成容器验收通过；尚未退款",
        reviewer=settings.admin_username,
    )
    marker.write_text(
        json.dumps(
            {
                "session_id": sid,
                "request_id": request["request_id"],
                "history_count": len(store.load(sid, "USER001")["history"]),
                "evidence_sha256": evidence["sha256"],
                "business": before,
            }
        )
    )

    barrier = Barrier(3)

    def one(uid):
        barrier.wait(timeout=10)
        phases, final = [], None
        for final in chat.run_turn("你好，请简短回应一句。", uid):
            phases.append(final.phase)
        return {"queued": "queued" in phases, "stop": final.result.stop_reason}

    with ThreadPoolExecutor(3) as pool:
        concurrency = list(pool.map(one, ["USER001", "USER002", "USER003"]))
    conflict_sid = store.create("USER001")
    stream = chat.run_turn("保留并发槽位", "USER001", conflict_sid)
    next(stream)
    same_session_denied = False
    try:
        next(chat.run_turn("第二次同时请求", "USER001", conflict_sid))
    except ConversationBusy:
        same_session_denied = True
    finally:
        stream.close()
    failure = ChatService(
        settings.model_copy(
            update={
                "llm_base_url": "http://127.0.0.1:9/v1",
                "llm_timeout_s": 2,
                "turn_timeout_s": 3,
            }
        )
    )
    fault = list(failure.run_turn("查询服务不可用时的失败", "USER001"))[-1]
    checks = {
        "real_provider_answered": True,
        "business_immutable": before == data_fingerprint(settings.db_path),
        "queued_third_session": any(r["queued"] for r in concurrency),
        "concurrent_turns_completed": all(r["stop"] == "answered" for r in concurrency),
        "same_session_denied": same_session_denied,
        "provider_failure_visible": fault.result.stop_reason == "llm_error",
        "source_data_found": settings.docs_dir.is_dir(),
    }
    print(
        json.dumps(
            {
                "checks": checks,
                "provider_result": asdict(update.result),
                "concurrency": concurrency,
                "fault_stop": fault.result.stop_reason,
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(not all(checks.values()))


if __name__ == "__main__":
    main()
