"""
Model access for the agent: one JSON-returning call, and a stub for tests.

Two rules hold everywhere the agent talks to Claude:
  - Structured output, never free text we have to parse by hand (`output_config.format`).
  - The long, stable part of the prompt (schema, metric definitions) goes in the system block with
    `cache_control`, so repeated questions reuse the cached prefix instead of paying for it again.
"""
import json
from dataclasses import dataclass, field

from people_ai.config import ANSWER_EFFORT, ANSWER_MODEL, ROUTER_MODEL


class ModelError(RuntimeError):
    """The model could not be reached, refused, or returned something unusable."""


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_written_tokens: int = 0

    @property
    def total_input(self):
        """Tokens written to the cache are billed too, and don't appear in input_tokens."""
        return self.input_tokens + self.cached_tokens + self.cache_written_tokens

    def __str__(self):
        return (f"{self.model}: {self.total_input} in ({self.cached_tokens} cache read, "
                f"{self.cache_written_tokens} cache write), {self.output_tokens} out")


class Claude:
    """A thin wrapper over the Messages API. One method, because the agent only needs one shape of call."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic                       # imported lazily so tests run without credentials
            self._client = anthropic.Anthropic()
        return self._client

    def json(self, *, system, user, schema, model=ANSWER_MODEL, max_tokens=8000, effort=None):
        """Ask for one JSON object matching `schema`. Returns (data, usage)."""
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except Exception as error:                 # network, auth, rate limit: the caller decides what to say
            raise ModelError(f"{type(error).__name__}: {error}") from error
        if response.stop_reason == "refusal":
            raise ModelError(f"the model declined this request ({getattr(response.stop_details, 'category', None)})")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ModelError("no text block in the response")
        usage = Usage(model=model, input_tokens=response.usage.input_tokens,
                      output_tokens=response.usage.output_tokens,
                      cached_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                      cache_written_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
        try:
            return json.loads(text), usage
        except json.JSONDecodeError as error:
            raise ModelError(f"response was not valid JSON: {text[:200]}") from error


@dataclass
class StubClaude:
    """A scripted model for tests: returns queued answers and records what it was asked."""
    answers: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def json(self, *, system, user, schema, model=ROUTER_MODEL, max_tokens=8000, effort=None):
        self.calls.append(dict(system=system, user=user, model=model, schema=schema))
        if not self.answers:
            raise ModelError("stub ran out of scripted answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, Usage(model=model)


DEFAULTS = dict(router_model=ROUTER_MODEL, answer_model=ANSWER_MODEL, answer_effort=ANSWER_EFFORT)
