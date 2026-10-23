"""Explicit CLI for the optional V3 experiment. No default application switch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from acs.config import Settings
from acs.langgraph_poc import ReadOnlyPoc, deepseek_model
from acs.poc_budget import PocBudget
from acs.scenarios import build_scenario, scenario_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--user-id", default="USER001", help="应用注入的模拟身份，不是网络认证")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="创建全新合成场景，拒绝覆盖已有目录")
    for name in ("ask", "resume", "inspect"):
        sub = commands.add_parser(name)
        sub.add_argument("session_id")
        if name == "ask":
            sub.add_argument("text")
    args = parser.parse_args()
    root = args.state_dir.resolve()
    if args.command == "init":
        build_scenario(root)
        print(json.dumps({"synthetic": True, "state_dir": str(root)}, ensure_ascii=False))
        return
    settings = Settings(**({"_env_file": args.env_file} if args.env_file else {}))
    settings = scenario_settings(settings, root)
    # Inspect loads native state without credentials or a paid model call.
    if args.command == "inspect":
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

        model = FakeMessagesListChatModel(responses=[])
    else:
        if settings.llm_model != "deepseek-flash" or settings.llm_max_output_tokens > 1024:
            raise ValueError("真实冒烟仅核准 deepseek-flash，max output <= 1024")
        meter = PocBudget(root / "requests.jsonl",
                          max_output_tokens=settings.llm_max_output_tokens)
        model = deepseek_model(settings, callbacks=[meter])
    with ReadOnlyPoc(settings, root / "checkpoints.sqlite", args.user_id, model=model) as poc:
        if args.command == "inspect":
            state = poc.snapshot(args.session_id)
            print(json.dumps({"next": state.next, "checkpoint": state.config,
                              "messages": [m.model_dump(mode="json")
                                           for m in state.values.get("messages", [])]},
                             ensure_ascii=False))
        else:
            for event in poc.stream(args.session_id, getattr(args, "text", None)):
                print(event.model_dump_json(), flush=True)


if __name__ == "__main__":
    main()
