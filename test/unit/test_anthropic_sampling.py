"""Anthropic chat clients must not send sampling params the API rejects."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from core.llm_provider_management.providers.anthropic import _chat

_SAMPLING = ("temperature", "top_p", "top_k")


def test_anthropic_chat_omits_sampling_params():
    llm = _chat("claude-sonnet-5", api_key="sk-ant-test", base_url=None)
    payload = llm._get_request_payload([HumanMessage(content="hi")])
    extra = payload.get("extra_body") or {}
    for key in _SAMPLING:
        assert key not in payload
        assert key not in extra


def test_anthropic_chat_default_model_omits_sampling_params():
    llm = _chat("", api_key=None, base_url=None)
    assert llm.model == "claude-sonnet-5"
    payload = llm._get_request_payload([HumanMessage(content="hi")])
    extra = payload.get("extra_body") or {}
    for key in _SAMPLING:
        assert key not in payload
        assert key not in extra
