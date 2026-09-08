"""Shared fake-LLM test doubles.

The house convention (repeated inline across ~6 test files before this
extraction) is a duck-typed AIMessage plus a stub client exposing async
``ainvoke``. ``ScriptedLLM`` generalizes the existing ``_SeqLLM`` pattern
(see ``test_planner_decompose_hints.py``) for tests that need a distinct
client per resolved model name — e.g. ladder escalation tests where rung 0
and rung 1 resolve to different pool entries.
"""

from __future__ import annotations

from typing import Any


class FakeResp:
    """Duck-types a LangChain AIMessage: only ``.content`` is read by callers."""

    def __init__(self, content: Any, model: str = "stub-model"):
        self.content = content
        self.model = model
        self.usage_metadata = None
        self.response_metadata = {}


class StubLLM:
    """Single-response (or callable) stub. Records every call's messages."""

    def __init__(self, content: Any = "", *, model: str = "stub-model", raises: Exception | None = None):
        self.content = content
        self.model = model
        self.raises = raises
        self.calls = 0
        self.last_msgs: Any = None

    async def ainvoke(self, msgs):
        self.calls += 1
        self.last_msgs = msgs
        if self.raises is not None:
            raise self.raises
        content = self.content(msgs) if callable(self.content) else self.content
        return FakeResp(content, model=self.model)


class ScriptedLLM:
    """Queued responses/exceptions, one per call. Raises IndexError if exhausted."""

    def __init__(self, responses: list[Any], *, model: str = "stub-model"):
        self.responses = list(responses)
        self.model = model
        self.calls = 0
        self.invocations: list[Any] = []

    async def ainvoke(self, msgs):
        self.invocations.append(msgs)
        item = self.responses[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return FakeResp(item, model=self.model)
