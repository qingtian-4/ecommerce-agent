"""Legacy-loop boundary adapter to the same complete response contract as Graph."""
from __future__ import annotations

from acs.model_response_contract import ModelResponseError, require_response


def legacy_calls(response):
    return [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls]


class ContractLLM:
    """Delegate the real loop's LLM; neither execute tools nor add a model loop."""

    def __init__(self, inner, tools, used_ids=()):
        self.inner, self.tools = inner, {t.openai_schema["function"]["name"] for t in tools}
        self.used_ids, self.rejection = set(used_ids), None

    def __getattr__(self, key):
        return getattr(self.inner, key)

    @property
    def budget(self):
        return self.inner.budget

    @budget.setter
    def budget(self, value):
        self.inner.budget = value

    def validate(self, response):
        try:
            require_response(response.text, legacy_calls(response),
                getattr(response, 'finish_reason', None), known_tools=self.tools,
                used_ids=self.used_ids)
        except ModelResponseError as exc:
            self.rejection = exc.predicate
            raise
        self.used_ids.update(c.id for c in response.tool_calls)
        return response

    def chat(self, *args, **kwargs):
        try:
            return self.validate(self.inner.chat(*args, **kwargs))
        except ModelResponseError as exc:
            self.rejection = exc.predicate
            raise

    def chat_stream(self, *args, **kwargs):
        try:
            for response in self.inner.chat_stream(*args, **kwargs):
                if not isinstance(response, str):
                    yield self.validate(response)
                # Official HalfDuplex never publishes intermediate text as completion.
        except ModelResponseError as exc:
            self.rejection = exc.predicate
            raise
