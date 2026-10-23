"""Complete-response contract shared ONLY by the official tau adapters.

Mixed text/tool responses are valid model context, never an outbound final answer.
An invalid member rejects the entire batch before any official execution. Argument
schema/business validity belongs to the official executor; JSON object syntax does not.
"""
from __future__ import annotations

import json


class ModelResponseError(RuntimeError):
    def __init__(self, predicate, *, layer="adapter_rule"):
        self.predicate, self.layer = predicate, layer
        super().__init__(predicate)


def inspect_response(text, calls, finish_reason, *, known_tools, used_ids=(),
                     invalid_calls=(), stream_complete=True):
    """Return a content-free structural report; never normalize/mutate messages."""
    invalid = bool(invalid_calls)
    parsed, names, ids, states = [], [], [], []
    for call in calls:
        name, cid, arguments = call.get("name"), call.get("id"), call.get("arguments")
        names.append(name if name in known_tools else "<unknown>")
        ids.append(cid)
        try:
            value = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(value, dict):
                raise ValueError("Arguments must be a JSON object")
            parsed.append(value)
            states.append("object")
        except (TypeError, ValueError):
            invalid = True
            states.append("invalid_json_object")
    predicate = None
    if not stream_complete:
        predicate = "incomplete_stream"
    elif text is not None and not isinstance(text, str):
        predicate = "invalid_content_type"
    elif finish_reason is not None and finish_reason not in ("stop", "tool_calls"):
        predicate = "invalid_finish_reason"
    elif invalid:
        predicate = "invalid_tool_calls"
    elif any(not isinstance(cid, str) or not cid.strip() for cid in ids):
        predicate = "empty_tool_call_id"
    elif len(ids) != len(set(ids)):
        predicate = "duplicate_tool_call_id"
    elif set(ids).intersection(used_ids):
        predicate = "reused_tool_call_id"
    elif any(name == "<unknown>" for name in names):
        predicate = "unknown_tool"
    elif finish_reason == "tool_calls" and not calls:
        predicate = "missing_tool_calls"
    elif not calls and (not isinstance(text, str) or not text.strip()):
        predicate = "empty_response"
    return {"predicate": predicate, "finish_reason": finish_reason,
            "has_text": bool(text), "text_length": len(text) if isinstance(text, str) else 0,
            "tool_call_count": len(calls), "tool_names": names,
            "argument_parse_states": states, "invalid_tool_calls": bool(invalid_calls),
            "mixed_content": bool(text and calls), "stream_complete": stream_complete}


def require_response(text, calls, finish_reason, *, evidence=None, context=None, **kwargs):
    report = inspect_response(text, calls, finish_reason, **kwargs)
    if evidence is not None:
        evidence(report, context or {})  # durable BEFORE raising
    if report["predicate"]:
        raise ModelResponseError(report["predicate"])
    return report


def wire_response(content, *, streaming):
    """Decode complete HTTP JSON/SSE for validation without rewriting SDK bytes."""
    if not streaming:
        data = json.loads(content)
        choice = data["choices"][0]
        return choice["message"], choice.get("finish_reason"), True
    text, calls, finish, done = "", {}, None, False
    for line in content.splitlines():
        if not line.startswith(b"data: "):
            continue
        if line[6:] == b"[DONE]":
            done = True
            continue
        data = json.loads(line[6:])
        for choice in data.get("choices", []):
            if choice.get("index", 0) != 0:
                raise ValueError("Multiple choices are outside the frozen contract")
            delta = choice.get("delta", {})
            text += delta.get("content") or ""
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
            for fragment in delta.get("tool_calls") or []:
                call = calls.setdefault(fragment["index"], {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                call["id"] += fragment.get("id") or ""
                for field in ("name", "arguments"):
                    call["function"][field] += (fragment.get("function") or {}).get(field) or ""
    return {"content": text, "tool_calls": [calls[i] for i in sorted(calls)]}, finish, (
        done)


def inspect_wire(content, payload):
    message, finish, complete = wire_response(content, streaming=bool(payload.get("stream")))
    calls = []
    for call in message.get("tool_calls") or []:
        if call.get("type", "function") != "function":
            raise ValueError("Unsupported tool call type")
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("SDK tool arguments must be JSON text")
        calls.append({"id": call.get("id"), "name": function.get("name"),
                      "arguments": arguments})
    report = inspect_response(message.get("content") or "", calls, finish,
        known_tools={t["function"]["name"] for t in payload.get("tools", [])},
        used_ids={c["id"] for m in payload.get("messages", []) for c in m.get("tool_calls") or []},
        stream_complete=complete)
    if not report["predicate"] and finish is None:
        report["predicate"] = "missing_finish_reason"
    return report
