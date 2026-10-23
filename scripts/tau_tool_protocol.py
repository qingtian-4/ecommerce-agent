"""Receipt validation shared by both adapters; never executes a domain tool.

The return-contract and declared ValueError inspection follows the independently
reviewed f780f17 OfficialRegistry. Raw official content remains auditable.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap


class OfficialToolProtocol:
    def __init__(self, tools):
        self.tools = {tool.name: tool for tool in tools}
        self.business_errors = {}
        for name, tool in self.tools.items():
            patterns = []
            tree = ast.parse(textwrap.dedent(inspect.getsource(tool._func)))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Raise)
                    and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "ValueError"
                    and node.exc.args
                ):
                    continue
                value = node.exc.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    patterns.append(re.escape(value.value))
                elif isinstance(value, ast.JoinedStr):
                    patterns.append(
                        "".join(
                            re.escape(v.value) if isinstance(v, ast.Constant) else ".*"
                            for v in value.values
                        )
                    )
            self.business_errors[name] = patterns

    def inspect(self, call, reply):
        tool = self.tools.get(call.name)
        mutates = getattr(getattr(tool, "_func", None), "__mutates_state__", True)
        category = "ok"
        try:
            payload = json.loads(reply.content or "null")
        except json.JSONDecodeError:
            payload = reply.content
        if reply.error:
            try:
                if tool is None:
                    raise ValueError("Unknown tool")
                inspect.signature(tool._func).bind(**call.arguments)
            except (TypeError, ValueError):
                category = "invalid_arguments"
            else:
                raw = reply.content or ""
                if any(
                    re.fullmatch("Error: " + p, raw)
                    for p in self.business_errors.get(call.name, [])
                ):
                    category = "business_rejected"
                else:
                    category = "tool_failure"
        else:
            try:
                if tool is None or reply.content is None:
                    raise ValueError("Missing contract or receipt")
                value = (
                    reply.content
                    if tool.returns.model_fields["returns"].annotation is str
                    else json.loads(reply.content)
                )
                tool.returns.model_validate({"returns": value}, strict=True)
            except (ValueError, TypeError):
                category = "invalid_result"
        return {
            "tool": call.name,
            "tool_call_id": call.id,
            "category": category,
            "mutates_state": mutates,
            "uncertain_write": mutates and category in {"tool_failure", "invalid_result"},
            "protocol_error": category == "invalid_result",
            "official_error": reply.error,
            "official_content": reply.content,
            # Keep the old Loop's JSON serialization, including quoted string results.
            "model_content": json.dumps(payload, ensure_ascii=False, default=str),
        }
