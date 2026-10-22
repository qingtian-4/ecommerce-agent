"""τ half-duplex bridge to the actual ACS loop; the official environment executes all tools.

The bridge pauses at the loop's published tool-call event, returns it to the
orchestrator, then feeds the official ToolMessage back into registry.run(). It
does not invoke environment functions itself or substitute local business data.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from collections import Counter
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from tau2.agent.base_agent import HalfDuplexAgent
from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage

from acs.config import get_settings
from acs.cs_tools import TraceEvent
from acs.service_agent import CustomerServiceAgent


class OfficialRegistry:
    def __init__(self, tools, *, normalize_results=False):
        self.tools = tools
        self.reply = None
        self.normalize_results = normalize_results
        self.uncertain_write = False
        self.protocol_error = False
        self.protocol_records = []
        self.object_snapshots = {}
        self.before_mutation = None
        # These are tool contracts, not task IDs, answers, or routing rules.
        self.contracts = {t.name: t for t in tools if hasattr(t, 'returns')}
        self.business_errors = {}
        for name, tool in self.contracts.items():
            patterns = []
            tree = ast.parse(textwrap.dedent(inspect.getsource(tool._func)))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                        and isinstance(node.exc.func, ast.Name)
                        and node.exc.func.id == 'ValueError' and node.exc.args):
                    continue
                value = node.exc.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    patterns.append(re.escape(value.value))
                elif isinstance(value, ast.JoinedStr):
                    patterns.append(''.join(re.escape(v.value) if isinstance(v, ast.Constant)
                                            else '.*' for v in value.values))
            self.business_errors[name] = patterns

    @property
    def mutation_tools(self):
        return {name for name, tool in self.contracts.items()
                if getattr(tool._func, '__mutates_state__', True)}

    def verify_mutation(self, name, arguments, payload, error, user_id):
        """Pinned Retail write postconditions, never model text or reference actions."""
        value = payload.get('result')
        good = not error and isinstance(value, dict)
        before = self.before_mutation
        claims = []
        facts = {}
        if name in {'modify_user_address', 'modify_pending_order_address'}:
            key = 'user_id' if name == 'modify_user_address' else 'order_id'
            fields = {k:v for k,v in arguments.items() if k != key}
            good = good and bool(fields) and value.get(key) == arguments.get(key) and all(
                value.get('address', {}).get(k) == v for k,v in fields.items())
            claims = ['address']
            facts = {'address':value.get('address')} if good else {}
        elif name == 'cancel_pending_order':
            good = (good and value.get('order_id') == arguments.get('order_id')
                    and value.get('status') == 'cancelled'
                    and value.get('cancel_reason') == arguments.get('reason'))
            claims = ['cancel']
            facts = {'status':'cancelled','reason':arguments.get('reason')}
        elif name in {'return_delivered_order_items', 'exchange_delivered_order_items'}:
            prefix = 'return' if name.startswith('return') else 'exchange'
            good = (good and value.get('order_id') == arguments.get('order_id')
                    and value.get('status') == prefix+' requested'
                    and value.get(prefix+'_items') == sorted(arguments.get('item_ids', []))
                    and value.get(prefix+'_payment_method_id')
                    == arguments.get('payment_method_id'))
            if prefix == 'exchange':
                good = good and value.get('exchange_new_items') == sorted(
                    arguments.get('new_item_ids', []))
            claims = ['application' if prefix == 'return' else 'modify']
            facts = {'status':prefix+' requested','items':arguments.get('item_ids'),
                     'new_items':arguments.get('new_item_ids')}
        elif name == 'modify_pending_order_items':
            actual = [item['item_id'] for item in value.get('items', [])] if good else []
            expected = arguments.get('new_item_ids', [])
            old = arguments.get('item_ids', [])
            counts = Counter(i['item_id'] for i in (before or {}).get('items', []))
            expected_counts = counts.copy()
            expected_counts.subtract(old)
            expected_counts.update(expected)
            good = (good and value.get('order_id') == arguments.get('order_id')
                    and value.get('status') == 'pending (item modified)' and bool(expected)
                    and before is not None and not (Counter(old)-counts)
                    and Counter(actual) == +expected_counts)
            facts = {'status':'pending (item modified)','items':actual}
            claims = ['modify']
        elif name == 'modify_pending_order_payment':
            payments = value.get('payment_history', []) if good else []
            previous = (before or {}).get('payment_history', [])
            good = (good and value.get('order_id') == arguments.get('order_id')
                    and len(previous) == 1 and len(payments) == 3
                    and payments[:1] == previous and payments[1] == {
                        'transaction_type':'payment','amount':previous[0]['amount'],
                        'payment_method_id':arguments.get('payment_method_id')}
                    and payments[2] == {'transaction_type':'refund','amount':previous[0]['amount'],
                        'payment_method_id':previous[0]['payment_method_id']})
            facts = {'payment_method_id':arguments.get('payment_method_id')}
            claims = ['modify']
        else:
            good = False
        return {'tool':name,'verified':bool(good),'claims':claims if good else [],
                'outcome':'verified' if good else 'postcondition_failed',
                'target':arguments.get('order_id', arguments.get('user_id')),
                'scope':str(arguments.get('order_id',arguments.get('user_id')))+'/'+name,
                'summary':('Verified '+name+' for '
                    +str(arguments.get('order_id',arguments.get('user_id')))+': '
                    +json.dumps(facts,ensure_ascii=False)+'.') if good else None}

    @property
    def names(self):
        return [t.openai_schema['function']['name'] for t in self.tools]

    def schemas(self):
        return [t.openai_schema for t in self.tools]

    def run(self, name, arguments, *, current_user_id):
        if self.reply is None:
            raise RuntimeError("Official tool result was not supplied")
        reply, self.reply = self.reply, None
        if self.normalize_results:
            return self._normalized(name, arguments, reply)
        try:
            payload = json.loads(reply.content or "null")
        except json.JSONDecodeError:
            payload = reply.content
        if reply.error and not isinstance(payload, dict):
            payload = {'error': payload, 'error_type': 'official_tool_error'}
        return (
            payload,
            [
                TraceEvent("tool_call", name, {"arguments": arguments}),
                TraceEvent("tool_result", name, {"result": payload}),
            ],
            (f"official_tool_error:{name}" if reply.error else None),
        )


    def _normalized(self, name, arguments, reply):
        tool = self.contracts.get(name)
        mutates = getattr(getattr(tool, '_func', None), '__mutates_state__', True)
        category, reason = 'ok', None
        raw = reply.content
        if reply.error:
            bad_binding = False
            if tool is not None:
                try:
                    inspect.signature(tool._func).bind(**json.loads(arguments))
                except (TypeError, ValueError):
                    bad_binding = True
            reason = raw or 'Missing official error content'
            if bad_binding:
                category = 'invalid_arguments'
            elif reason.startswith('Error: ') and reason.endswith('not found'):
                category = 'not_found'
            elif any(re.fullmatch('Error: '+pat, reason)
                     for pat in self.business_errors.get(name, [])):
                category = 'business_rejected'
            else:
                category = 'tool_failure'
            payload = {'error_type': category, 'error': raw}
        else:
            try:
                if tool is None or raw is None:
                    raise ValueError('Missing tool contract or response')
                value = (raw if tool.returns.model_fields['returns'].annotation is str
                         else json.loads(raw))
                # Validation never substitutes coerced data for the original facts.
                tool.returns.model_validate({'returns': value}, strict=True)
                payload = {'result': value}
            except (ValueError, TypeError) as exc:
                category, reason = 'invalid_result', str(exc)
                payload = {'error_type': category, 'error': reason,
                           'official_content': raw}
        if category == 'ok' and isinstance(payload.get('result'), dict):
            value = payload['result']
            key = value.get('order_id',value.get('user_id'))
            if key:
                self.before_mutation = self.object_snapshots.get(key) if mutates else None
                self.object_snapshots[key] = json.loads(json.dumps(value))
        self.protocol_error = category == 'invalid_result'
        self.uncertain_write = mutates and category in {'tool_failure', 'invalid_result'}
        detail = {'tool': name, 'category': category, 'reason': reason,
                  'mutates_state': mutates, 'uncertain_write': self.uncertain_write,
                  'official_error': reply.error, 'official_content': raw}
        self.protocol_records.append(detail)
        events = [TraceEvent('tool_call', name, {'arguments': arguments}),
                  TraceEvent('tool_result', name, {'result': payload, **detail})]
        error = None
        if category != 'ok':
            error = f'official_{category}:{name}'
            events.append(TraceEvent('error', name, {'error_type': category, **detail}))
        return payload, events, error


@dataclass
class ACSState:
    agent: Any
    stream: Any = None
    seen_calls: int = 0
    trace_cursor: int = 0
    pending_id: str | None = None


class ACSAgent(HalfDuplexAgent[ACSState]):
    def __init__(self, tools, domain_policy, *, settings=None,
                 loop_class=CustomerServiceAgent, llm_factory=None, normalize_results=False,
                 protocol_failure=None):
        super().__init__(tools, domain_policy)
        self.settings = settings
        self.loop_class = loop_class
        self.llm_factory = llm_factory
        self.normalize_results = normalize_results
        self.protocol_failure = protocol_failure

    def get_init_state(self, message_history=None):
        settings = (self.settings or get_settings()).model_copy(update={"context_max_chars": 64000})
        llm = self.llm_factory(settings) if self.llm_factory else None
        agent = self.loop_class(settings=settings, llm=llm, registry=OfficialRegistry(
            self.tools, normalize_results=self.normalize_results))
        agent.messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    agent_instruction=AGENT_INSTRUCTION,
                    domain_policy=self.domain_policy,
                ),
            }
        ]
        for message in message_history or []:
            if isinstance(message, ToolMessage):
                agent.messages.append({"role": "tool", "tool_call_id": message.id,
                                       "content": message.content})
            else:
                row = {"role": message.role, "content": message.content}
                if message.tool_calls:
                    row["tool_calls"] = [
                        {"id": c.id, "type": "function", "function": {
                            "name": c.name, "arguments": json.dumps(c.arguments)}}
                        for c in message.tool_calls
                    ]
                agent.messages.append(row)
        return ACSState(agent)

    def generate_next_message(self, message, state):
        if isinstance(message, UserMessage):
            if state.stream is not None:
                raise RuntimeError("New user message arrived before the previous turn finished")
            settings = state.agent.settings
            # Independently loaded V1 has its own BudgetExceeded class identity.
            budget_type = import_module(state.agent.__class__.__module__).RunBudget
            state.stream = state.agent.chat_stream(
                message.content or "",
                budget=budget_type(settings.turn_timeout_s, settings.max_api_requests),
            )
            state.seen_calls = state.trace_cursor = 0
        elif isinstance(message, ToolMessage):
            if message.id != state.pending_id:
                raise ValueError("Official tool result id does not match pending call")
            state.agent.registry.reply = message
            state.pending_id = None
        else:
            raise TypeError("Adapter emits serial tool calls; expected a single ToolMessage")
        for result in state.stream:
            if state.agent.registry.uncertain_write or state.agent.registry.protocol_error:
                reason = ('state_uncertain' if state.agent.registry.uncertain_write
                          else 'adapter_protocol_error')
                if self.protocol_failure:
                    self.protocol_failure(reason)
                # Both ACS arms use the same transport guard. Never ask a model to
                # replay a mutation whose receipt cannot be established.
                state.stream.close()
                state.stream = None
                completed = {m.get('tool_call_id') for m in state.agent.messages
                             if m['role'] == 'tool'}
                for m in list(state.agent.messages):
                    for c in m.get('tool_calls', []):
                        if c['id'] not in completed:
                            state.agent.messages.append({'role': 'tool', 'tool_call_id': c['id'],
                                'content': '{"error_type":"state_uncertain","executed":false}'})
                text = ('The tool receipt is uncertain or violates its declared protocol. '
                        'No further operations were attempted.')
                state.agent.messages.append({'role': 'assistant', 'content': text})
                return AssistantMessage.text(text, raw_data={
                    'acs_stop_reason': reason,
                    'acs_trace': [e.as_dict() for e in result.trace],
                    'protocol_guard': state.agent.registry.protocol_records[-1],
                }), state
            for event in result.trace[state.trace_cursor :]:
                state.trace_cursor += 1
                if event.type != "tool_call":
                    continue
                # The current batch is already on the real loop's message history.
                calls = next(
                    m["tool_calls"] for m in reversed(state.agent.messages) if m.get("tool_calls")
                )
                completed = sum(
                    m["role"] == "tool"
                    for m in reversed(state.agent.messages)
                    if m.get("tool_call_id") in {c["id"] for c in calls}
                )
                call = calls[completed]
                route = next((e.detail for e in result.trace if e.type == 'route_selected'), None)
                if route is not None and call['function']['name'] not in route['allowed_tools']:
                    # Loop publishes the intent before it enforces scope. Do not let the
                    # half-duplex bridge execute a rejected intent in the official env.
                    continue
                state.pending_id = call["id"]
                return AssistantMessage.text(
                    "",
                    tool_calls=[
                        ToolCall(
                            id=call["id"],
                            name=call["function"]["name"],
                            arguments=json.loads(call["function"]["arguments"]),
                        )
                    ],
                ), state
            if result.stop_reason != "running":
                # Exhaust the generator so its budget cleanup is not delayed to GC.
                state.stream.close()
                state.stream = None
                return AssistantMessage.text(
                    result.answer,
                    usage={
                        "prompt_tokens": result.totals.get("prompt_tokens", 0),
                        "completion_tokens": result.totals.get("completion_tokens", 0),
                    },
                    raw_data={
                        "acs_stop_reason": result.stop_reason,
                        "acs_totals": result.totals,
                        "acs_trace": [e.as_dict() for e in result.trace],
                    },
                ), state
        raise RuntimeError("ACS loop ended without a final result")

    def is_stop(self, message):
        if (message.raw_data or {}).get('acs_stop_reason') in {
            'state_uncertain', 'adapter_protocol_error',
        }:
            return True
        return super().is_stop(message)

    def stop(self, message=None, state=None):
        if state is not None:
            if state.stream is not None:
                state.stream.close()
                state.stream = None
            client = getattr(state.agent.llm, '_client', None)
            if client is not None:
                client.close()


def create_acs_agent(tools, domain_policy, **kwargs):
    return ACSAgent(tools=tools, domain_policy=domain_policy)
