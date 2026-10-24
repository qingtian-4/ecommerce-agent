"""Native middleware context must be fresh and transient, with intact tool history."""

import json
import socket

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import fixture_settings, init_fixture
from acs.sessions import SessionStore

from .test_langgraph_poc import ScriptedModel, call


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError("Context contracts never send network requests")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_facts_are_transient_and_calls_and_user_history_are_kept(tmp_path):
    root = tmp_path / "context"
    init_fixture(root)
    store = SessionStore(fixture_settings(root).db_path)
    sid = store.create("USER001")
    model = ScriptedModel(
        responses=[
            call("update_return_task", {"order_id": "O10024"}, "draft"),
            AIMessage(content="已保存草稿"),
            AIMessage(content="不取消，继续保留"),
        ]
    )
    with LocalReturnPoc(root, "USER001", model=model) as poc:
        list(poc.stream(sid, "我要退O10024，保存订单草稿"))
        list(poc.stream(sid, "不要取消，先问运费"))
        binding = poc._binding(sid)
        persisted = poc.graph.get_state(poc._config(binding)).values["messages"]
        facts = [m for m in persisted if isinstance(m, SystemMessage)]
        assert facts == [], "Authoritative facts must not accumulate in checkpoint history"
        assert [m.content for m in persisted if isinstance(m, HumanMessage)] == [
            "我要退O10024，保存订单草稿",
            "不要取消，先问运费",
        ]
        tool = next(m for m in persisted if isinstance(m, ToolMessage))
        assert tool.tool_call_id == "draft"
    assert len(model.seen) == 3
    for seen in model.seen:
        live = [
            m for m in seen if isinstance(m, SystemMessage) and "当前应用核验事实：" in m.content
        ]
        assert len(live) == 1
    latest = next(
        m
        for m in model.seen[-1]
        if isinstance(m, SystemMessage) and "当前应用核验事实：" in m.content
    )
    assert '"revision": 1' in latest.content
    assert '"order_id": "O10024"' in latest.content
    assert json.loads(tool.content)["result"]["task"]["revision"] == 1


def test_legacy_snapshot_ignored_without_deleting_history_and_restart_gets_fresh_db(tmp_path):
    root = tmp_path / "legacy"
    init_fixture(root)
    store = SessionStore(fixture_settings(root).db_path)
    sid = store.create("USER001")
    first = ScriptedModel(responses=[AIMessage(content="waiting for details")])
    with LocalReturnPoc(root, "USER001", model=first) as poc:
        list(poc.stream(sid, "O10024请先咨询"))
        binding = poc._binding(sid)
        poc.graph.update_state(
            poc._config(binding),
            {
                "messages": [
                    SystemMessage(content='当前应用核验事实：{"obsolete": "MUST_NOT_REACH_MODEL"}')
                ]
            },
        )
    # An existing valid completed history normally ends with AI; legacy snapshots
    # occurred BEFORE that answer, so move this server-owned test marker there.
    with LocalReturnPoc(root, "USER001", model=first) as poc:
        state = poc.graph.get_state(poc._config(binding))
        from langchain_core.messages import RemoveMessage

        marker = state.values["messages"][-1]
        poc.graph.update_state(
            poc._config(binding),
            {
                "messages": [
                    RemoveMessage(id=marker.id),
                    SystemMessage(content=marker.content),
                    AIMessage(content="legacy answer"),
                ]
            },
        )
    second = ScriptedModel(
        responses=[
            call("update_return_task", {"order_id": "O10024"}, "new"),
            AIMessage(content="新草稿"),
        ]
    )
    with LocalReturnPoc(root, "USER001", model=second) as poc:
        list(poc.stream(sid, "现在保存O10024的草稿"))
        state = poc.graph.get_state(poc._config(binding))
        assert any(
            isinstance(m, SystemMessage) and "MUST_NOT_REACH_MODEL" in m.text
            for m in state.values["messages"]
        )
    assert all("MUST_NOT_REACH_MODEL" not in str(seen) for seen in second.seen)
    assert '"revision": 1' in second.seen[-1][-1].text


def frozen_eight(runtime_type, root):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from eval_local_return_poc import STAGES

    from .test_local_return_poc import draft

    init_fixture(root)
    store = SessionStore(fixture_settings(root).db_path)
    sid = store.create("USER001")
    decisions = [
        [
            call("get_order", {"order_id": "O10024"}, "order"),
            call("search_knowledge", {"query": "七天无理由退货运费"}, "policy"),
            call("update_return_task", {"order_id": "O10024"}, "d1"),
            AIMessage(content="订单草稿及运费"),
        ],
        [call("update_return_task", draft(), "d2"), AIMessage(content="已补齐")],
        [
            call("get_product", {"product_id": "P003"}, "product"),
            AIMessage(content="2C1A接口，保留草稿"),
        ],
        [
            AIMessage(
                content="更正并预览",
                tool_calls=[
                    {"id": "d3", "name": "update_return_task", "args": dict(draft(), item_id=5)},
                    {"id": "p3", "name": "preview_return", "args": {}},
                ],
            ),
            AIMessage(content="已预览充电宝，未提交"),
        ],
        [AIMessage(content="政策咨询，不取消草稿")],
        [],
        [call("cancel_return_task", {}, "cancel"), AIMessage(content="已取消草稿")],
        [
            AIMessage(
                content="重建并预览",
                tool_calls=[
                    {"id": "d5", "name": "update_return_task", "args": draft()},
                    {"id": "p5", "name": "preview_return", "args": {}},
                ],
            ),
            AIMessage(content="最新耳机预览，未提交"),
        ],
    ]
    summaries = []
    inputs = []
    for text, responses in zip(STAGES, decisions, strict=True):
        model = ScriptedModel(responses=responses)
        with runtime_type(root, "USER001", model=model) as poc:
            done = list(poc.stream(sid, text))[-1]
            task = done.detail["business_state"]["task"].get("task")
            preview = done.detail["business_state"]["preview"]
            summaries.append(
                {
                    "answer": done.detail["answer"],
                    "task": {k: v for k, v in (task or {}).items() if k != "sources"},
                    "preview_ready": bool(preview and preview["ready"]),
                    "stop_reason": done.detail["stop_reason"],
                    "historical": done.detail.get("answer_is_historical", False),
                }
            )
            inputs.extend(model.seen)
            if text is not None:
                checkpoint = poc.graph.get_state(poc._config(poc._binding(sid)))
                assert checkpoint.next == ()
    with store.transaction() as conn:
        receipts = [
            tuple(r)
            for r in conn.execute(
                "SELECT name,arguments,error,revision_before,revision_after "
                "FROM local_receipts ORDER BY rowid"
            )
        ]
        formal = {
            t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in ("return_requests", "return_confirmations", "return_reviews", "return_audit")
        }
    return summaries, receipts, formal, inputs


def test_frozen_eight_stages_semantics_and_complete_native_pairs(tmp_path):
    stages, receipts, formal, inputs = frozen_eight(LocalReturnPoc, tmp_path / "frozen-eight")
    assert [s["task"]["revision"] for s in stages] == [1, 2, 2, 3, 3, 3, 4, 5]
    assert stages[3]["task"]["item_id"] == 5
    assert stages[6]["task"]["status"] == "cancelled"
    assert stages[7]["task"]["item_id"] == 4 and stages[7]["preview_ready"]
    assert all(v == 0 for v in formal.values())
    assert len(receipts) == 10
    last = inputs[-1]
    pending = set()
    for message in last:
        if isinstance(message, AIMessage):
            pending.update(c["id"] for c in message.tool_calls)
        elif isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
    assert pending == set()
    assert len([m for m in last if isinstance(m, HumanMessage)]) == 7
    assert (
        len([m for m in last if isinstance(m, SystemMessage) and "当前应用核验事实：" in m.text])
        == 1
    )


@pytest.mark.parametrize(
    "status,usage,estimated,expected",
    [
        ("completed", {"prompt_tokens": 10, "completion_tokens": 1}, "0.000028", (1, 0, 0)),
        ("completed", None, None, (0, 1, 0)),
        ("http_500", None, None, (0, 1, 1)),
        (None, None, None, (0, 0, 0)),
    ],
)
def test_context_audit_does_not_call_unknown_usage_settled(
    tmp_path, status, usage, estimated, expected
):
    import hashlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from audit_local_context import audit, encoded

    private = tmp_path / ".private-evidence/runtime/.private-evidence"
    private.mkdir(parents=True)
    body = {"messages": [{"role": "user", "content": "synthetic"}], "tools": []}
    request = {"kind": "authorized_private_model_request", "request_id": "local", "body": body}
    (private / "request.json").write_text(json.dumps(request))
    rows = [
        {
            "type": "reserved",
            "request_id": "local",
            "body_sha256": hashlib.sha256(encoded(body)).hexdigest(),
            "output_token_bound": 1024,
            "reserved_cny": "0.02",
        }
    ]
    if status:
        rows.append(
            {
                "type": "request_end",
                "request_id": "local",
                "status": status,
                "usage": usage,
                "estimated_cny": estimated,
            }
        )
    (private.parent / "fees.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = audit(tmp_path)
    assert (
        tuple(
            report["settlement"][k]
            for k in ["completed_with_usage", "unknown_usage", "failed_requests"]
        )
        == expected
    )
    assert report["settlement"]["unsettled"] == (0 if status else 1)
    assert report["totals"]["original_reserved_cny"] == "0.02"
