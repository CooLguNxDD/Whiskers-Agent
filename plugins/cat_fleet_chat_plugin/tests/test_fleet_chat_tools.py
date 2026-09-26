"""Unit tests for the fleet chat proxy. The hub itself is not started."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.cat_fleet_chat_plugin.MCPTools import fleet_chat_tools as tools
from plugins.cat_fleet_chat_plugin.hub_client import clamp_wait, hub_url


@pytest.fixture(autouse=True)
def allow_mock_hub_url():
    """Keep unit tests independent of Docker host DNS resolution."""
    with patch("plugins.cat_fleet_chat_plugin.hub_client._is_safe_url", new_callable=AsyncMock, return_value=True):
        yield


def test_hub_url_defaults_to_docker_host(monkeypatch):
    monkeypatch.delenv("CAT_FLEET_HUB_URL", raising=False)
    assert hub_url() == "http://host.docker.internal:8787"


def test_hub_url_uses_env(monkeypatch):
    monkeypatch.setenv("CAT_FLEET_HUB_URL", "http://127.0.0.1:8787/")
    assert hub_url() == "http://127.0.0.1:8787"


@pytest.mark.asyncio
async def test_omitted_shape_defaults_to_csv():
    from plugins.cat_fleet_chat_plugin.hub_client import request_json

    with (
        patch(
            "plugins.cat_fleet_chat_plugin.hub_client.open_client",
            new_callable=AsyncMock,
        ),
        patch(
            "plugins.cat_fleet_chat_plugin.hub_client.safe_api_call",
            new_callable=AsyncMock,
            return_value="id,name\n1,fleet",
        ) as mocked,
    ):
        result = await request_json("GET", "/api/v1/channels", tool_name="fleet_list_channels")
    assert result == "id,name\n1,fleet"
    assert mocked.await_args.kwargs["shape"]["response_format"] == "csv"

    with (
        patch(
            "plugins.cat_fleet_chat_plugin.hub_client.open_client",
            new_callable=AsyncMock,
        ),
        patch(
            "plugins.cat_fleet_chat_plugin.hub_client.safe_api_call",
            new_callable=AsyncMock,
            return_value={"data": []},
        ) as mocked,
    ):
        await request_json(
            "GET",
            "/api/v1/channels",
            tool_name="fleet_list_channels",
            shape={"response_format": "json", "limit": 5},
        )
    applied = mocked.await_args.kwargs["shape"]
    assert applied["response_format"] == "json"
    assert applied["limit"] == 5


def test_wait_is_clamped():
    assert clamp_wait(None) == 60
    assert clamp_wait(0) == 1
    assert clamp_wait(900) == 300


@pytest.mark.asyncio
async def test_post_maps_hub_error():
    payload = {
        "status": "error",
        "error": "api_error",
        "status_code": 409,
        "raw_body": '{"error":{"code":"channel_exists","message":"taken","details":{"name":"fleet"}}}',
    }
    with patch(
        "plugins.cat_fleet_chat_plugin.MCPTools.fleet_chat_tools.request_json",
        new_callable=AsyncMock,
        return_value={
            "status": "error",
            "error": "channel_exists",
            "message": "taken",
        },
    ) as mocked:
        result = await tools.fleet_create_channel("Fleet")
        assert result["error"] == "channel_exists"
        mocked.assert_awaited()
    # The mapper itself is what turns the hub envelope into the plugin shape.
    from plugins.cat_fleet_chat_plugin.hub_client import _map_error

    mapped = _map_error(payload)
    assert mapped["error"] == "channel_exists"
    assert mapped["details"]["name"] == "fleet"


@pytest.mark.asyncio
async def test_wait_sends_slack_and_channel():
    with patch(
        "plugins.cat_fleet_chat_plugin.MCPTools.fleet_chat_tools.request_json",
        new_callable=AsyncMock,
        return_value={"messages": [], "timed_out": True, "cursor": 0, "channel": "fleet"},
    ) as mocked:
        result = await tools.fleet_wait_for_mentions(
            "codex", since_id=4, channel="fleet", timeout_seconds=15
        )
        assert result["channel"] == "fleet"
        kwargs = mocked.await_args.kwargs
        assert kwargs["timeout"] == 25
        assert kwargs["params"]["timeout"] == 15
        assert kwargs["params"]["agent"] == "codex"
        assert kwargs["params"]["since_id"] == 4


def test_wait_tool_is_goap_denylisted():
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST

    assert (
        "cat_fleet_chat_plugin",
        "cat_fleet_chat_plugin__fleet_wait_for_mentions",
    ) in GOAP_CANDIDATE_DENYLIST


def test_tool_tags_split_read_write_and_wait():
    from core.context import mcp
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags

    from plugins.cat_fleet_chat_plugin import MCPTools  # noqa: F401

    found = {tool_name(tool): set(tool_tags(tool)) for tool in iter_tools(mcp)}
    wait_tags = found["fleet_wait_for_mentions"]
    read_tags = found["fleet_get_messages"]
    write_tags = found["fleet_post_message"]
    assert "wait" in wait_tags and "read" not in wait_tags and "write" not in wait_tags
    assert "read" in read_tags and "wait" not in read_tags
    assert "write" in write_tags and "wait" not in write_tags
    assert "cat_fleet_chat_plugin" in wait_tags


# --- archive, attachments, event waits ---------------------------------------

_TOOLS = "plugins.cat_fleet_chat_plugin.MCPTools.fleet_chat_tools"
_FILES = "plugins.cat_fleet_chat_plugin.attachments"


def _store():
    store = AsyncMock()
    store.put_bytes.return_value = "key"
    store.presigned_url.return_value = "http://minio/signed"
    store.get_bytes.return_value = b"hello fleet"
    store.remove_bytes.return_value = None
    return store


@pytest.mark.asyncio
async def test_attach_file_uploads_to_manifest_bucket_then_posts():
    from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS

    store = _store()
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={"message": {"id": 7}}) as posted,
    ):
        result = await tools.fleet_attach_file(
            "work", "codex", "../notes/report.md", content_text="hello fleet", text="@claude see"
        )
    assert result == {"message": {"id": 7}}
    bucket, key, data, media = store.put_bytes.await_args.args
    assert bucket == SETTINGS["attachment_bucket"] == "cat-fleet-attachments"
    assert key.startswith("fleet/work/") and key.endswith("/report.md")
    assert data == b"hello fleet"
    assert media == "text/markdown"
    body = posted.await_args.kwargs["body"]
    descriptor = body["attachments"][0]
    assert descriptor["bucket"] == bucket and descriptor["object_key"] == key
    assert descriptor["size_bytes"] == 11 and len(descriptor["sha256"]) == 64
    assert body["text"] == "@claude see"


@pytest.mark.asyncio
async def test_attach_file_reuses_stable_key_for_same_request_id():
    from plugins.cat_fleet_chat_plugin.attachments import stable_object_key

    store = _store()
    expected = stable_object_key("work", "report.md", b"hello fleet", "req-1")
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={"message": {"id": 7}}) as posted,
    ):
        first = await tools.fleet_attach_file(
            "work", "codex", "report.md", content_text="hello fleet", client_request_id="req-1"
        )
        second = await tools.fleet_attach_file(
            "work", "codex", "report.md", content_text="hello fleet", client_request_id="req-1"
        )
    assert first == second == {"message": {"id": 7}}
    keys = [call.args[1] for call in store.put_bytes.await_args_list]
    assert keys == [expected, expected]
    assert posted.await_count == 2
    store.remove_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_file_deletes_object_when_hub_post_fails():
    store = _store()
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(
            f"{_TOOLS}.request_json",
            new_callable=AsyncMock,
            return_value={"status": "error", "error": "api_error", "http_status": 500},
        ),
    ):
        result = await tools.fleet_attach_file("work", "codex", "a.txt", content_text="hello")
    assert result["status"] == "error"
    assert result["error"] == "api_error"
    bucket, key, _data, _media = store.put_bytes.await_args.args
    store.remove_bytes.assert_awaited_once_with(bucket, key)


@pytest.mark.asyncio
async def test_attach_file_deletes_object_on_payload_conflict():
    store = _store()
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(
            f"{_TOOLS}.request_json",
            new_callable=AsyncMock,
            return_value={"status": "error", "error": "payload_mismatch", "http_status": 409},
        ),
    ):
        result = await tools.fleet_attach_file(
            "work", "codex", "a.txt", content_text="hello", client_request_id="req-1"
        )
    assert result["http_status"] == 409
    bucket, key, _data, _media = store.put_bytes.await_args.args
    store.remove_bytes.assert_awaited_once_with(bucket, key)


@pytest.mark.asyncio
async def test_attach_file_keeps_object_on_idempotent_replay():
    store = _store()
    replay = {"message": {"id": 7, "client_request_id": "req-1"}}
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=replay),
    ):
        result = await tools.fleet_attach_file(
            "work", "codex", "a.txt", content_text="hello", client_request_id="req-1"
        )
    assert result == replay
    store.remove_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_file_rejects_bad_input_without_uploading(monkeypatch):
    from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS

    store = _store()
    monkeypatch.setitem(SETTINGS, "attachment_max_bytes", 4)
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock) as posted,
    ):
        too_big = await tools.fleet_attach_file("work", "codex", "a.txt", content_text="12345")
        both = await tools.fleet_attach_file("work", "codex", "a.txt", content_text="x", content_base64="eA==")
        bad_b64 = await tools.fleet_attach_file("work", "codex", "a.bin", content_base64="!!")
        bad_channel = await tools.fleet_attach_file("Work Room", "codex", "a.txt", content_text="x")
    assert too_big["error"] == "attachment_too_large"
    assert both["error"] == "validation_error"
    assert bad_b64["error"] == "validation_error"
    assert bad_channel["error"] == "validation_error"
    store.put_bytes.assert_not_awaited()
    posted.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_file_storage_failure_returns_tool_error():
    store = _store()
    store.put_bytes.side_effect = RuntimeError("minio down")
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock) as posted,
    ):
        result = await tools.fleet_attach_file("work", "codex", "a.txt", content_text="hello")
    assert result["status"] == "error"
    assert result["error"] == "storage_error"
    assert "minio down" not in result["message"]
    posted.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_attachment_storage_failure_returns_tool_error():
    store = _store()
    store.presigned_url.side_effect = RuntimeError("minio down")
    hub_row = {
        "attachment": {
            "id": 2,
            "storage": "minio",
            "bucket": "cat-fleet-attachments",
            "object_key": "fleet/work/abc/report.md",
            "content_type": "text/markdown",
            "size_bytes": 11,
        }
    }
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=hub_row),
    ):
        result = await tools.fleet_get_attachment(2)
    assert result["status"] == "error"
    assert result["error"] == "storage_error"
    assert "minio down" not in result["message"]


@pytest.mark.asyncio
async def test_get_attachment_refuses_foreign_bucket():
    store = _store()
    hub_row = {
        "attachment": {
            "id": 1,
            "storage": "minio",
            "bucket": "job-search-resumes",
            "object_key": "fleet/x/resume.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        }
    }
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=hub_row),
    ):
        result = await tools.fleet_get_attachment(1, include_content=True)
    assert result["error"] == "forbidden_attachment"
    store.presigned_url.assert_not_awaited()
    store.get_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_attachment_inlines_small_text():
    store = _store()
    hub_row = {
        "attachment": {
            "id": 2,
            "storage": "minio",
            "bucket": "cat-fleet-attachments",
            "object_key": "fleet/work/abc/report.md",
            "content_type": "text/markdown",
            "size_bytes": 11,
        }
    }
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=hub_row),
    ):
        plain = await tools.fleet_get_attachment(2)
        full = await tools.fleet_get_attachment(2, include_content=True)
    assert plain["attachment"]["presigned_url"] == "http://minio/signed"
    assert "content_text" not in plain["attachment"]
    assert full["attachment"]["content_text"] == "hello fleet"


@pytest.mark.asyncio
async def test_get_attachment_omits_forged_small_descriptor():
    from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS

    store = _store()
    store.get_bytes.return_value = b"x" * 12
    descriptor = {
        "storage": "minio", "bucket": "cat-fleet-attachments",
        "object_key": "fleet/work/abc/report.md", "content_type": "text/markdown",
        "size_bytes": 1,
    }
    with (
        patch.dict(SETTINGS, {"attachment_inline_max_bytes": 10}),
        patch(f"{_FILES}.get_artifact_store", return_value=store),
    ):
        from plugins.cat_fleet_chat_plugin.attachments import describe

        result = await describe(descriptor, include_content=True)
    assert "content_text" not in result
    assert "content_base64" not in result
    assert "content_omitted" in result


@pytest.mark.asyncio
async def test_upload_markdown_fallback_when_mimetypes_unknown():
    from plugins.cat_fleet_chat_plugin.attachments import upload

    store = _store()
    with (
        patch(f"{_FILES}.get_artifact_store", return_value=store),
        patch(f"{_FILES}.mimetypes.guess_type", return_value=(None, None)),
    ):
        result = await upload("work", "notes.md", b"hello")
    assert result["content_type"] == "text/markdown"


@pytest.mark.asyncio
async def test_hub_client_rejects_unsafe_url():
    from plugins.cat_fleet_chat_plugin.hub_client import request_json

    with (
        patch("plugins.cat_fleet_chat_plugin.hub_client._is_safe_url", new_callable=AsyncMock, return_value=False),
        patch("plugins.cat_fleet_chat_plugin.hub_client.safe_api_call", new_callable=AsyncMock) as called,
    ):
        result = await request_json("GET", "/api/v1/channels")
    assert result["error"] == "unsafe_url"
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_message_refuses_foreign_descriptor():
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock) as posted:
        result = await tools.fleet_post_message(
            "work",
            "codex",
            "x",
            attachments=[{"storage": "minio", "bucket": "other", "object_key": "fleet/a"}],
        )
    assert result["error"] == "forbidden_attachment"
    posted.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_and_list_archived_pass_through():
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={}) as mocked:
        await tools.fleet_archive_channel("work", "codex", note="shipped", force=True)
        kwargs = mocked.await_args.kwargs
        assert mocked.await_args.args == ("POST", "/api/v1/channels/work/archive")
        assert kwargs["body"] == {"actor": "codex", "note": "shipped", "force": True}
        await tools.fleet_list_channels(include_archived=True)
        assert mocked.await_args.kwargs["params"]["include_archived"] == 1
        bad = await tools.fleet_archive_channel("../x", "codex")
        assert bad["error"] == "validation_error"


@pytest.mark.asyncio
async def test_wait_for_events_params_and_slack():
    with patch(
        f"{_TOOLS}.request_json",
        new_callable=AsyncMock,
        return_value={"events": [], "cursor": 9, "timed_out": True},
    ) as mocked:
        await tools.fleet_wait_for_events(
            "codex", after_event_id=4, kinds=["message.created", "task.updated"], timeout_seconds=20
        )
    kwargs = mocked.await_args.kwargs
    assert mocked.await_args.args == ("GET", "/api/v1/notifications")
    assert kwargs["timeout"] == 30
    assert kwargs["params"]["kinds"] == "message.created,task.updated"
    assert kwargs["params"]["after_event_id"] == 4
    assert kwargs["params"]["agent"] == "codex"


def test_event_wait_is_goap_denylisted_and_tagged_wait():
    from core.context import mcp
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST

    assert (
        "cat_fleet_chat_plugin",
        "cat_fleet_chat_plugin__fleet_wait_for_events",
    ) in GOAP_CANDIDATE_DENYLIST
    found = {tool_name(tool): set(tool_tags(tool)) for tool in iter_tools(mcp)}
    assert "wait" in found["fleet_wait_for_events"]
    assert "write" in found["fleet_attach_file"] and "wait" not in found["fleet_attach_file"]
    assert "write" in found["fleet_archive_channel"]
    assert "read" in found["fleet_get_attachment"]


@pytest.mark.asyncio
async def test_get_attachment_reads_raw_hub_json():
    """Shaping renames ``attachment`` to ``attachment_*``; the plugin must read the raw body."""
    from plugins.cat_fleet_chat_plugin.hub_client import request_json

    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={"status": "error", "error": "not_found"}) as mocked:
        await tools.fleet_get_attachment(3)
    assert mocked.await_args.kwargs["raw"] is True

    with (
        patch("plugins.cat_fleet_chat_plugin.hub_client.open_client", new_callable=AsyncMock),
        patch(
            "plugins.cat_fleet_chat_plugin.hub_client.safe_api_call",
            new_callable=AsyncMock,
            return_value={"attachment": {"id": 3}},
        ) as called,
    ):
        result = await request_json("GET", "/api/v1/attachments/3", raw=True)
    assert result == {"attachment": {"id": 3}}
    assert called.await_args.kwargs["raw_response"] is True


@pytest.mark.asyncio
async def test_set_channel_state_and_filter():
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={}) as mocked:
        await tools.fleet_set_channel_state("work", "blocked", "codex", note="waiting on review")
        assert mocked.await_args.args == ("POST", "/api/v1/channels/work/state")
        assert mocked.await_args.kwargs["body"] == {
            "state": "blocked",
            "actor": "codex",
            "note": "waiting on review",
            "force": False,
        }
        await tools.fleet_list_channels(state=["blocked", "review"])
        assert mocked.await_args.kwargs["params"]["state"] == "blocked,review"
        await tools.fleet_unarchive_channel("work", "codex", state="review")
        assert mocked.await_args.kwargs["body"] == {"actor": "codex", "state": "review"}
        calls = mocked.await_count
        bad = await tools.fleet_set_channel_state("work", "sleeping", "codex")
        assert bad["error"] == "validation_error"
        assert mocked.await_count == calls


def test_plugin_states_match_hub_vocabulary():
    """The plugin's fail-fast list must match the hub's enum."""
    assert tools.CHANNEL_STATES == ("active", "paused", "blocked", "review", "done", "archived")


def test_set_channel_state_is_a_write_tool():
    from core.context import mcp
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags

    found = {tool_name(tool): set(tool_tags(tool)) for tool in iter_tools(mcp)}
    assert "write" in found["fleet_set_channel_state"]
    assert "wait" not in found["fleet_set_channel_state"]
