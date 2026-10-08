"""Tests for the UI event handlers.

These exist because the handlers used to be nested inside `build_app()`, which
made them unimportable and therefore untested. A `TypeError` from passing
`settings` positionally to a keyword-only parameter then reached the browser as a
silent gradio 500: the page rendered, every send failed, and nothing in the
project caught it.

Two layers of protection now:

* `test_agent_constructor_rejects_positional_settings` pins the exact signature
  mistake, using `inspect.signature` rather than calling it, so it fails in
  milliseconds with a clear message instead of only at runtime.
* the handler tests call the real handlers with a scripted LLM, so any handler
  whose return arity or behaviour breaks fails here.
"""

from __future__ import annotations

import inspect

import app as app_module
import gradio as gr
import pytest

from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse


@pytest.fixture(autouse=True)
def isolated_ui_settings(settings, monkeypatch):
    monkeypatch.setattr(app_module, "SETTINGS", settings.model_copy(
        update={"llm_api_key": "audit-ui-placeholder"}
    ))
    monkeypatch.setattr(app_module, "_KB_STATS", None)


# --------------------------------------------------------------------------- #
# the exact bug that reached the browser
# --------------------------------------------------------------------------- #
def test_agent_constructor_rejects_positional_settings() -> None:
    """`settings` is keyword-only; `CustomerServiceAgent(SETTINGS)` must fail.

    Guards the specific call that was wrong, without needing an API key.
    """
    sig = inspect.signature(CustomerServiceAgent.__init__)
    settings_param = sig.parameters["settings"]
    assert settings_param.kind is inspect.Parameter.KEYWORD_ONLY, (
        "if `settings` ever becomes positional, app.py's call sites silently "
        "keep working while other call sites break -- keep it keyword-only and "
        "keep the call sites explicit"
    )

    with pytest.raises(TypeError):
        CustomerServiceAgent(object())  # type: ignore[misc]


def test_app_constructs_the_agent_with_a_keyword(tmp_path, monkeypatch) -> None:
    """A real send must be able to build an agent through app.ensure_agent."""
    captured: dict[str, object] = {}

    class _Recorder(CustomerServiceAgent):
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            captured["args"] = args
            captured["kwargs"] = kwargs
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_module, "CustomerServiceAgent", _Recorder)
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}
    app_module.ensure_agent(state, app_module.USER_CHOICES[0])

    assert captured["args"] == (), (
        "app.ensure_agent must pass settings by keyword; positional args are the bug"
    )
    assert "settings" in captured["kwargs"]
    assert captured["kwargs"]["current_user_id"] == "USER001"


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_user_id_from_key_extracts_the_id() -> None:
    assert app_module.user_id_from_key("USER002 · 李娜 · 139****9002") == "USER002"
    assert app_module.user_id_from_key("USER001") == "USER001"
    assert app_module.user_id_from_key(None) == "USER001"
    assert app_module.user_id_from_key("") == "USER001"


def test_label_for_id_falls_back_for_unknown_users() -> None:
    assert app_module.label_for_id("USER003").startswith("王芳")
    assert app_module.label_for_id("NOPE") == app_module.USERS[0][1]


def test_every_user_choice_is_well_formed() -> None:
    assert len(app_module.USER_CHOICES) == len(app_module.USER_IDS)
    for key, uid in zip(app_module.USER_CHOICES, app_module.USER_IDS, strict=True):
        assert app_module.user_id_from_key(key) == uid


# --------------------------------------------------------------------------- #
# rendering must never raise
# --------------------------------------------------------------------------- #
def test_renderers_handle_the_empty_state() -> None:
    assert "尚未发送消息" in app_module.render_trace(None)
    assert "—" in app_module.render_stats(None)


def test_header_and_user_card_render_for_every_user() -> None:
    for key in app_module.USER_CHOICES:
        header = app_module.render_header(key)
        assert app_module.SHOP_NAME in header
        assert app_module.user_id_from_key(key) in header

        card = app_module.render_user_card(app_module.user_id_from_key(key), key)
        assert "当前会话" in card
        assert "订单列表" in card


def test_sidebar_lists_only_the_selected_users_orders():
    first = app_module.render_user_card("USER001", "张伟")
    second = app_module.render_user_card("USER002", "李娜")
    assert "O10024" in first
    assert "O10024" not in second
    assert "O10029" in second
    assert "O10029" not in first


def test_render_trace_shows_an_error_box_when_given_one() -> None:
    out = app_module.render_trace(None, error="TypeError: boom")
    assert "err-box" in out
    assert "TypeError: boom" in out


def test_render_trace_escapes_html_from_tool_output(settings, fake_embedder) -> None:
    """Tool results and model text are untrusted; they must not inject markup."""
    from acs.cs_tools import ToolRegistry
    from acs.rag import KnowledgeBase

    # KnowledgeBase needs `embed`, which ScriptedLLM deliberately does not have
    # (it fakes a chat model, not an embedding model). Use the fake embedder.
    llm = ScriptedLLM([ScriptedResponse(text="<script>alert(1)</script>")])
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    agent = CustomerServiceAgent(
        settings=settings, llm=llm, registry=ToolRegistry(settings, kb=kb)
    )
    result = agent.chat("你好")
    out = app_module.render_trace(result)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# --------------------------------------------------------------------------- #
# handlers: arity and behaviour
# --------------------------------------------------------------------------- #
def _stub_agent(monkeypatch, responses: list[ScriptedResponse]) -> ScriptedLLM:
    """Make app.ensure_agent return an agent backed by a scripted LLM."""
    llm = ScriptedLLM(responses)
    real = app_module.ensure_agent

    def patched(state_dict: dict, user_key: str):
        agent = real(state_dict, user_key)
        agent.llm = llm
        return agent

    monkeypatch.setattr(app_module, "ensure_agent", patched)
    return llm


def test_all_handlers_return_the_same_seven_values(monkeypatch) -> None:
    _stub_agent(monkeypatch, [ScriptedResponse(text="您好。")])
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}

    for value in (
        app_module.on_send("你好", [], state, app_module.USER_CHOICES[0]),
        app_module.on_clear(state, app_module.USER_CHOICES[0]),
        app_module.on_user_change(app_module.USER_CHOICES[1], state),
    ):
        assert isinstance(value, tuple)
        assert len(value) == 7, "handler arity must match the 7 declared outputs"
        history, box, trace, state_out, stats, card, header = value
        assert isinstance(history, list)
        assert box == ""
        assert isinstance(trace, str) and trace
        assert isinstance(state_out, dict)
        assert isinstance(stats, str) and stats
        assert isinstance(card, str) and card
        assert isinstance(header, str) and header


def test_on_send_returns_history_and_trace(monkeypatch) -> None:
    _stub_agent(monkeypatch, [ScriptedResponse(text="7 天内可退。")])
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}

    history, box, trace, _, stats, _, _ = app_module.on_send(
        "退货期限多久？", [], state, app_module.USER_CHOICES[0]
    )

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[-1]["content"] == "7 天内可退。"
    assert "工具调用" in trace or "决策摘要" in trace
    assert "延迟" in stats
    assert box == ""


def test_on_send_with_empty_message_is_a_no_op(monkeypatch) -> None:
    _stub_agent(monkeypatch, [ScriptedResponse(text="不应被调用")])
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}

    history, _, _, _, _, _, _ = app_module.on_send("   ", [], state, app_module.USER_CHOICES[0])
    assert history == []


def test_on_send_surfaces_an_exception_instead_of_raising(monkeypatch) -> None:
    """A failing turn must produce a visible error panel, not a gradio 500."""
    real = app_module.ensure_agent

    class _Boom:
        def chat(self, _message):  # noqa: ANN001
            raise RuntimeError("simulated backend failure")

        def reset(self) -> None:
            pass

        def set_user(self, _uid: str) -> None:
            pass

    def patched(state_dict: dict, user_key: str):
        real(state_dict, user_key)
        return _Boom()

    monkeypatch.setattr(app_module, "ensure_agent", patched)
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}

    history, _, trace, _, stats, _, _ = app_module.on_send(
        "你好", [], state, app_module.USER_CHOICES[0]
    )

    assert "系统出现异常" in history[-1]["content"]
    assert "simulated backend failure" in trace, "the real cause must be visible"
    assert "err-box" in trace
    assert stats


def test_on_clear_empties_history_and_resets_the_agent(monkeypatch) -> None:
    _stub_agent(monkeypatch, [ScriptedResponse(text="您好。")])
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}
    app_module.on_send("你好", [], state, app_module.USER_CHOICES[0])

    history, _, trace, _, _, card, _ = app_module.on_clear(state, app_module.USER_CHOICES[0])

    assert history == []
    assert "尚未发送消息" in trace
    assert "当前会话" in card


def test_on_user_change_switches_identity_and_drops_history(monkeypatch) -> None:
    _stub_agent(monkeypatch, [ScriptedResponse(text="您好。")])
    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}
    app_module.on_send("你好", [], state, app_module.USER_CHOICES[0])

    history, _, _, state_out, _, card, _ = app_module.on_user_change(
        app_module.USER_CHOICES[1], state
    )

    assert history == [], "history must be dropped so order details cannot leak"
    assert state_out["user_id"] == "USER002"
    assert state_out["agent"].current_user_id == "USER002"
    assert "USER002" in card


# --------------------------------------------------------------------------- #
# gradio component surface
# --------------------------------------------------------------------------- #
def test_gradio_component_arguments_still_exist() -> None:
    """Gradio 6 removed `Chatbot(type=...)`; this catches the next such removal."""
    chatbot_params = inspect.signature(gr.Chatbot.__init__).parameters
    for name in ("placeholder", "show_label", "height"):
        assert name in chatbot_params, f"Chatbot lost {name!r}"
    assert "type" not in chatbot_params, (
        "Chatbot(type=...) is gone in Gradio 6; messages is the only format"
    )

    launch_params = inspect.signature(gr.Blocks.launch).parameters
    for name in ("css", "theme", "server_name", "server_port", "show_error"):
        assert name in launch_params, f"launch() lost {name!r}"
    assert "show_api" not in launch_params, "launch(show_api=...) is gone in Gradio 6"


def test_on_send_with_the_real_agent_constructor(settings, fake_embedder, monkeypatch):
    """End-to-end through the handler that actually failed in the browser.

    Every other handler test replaces `ensure_agent`, which is fine for testing
    handler logic but would NOT have caught the original bug: the failure was in
    constructing `CustomerServiceAgent` itself. This one constructs it for real,
    against a temp database and index, and would raise the same
    `TypeError: takes 1 positional argument but 2 were given` if the call site
    regressed to passing `settings` positionally.
    """
    from acs.llm import LLMClient
    from acs.rag import KnowledgeBase

    # A dummy key so LLMClient's constructor accepts the settings, then the real
    # network client is replaced by a scripted one so nothing is called. Without
    # the key, construction itself throws and this test would pass or fail for
    # the wrong reason.
    usable = type(settings)(
        _env_file=None,
        db_path=settings.db_path,
        docs_dir=settings.docs_dir,
        index_dir=settings.index_dir,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        llm_api_key="sk-test-not-real",
    )

    monkeypatch.setattr(app_module, "SETTINGS", usable)
    monkeypatch.setattr(
        app_module, "KnowledgeBase", lambda *_a, **_k: KnowledgeBase(usable, llm=fake_embedder)
    )
    monkeypatch.setattr(
        app_module, "ToolRegistry", lambda *_a, **_k: ToolRegistry(usable, kb=None)
    )
    monkeypatch.setattr(
        LLMClient, "__post_init__", lambda self: setattr(self, "_client", None)
    )

    state: dict = {"user_key": app_module.USER_CHOICES[0], "agent": None}
    history, _, trace, state_out, stats, _, _ = app_module.on_send(
        "你好", [], state, app_module.USER_CHOICES[0]
    )

    assert state_out["agent"] is not None, "the real agent constructor must be reached"
    assert state_out["user_id"] == "USER001"
    assert state_out["agent"].current_user_id == "USER001"
    assert len(history) == 2
    assert trace and stats
