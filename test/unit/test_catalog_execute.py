"""Unit tests for catalog execute plane."""

from __future__ import annotations

import pytest

from core.route_registry.execute import execute_operation, ExecuteError
from core.route_registry.operation_catalog import (
    get_operation_catalog,
    _reset_operation_catalog_for_tests,
)
from core.route_registry.operation_descriptor import AccessClass, OperationDescriptor
from core.route_registry import RouteRegistry
from core.route_registry.route_descriptor import RouteDescriptor


@pytest.fixture(autouse=True)
def _reset():
    _reset_operation_catalog_for_tests()
    yield
    _reset_operation_catalog_for_tests()


def _publish_fn(fn, *, schema=None, scopes=(), tags=("p1", "read")):
    reg = RouteRegistry()
    # Register into both route registry (callable) and catalog via contribute
    rd = RouteDescriptor(
        plugin_id="p1",
        operation_id="p1__echo",
        description="echo",
        parameters=schema or {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        method="CALL",
        is_fast_path=True,
        callable_ref=fn,
        tags=tags,
        required_scopes=tuple(scopes) if scopes else (),
    )
    reg.contribute([rd])
    return reg


@pytest.mark.asyncio
async def test_execute_success() -> None:
    def echo(msg: str):
        return {"echo": msg}

    _publish_fn(echo)
    result = await execute_operation(
        "p1", "p1__echo", {"msg": "hi"}, caller_scopes=["admin"],
    )
    assert result == {"echo": "hi"}


@pytest.mark.asyncio
async def test_execute_requires_plugin_id() -> None:
    with pytest.raises(ExecuteError) as ei:
        await execute_operation("", "op", {}, caller_scopes=["admin"])
    assert ei.value.status == 400


@pytest.mark.asyncio
async def test_execute_schema_validation() -> None:
    def echo(msg: str):
        return msg

    _publish_fn(echo)
    with pytest.raises(ExecuteError) as ei:
        await execute_operation("p1", "p1__echo", {}, caller_scopes=["admin"])
    assert ei.value.status == 422
    assert ei.value.code == "schema_validation_failed"


@pytest.mark.asyncio
async def test_execute_not_found() -> None:
    with pytest.raises(ExecuteError) as ei:
        await execute_operation("p1", "missing", {}, caller_scopes=["admin"])
    assert ei.value.status == 404


@pytest.mark.asyncio
async def test_execute_scope_deny() -> None:
    def echo(msg: str):
        return msg

    _publish_fn(echo, scopes=("plugin:p1",), tags=("p1",))
    with pytest.raises(ExecuteError) as ei:
        await execute_operation("p1", "p1__echo", {"msg": "x"}, caller_scopes=[])
    assert ei.value.status == 403


@pytest.mark.asyncio
async def test_execute_async_function() -> None:
    async def echo(msg: str):
        return {"echo": msg}

    _publish_fn(echo)
    result = await execute_operation(
        "p1", "p1__echo", {"msg": "async"}, caller_scopes=["admin"],
    )
    assert result == {"echo": "async"}


@pytest.mark.asyncio
async def test_execute_partial_async() -> None:
    import functools

    async def echo(msg: str, prefix: str = ""):
        return {"echo": f"{prefix}{msg}"}

    _publish_fn(functools.partial(echo, prefix="p:"))
    result = await execute_operation(
        "p1", "p1__echo", {"msg": "hi"}, caller_scopes=["admin"],
    )
    assert result == {"echo": "p:hi"}


@pytest.mark.asyncio
async def test_execute_without_response_shape_is_byte_identical_raw_result() -> None:
    """No _response_shape in args -> untouched raw result (v1 contract: shaping is opt-in
    at the catalog boundary so existing GOAP/FlowSpec deterministic-stage callers, which
    never pass _response_shape, see zero behavior change)."""
    def echo(msg: str):
        return {"echo": msg, "queueId": "should-survive-unshaped"}

    _publish_fn(echo)
    result = await execute_operation("p1", "p1__echo", {"msg": "hi"}, caller_scopes=["admin"])
    assert result == {"echo": "hi", "queueId": "should-survive-unshaped"}


@pytest.mark.asyncio
async def test_execute_response_shape_popped_before_schema_validation() -> None:
    """_response_shape must never reach jsonschema validation, even under
    additionalProperties: false — it is not a real operation parameter."""
    def echo(msg: str):
        return {"echo": msg}

    strict_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
        "additionalProperties": False,
    }
    _publish_fn(echo, schema=strict_schema)
    # normalize/include_meta explicitly off: isolates "did shaping run at all without
    # jsonschema rejecting _response_shape" from the pipeline's own dict->list normalize
    # and include_meta-envelope defaults, which are covered by utils/response_shape.py's
    # own unit tests, not this one.
    result = await execute_operation(
        "p1", "p1__echo",
        {"msg": "hi", "_response_shape": {"response_format": "json", "normalize": False, "include_meta": False}},
        caller_scopes=["admin"],
    )
    assert result == {"echo": "hi"}


@pytest.mark.asyncio
async def test_execute_response_shape_applied_to_result() -> None:
    """A caller-supplied shape is threaded through the shared shaping choke point."""
    def get_record(msg: str):
        return {"echo": msg, "queueId": "internal-noise", "secret": "trimmed"}

    _publish_fn(get_record)
    result = await execute_operation(
        "p1", "p1__echo",
        {
            "msg": "hi",
            "_response_shape": {
                "strip_keys": ["queueId", "secret"],
                "response_format": "json",
                "normalize": False,
                "include_meta": False,
            },
        },
        caller_scopes=["admin"],
    )
    assert result == {"echo": "hi"}


@pytest.mark.asyncio
async def test_execute_response_shape_does_not_mutate_caller_args() -> None:
    def echo(msg: str):
        return {"echo": msg}

    _publish_fn(echo)
    caller_args = {"msg": "hi", "_response_shape": {"response_format": "json"}}
    await execute_operation("p1", "p1__echo", caller_args, caller_scopes=["admin"])
    assert "_response_shape" in caller_args  # execute_operation copied, never mutated the input dict


@pytest.mark.asyncio
async def test_execute_response_shape_kill_switch_disables_shaping(monkeypatch) -> None:
    class _Cfg:
        server = {"graph": {"direct_call_shaping": False, "direct_call_shaping_exclude": []}}

    monkeypatch.setattr("utils.config_registry.get_config_registry", lambda: _Cfg())

    def get_record(msg: str):
        return {"echo": msg, "queueId": "internal-noise"}

    _publish_fn(get_record)
    result = await execute_operation(
        "p1", "p1__echo",
        {"msg": "hi", "_response_shape": {"strip_keys": ["queueId"]}},
        caller_scopes=["admin"],
    )
    # kill switch on -> shape never applied, raw result returned untouched
    assert result == {"echo": "hi", "queueId": "internal-noise"}


@pytest.mark.asyncio
async def test_execute_response_shape_respects_exclusion_list(monkeypatch) -> None:
    class _Cfg:
        server = {
            "graph": {
                "direct_call_shaping": True,
                "direct_call_shaping_exclude": ["p1__echo"],
            }
        }

    monkeypatch.setattr("utils.config_registry.get_config_registry", lambda: _Cfg())

    def get_record(msg: str):
        return {"echo": msg, "queueId": "internal-noise"}

    _publish_fn(get_record)
    result = await execute_operation(
        "p1", "p1__echo",
        {"msg": "hi", "_response_shape": {"strip_keys": ["queueId"]}},
        caller_scopes=["admin"],
    )
    assert result == {"echo": "hi", "queueId": "internal-noise"}


@pytest.mark.asyncio
async def test_execute_native_response_shape_is_not_intercepted() -> None:
    """An op that declares `_response_shape` in its schema must receive the key
    and must not be externally re-shaped after it returns."""
    received: dict = {}

    def echo(msg: str, _response_shape=None):
        received["shape"] = _response_shape
        return {"echo": msg, "queueId": "internal-noise"}

    schema = {
        "type": "object",
        "properties": {
            "msg": {"type": "string"},
            "_response_shape": {"type": "object"},
        },
        "required": ["msg"],
    }
    _publish_fn(echo, schema=schema)
    caller_shape = {"strip_keys": ["queueId"], "response_format": "json"}
    result = await execute_operation(
        "p1", "p1__echo",
        {"msg": "hi", "_response_shape": caller_shape},
        caller_scopes=["admin"],
    )
    assert received["shape"] == caller_shape
    assert result == {"echo": "hi", "queueId": "internal-noise"}


@pytest.mark.asyncio
async def test_execute_not_executable_host_mirrored_op() -> None:
    """Host-mirrored console routes publish with is_fast_path=False,
    callable_ref=None (core/route_registry/host_catalog.py) — execute must
    reject them as 501 not_executable rather than attempting to call None.
    """
    from core.route_registry.operation_descriptor import Visibility

    op = OperationDescriptor(
        plugin_id="api.plugins",
        operation_id="api_list_plugins",
        description="host route",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        access=AccessClass.READ,
        required_scopes=(),
        visibility=Visibility.AUTHENTICATED,
        version="1",
        tags=("plugins", "host"),
        http=None,
        mcp=None,
        ui=None,
        is_fast_path=False,
        callable_ref=None,
    )
    get_operation_catalog().publish_owner("api.plugins", [op])

    with pytest.raises(ExecuteError) as ei:
        await execute_operation(
            "api.plugins", "api_list_plugins", {}, caller_scopes=["admin"],
        )
    assert ei.value.status == 501
    assert ei.value.code == "not_executable"


@pytest.mark.asyncio
async def test_execute_async_callable_object() -> None:
    class AsyncEcho:
        async def __call__(self, msg: str):
            return {"echo": msg}

    _publish_fn(AsyncEcho())
    result = await execute_operation(
        "p1", "p1__echo", {"msg": "obj"}, caller_scopes=["admin"],
    )
    assert result == {"echo": "obj"}
