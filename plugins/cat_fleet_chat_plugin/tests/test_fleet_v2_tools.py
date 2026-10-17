"""Fleet v2 wire contracts; the hub is doubled, never started."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.cat_fleet_chat_plugin.MCPTools import fleet_chat_tools as tools

_TOOLS = "plugins.cat_fleet_chat_plugin.MCPTools.fleet_chat_tools"
_SHAPE = {"response_format": "json", "limit": 3}

MAIL = [
    ("fleet_mail_send", {"sender": "sol", "recipient": "luna", "body": "hello", "subject": "review", "reply_to": 2, "client_request_id": "mail-1"}, "POST", "/api/v1/mailbox", "body", {"sender": "sol", "recipient": "luna", "body": "hello", "subject": "review", "reply_to": 2, "client_request_id": "mail-1"}),
    ("fleet_mail_list", {"agent": "sol", "state": "all", "since_id": 7, "limit": 10}, "GET", "/api/v1/mailbox/sol", "params", {"state": "all", "since_id": 7, "limit": 10}),
    ("fleet_mail_read", {"agent": "sol", "ids": [2, 3]}, "POST", "/api/v1/mailbox/sol/read", "body", {"ids": [2, 3]}),
    ("fleet_mail_read", {"agent": "sol", "up_to_id": 7}, "POST", "/api/v1/mailbox/sol/read", "body", {"up_to_id": 7}),
    ("fleet_mail_ack", {"agent": "sol", "ids": [2]}, "POST", "/api/v1/mailbox/sol/ack", "body", {"ids": [2]}),
    ("fleet_mail_counts", {"agent": "sol"}, "GET", "/api/v1/mailbox/sol/counts", "params", {}),
]
FEED = [
    ("fleet_feed_post", {"author": "sol", "body": "note", "quote_of": 4, "client_request_id": "post-1"}, "POST", "/api/v1/feed/posts", "body", {"author": "sol", "body": "note", "quote_of": 4, "client_request_id": "post-1"}),
    ("fleet_feed_reply", {"author": "sol", "parent_id": 4, "body": "reply", "client_request_id": "reply-1"}, "POST", "/api/v1/feed/posts", "body", {"author": "sol", "parent_id": 4, "body": "reply", "client_request_id": "reply-1"}),
    ("fleet_feed_repost", {"author": "sol", "post_id": 4}, "POST", "/api/v1/feed/posts/4/repost", "body", {"author": "sol"}),
    ("fleet_feed_react", {"handle": "sol", "post_id": 4, "kind": "+1"}, "PUT", "/api/v1/feed/posts/4/reactions/+1", "body", {"handle": "sol"}),
    ("fleet_feed_unreact", {"handle": "sol", "post_id": 4, "kind": "heart"}, "DELETE", "/api/v1/feed/posts/4/reactions/heart", "params", {"handle": "sol"}),
    ("fleet_feed_timeline", {"scope": "hashtag", "handle": "sol", "tag": "testing", "author": "luna", "cursor": "opaque/+=", "limit": 6}, "GET", "/api/v1/feed/timeline", "params", {"scope": "hashtag", "handle": "sol", "tag": "testing", "author": "luna", "cursor": "opaque/+=", "limit": 6}),
    ("fleet_feed_thread", {"post_id": 4, "cursor": "wide-thread"}, "GET", "/api/v1/feed/posts/4", "params", {"thread": 1, "cursor": "wide-thread"}),
]
KNOWLEDGE = [
    ("fleet_knowledge_add", {"project": "opencat", "fact": "Use query handles", "author": "sol", "topic": "tools", "source_run": "run", "source_step": "S01"}, "POST", "/api/v1/knowledge/facts", "body", {"project": "opencat", "fact": "Use query handles", "author": "sol", "topic": "tools", "source_run": "run", "source_step": "S01"}),
    ("fleet_knowledge_list", {"project": "opencat", "topic": "tools", "status": "active", "author": "sol", "cursor": "opaque/+=", "limit": 8}, "GET", "/api/v1/knowledge/facts", "params", {"project": "opencat", "topic": "tools", "status": "active", "author": "sol", "cursor": "opaque/+=", "limit": 8}),
    ("fleet_knowledge_set_status", {"actor": "sol", "fact_id": 9, "status": "superseded"}, "PATCH", "/api/v1/knowledge/facts/9", "body", {"actor": "sol", "status": "superseded"}),
    ("fleet_knowledge_topics", {"project": "opencat"}, "GET", "/api/v1/knowledge/topics", "params", {"project": "opencat"}),
]
PROPOSALS = [
    ("fleet_list_proposals", {"project": "opencat", "status": "open", "cursor": "opaque/+=", "limit": 5}, "GET", "/api/v1/proposals", "params", {"project": "opencat", "status": "open", "cursor": "opaque/+=", "limit": 5}),
    ("fleet_get_proposal", {"proposal_id": "P12"}, "GET", "/api/v1/proposals/P12", "params", {}),
    ("fleet_get_proposal", {"proposal_id": 12}, "GET", "/api/v1/proposals/12", "params", {}),
    ("fleet_create_proposal", {"author": "sol", "project": "opencat", "title": "v2", "summary": "Proxy hub", "weight": 5, "evidence": ["source.py"], "run_ref": "run", "step_ref": "S01", "pr_ref": "PR1", "client_request_id": "proposal-1"}, "POST", "/api/v1/proposals", "body", {"author": "sol", "project": "opencat", "title": "v2", "summary": "Proxy hub", "weight": 5, "evidence": ["source.py"], "run_ref": "run", "step_ref": "S01", "pr_ref": "PR1", "client_request_id": "proposal-1"}),
    ("fleet_update_proposal", {"proposal_id": "P12", "actor": "sol", "status": "rejected", "reason": "duplicate", "title": "new", "summary": "changed", "evidence": [], "weight": 4, "run_ref": "", "step_ref": "", "pr_ref": ""}, "PATCH", "/api/v1/proposals/P12", "body", {"actor": "sol", "status": "rejected", "reason": "duplicate", "title": "new", "summary": "changed", "evidence": [], "weight": 4, "run_ref": "", "step_ref": "", "pr_ref": ""}),
    ("fleet_update_proposal", {"proposal_id": 12, "actor": "sol", "status": "accepted"}, "PATCH", "/api/v1/proposals/12", "body", {"actor": "sol", "status": "accepted"}),
    ("fleet_get_evolve_state", {}, "GET", "/api/v1/evolve/state", "params", {}),
]
ALL_CASES = MAIL + FEED + KNOWLEDGE + PROPOSALS


async def _assert_wire(case):
    name, args, method, path, key, payload = case
    response = {"rows": [{"id": 7}], "cursor": "next", "has_more": True, "unread": 2}
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=response) as hub:
        result = await getattr(tools, name)(**args, _response_shape=_SHAPE)
    assert result is response
    hub.assert_awaited_once()
    assert hub.await_args.args == (method, path)
    assert hub.await_args.kwargs.get(key, {}) == payload
    assert hub.await_args.kwargs.get("params" if key == "body" else "body") is None
    assert hub.await_args.kwargs["tool_name"] == name
    assert hub.await_args.kwargs["shape"] == _SHAPE


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MAIL)
async def test_mail_wire_contract(case):
    await _assert_wire(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", FEED)
async def test_feed_wire_contract(case):
    await _assert_wire(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", KNOWLEDGE)
async def test_knowledge_wire_contract(case):
    await _assert_wire(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", PROPOSALS)
async def test_proposal_and_evolve_wire_contract(case):
    await _assert_wire(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES)
async def test_hub_errors_pass_through(case):
    name, args, *_ = case
    error = {"status": "error", "error": "conflict", "message": "hub decides", "http_status": 409, "details": {"id": 7}}
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=error):
        assert await getattr(tools, name)(**args) is error


@pytest.mark.asyncio
async def test_mail_defaults_and_optional_subject():
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={}) as hub:
        await tools.fleet_mail_send("sol", "luna", "hello")
        assert hub.await_args.kwargs["body"] == {"sender": "sol", "recipient": "luna", "body": "hello"}
        await tools.fleet_mail_list("sol")
        assert hub.await_args.kwargs["params"] == {"state": "unread", "since_id": 0, "limit": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["+1", "-1", "heart", "laugh", "eyes", "tada", "rocket", "thinking"])
async def test_reaction_kinds(kind):
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={}) as hub:
        await tools.fleet_feed_react("sol", 7, kind)
        assert hub.await_args.args == ("PUT", f"/api/v1/feed/posts/7/reactions/{kind}")
        await tools.fleet_feed_unreact("sol", 7, kind)
        assert hub.await_args.args == ("DELETE", f"/api/v1/feed/posts/7/reactions/{kind}")
        assert hub.await_args.kwargs["params"] == {"handle": "sol"}


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("fleet_mail_list", {"agent": "../feed"}),
    ("fleet_mail_counts", {"agent": "sol?state=all"}),
    ("fleet_feed_react", {"handle": "sol", "post_id": 7, "kind": "../repost"}),
    ("fleet_feed_unreact", {"handle": "sol", "post_id": 7, "kind": "invalid"}),
    ("fleet_get_proposal", {"proposal_id": "../evolve/state"}),
])
async def test_obvious_bad_path_inputs_fail_fast(name, args):
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock) as hub:
        result = await getattr(tools, name)(**args)
    assert result["error"] == "validation_error"
    hub.assert_not_awaited()


def test_v2_registration_manifest_and_tags():
    import json
    from pathlib import Path

    from core.context import mcp
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags

    found = {tool_name(tool): set(tool_tags(tool)) for tool in iter_tools(mcp)}
    manifest = json.loads((Path(__file__).parents[1] / "manifest.json").read_text())
    for name, _, method, *_ in ALL_CASES:
        assert name in manifest["capabilities"]
        assert found[name] == {"cat_fleet_chat_plugin", "read" if method == "GET" else "write"}


@pytest.mark.asyncio
async def test_unreact_reaches_fake_hub_with_query_handle(monkeypatch):
    """Exercise the real HTTP boundary, not just the tool's request_json call."""
    import httpx

    from plugins.cat_fleet_chat_plugin import hub_client

    monkeypatch.setenv("CAT_FLEET_HUB_TOKEN", "synthetic-test-token")
    seen = []

    def fake_hub(request):
        seen.append(request)
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/feed/posts/4/reactions/+1"
        assert dict(request.url.params) == {"handle": "sol"}
        assert request.headers["Authorization"] == "Bearer synthetic-test-token"
        return httpx.Response(200, json={"removed": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_hub)) as client:
        with (
            patch.object(hub_client, "open_client", new_callable=AsyncMock, return_value=client),
            patch.object(hub_client, "hub_url", return_value="https://hub.example.test"),
            patch.object(hub_client, "_is_safe_url", new_callable=AsyncMock, return_value=True),
        ):
            result = await tools.fleet_feed_unreact("sol", 4, "+1", _response_shape=_SHAPE)
    assert len(seen) == 1
    assert not (isinstance(result, dict) and result.get("status") == "error")


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("fleet_mail_send", {"sender": "sol", "recipient": "luna", "body": "hello"}),
    ("fleet_feed_reply", {"author": "sol", "parent_id": 4, "body": "hello"}),
    ("fleet_knowledge_set_status", {"actor": "sol", "fact_id": 9, "status": "active"}),
    ("fleet_update_proposal", {"proposal_id": "P12", "actor": "sol", "status": "built"}),
])
async def test_fake_hub_errors_keep_code_status_and_details(name, args, monkeypatch):
    import httpx

    from plugins.cat_fleet_chat_plugin import hub_client

    monkeypatch.setenv("CAT_FLEET_HUB_TOKEN", "synthetic-test-token")
    error = {"error": {"code": "invalid_transition", "message": "hub refused", "details": {"id": 9}}}
    transport = httpx.MockTransport(lambda request: httpx.Response(409, json=error))
    async with httpx.AsyncClient(transport=transport) as client:
        with (
            patch.object(hub_client, "open_client", new_callable=AsyncMock, return_value=client),
            patch.object(hub_client, "hub_url", return_value="https://hub.example.test"),
            patch.object(hub_client, "_is_safe_url", new_callable=AsyncMock, return_value=True),
        ):
            result = await getattr(tools, name)(**args)
    assert result == {
        "status": "error",
        "error": "invalid_transition",
        "message": "hub refused",
        "http_status": 409,
        "details": {"id": 9},
    }
