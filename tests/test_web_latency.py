"""Regressions from the owner's manual session; no model or external HTTP."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessageChunk

from acs.local_submission import init_formal_fixture
from acs.web_service import AnswerStreamCallback, WebEngine


def test_event_and_browser_reads_do_not_wait_for_a_reserved_writer(tmp_path):
    root = tmp_path / "fixture"
    init_formal_fixture(root)
    engine = WebEngine(root, lambda: nullcontext(None))
    engine.start()
    try:
        token = engine.bootstrap()
        engine.emit("sid", "job", "turn_started", status="running")
        with ThreadPoolExecutor(max_workers=2) as pool:
            with engine.tx():
                reads = [pool.submit(engine.events, "sid", 0), pool.submit(engine.browser, token)]
                try:
                    values = [future.result(timeout=0.5) for future in reads]
                    blocked = False
                except TimeoutError:
                    blocked = True
            assert not blocked, "Read-only SSE/auth competed for the writer lock"
            assert values[0][0]["type"] == "turn_started" and values[1]["csrf"]
    finally:
        engine.stop()


def test_small_tokens_are_coalesced_but_terminal_text_is_exact():
    emitted = []
    engine = SimpleNamespace(secret_values=(), emit=lambda *args, **kw: emitted.append((args, kw)))
    callback = AnswerStreamCallback(engine, "session", "job")
    run = uuid4()
    callback.on_chat_model_start({}, [], run_id=run)
    text = "订单状态来自数据库，请从候选中选择需要查询的订单。" * 40
    for i in range(0, len(text), 3):
        part = text[i:i+3]
        callback.on_llm_new_token(part, run_id=run,
                                 chunk=SimpleNamespace(message=AIMessageChunk(content=part)))
    deltas = [kw["text"] for args, kw in emitted if args[2] == "answer_delta"]
    assert deltas, "A long answer must be visible before completion"
    assert len(deltas) <= 30, "Do not synchronously commit every few characters"
    assert all(text.startswith(value) for value in deltas)
    callback.complete(text)
    assert emitted[-1][0][2] == "answer_completed"
    assert emitted[-1][1]["text"] == text
