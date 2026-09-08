"""Catalog execute plane — resolve (plugin_id, operation_id), authorize, validate, invoke."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from core.route_registry.operation_catalog import get_operation_catalog
from core.route_registry.operation_descriptor import Visibility

logger = logging.getLogger("whiskers")

_SHAPE_ARG_KEY = "_response_shape"


class ExecuteError(Exception):
    """Typed failure for catalog execute HTTP mapping."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def _validate_args(schema: dict | None, args: dict) -> None:
    """Validate args against JSON Schema; raise ExecuteError on failure."""
    if not schema:
        return
    try:
        import jsonschema
        from jsonschema import Draft7Validator
    except ImportError as exc:
        raise ExecuteError(
            "validator_unavailable",
            "jsonschema is not installed",
            status=500,
        ) from exc

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
    if errors:
        details = [
            {
                "path": list(e.absolute_path),
                "message": e.message,
            }
            for e in errors[:20]
        ]
        raise ExecuteError(
            "schema_validation_failed",
            "Input failed JSON Schema validation",
            status=422,
            details=details,
        )


def _authorize(op, caller_scopes: list[str] | None) -> None:
    """Raise ExecuteError if caller cannot execute this operation.

    Builds a real ``AccessRequest`` (plugin_id/operation_id/tags/access) so
    the level-3 plugin-gate ceiling rule fires here — the compat
    ``is_allowed(scopes, required)`` shortcut builds no request and the gate
    rule no-ops on ``request is None``, which let a gated operation execute
    through this fast path even while denied by its plugin's gate.
    """
    from core.api_key_management.scopes import required_scopes_for_route
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest

    if op.required_scopes:
        required = set(op.required_scopes)
    else:
        required = required_scopes_for_route(op.plugin_id, op.tags)

    request = AccessRequest(
        plugin_id=op.plugin_id,
        operation_id=op.operation_id,
        tags=tuple(op.tags or ()),
        access=op.access,
        path="catalog_execute",
    )
    grant = ScopeGrant(
        scopes=list(caller_scopes) if caller_scopes is not None else None,
        kind=PrincipalKind.LOCAL_CLI if caller_scopes is None else PrincipalKind.API_KEY,
    )
    decision = evaluate_access(grant, required=required, request=request)
    if not decision.allowed:
        raise ExecuteError(
            "forbidden",
            "Caller lacks required scopes for this operation",
            status=403,
            details={"required_scopes": sorted(required), "reason": decision.reason},
        )


async def _maybe_shape_result(
    result: Any, operation_id: str, args: dict, shape_override: dict[str, Any] | None
) -> Any:
    """Run the caller's ``_response_shape`` override through the shared shaping choke point.

    Mirrors ``core.context.response_shape_middleware.ResponseShapeMiddleware`` (the MCP
    direct-call path) so a catalog-execute caller and an MCP caller resolve
    ``response_shapes``/``endpoint_meta`` identically. Gated on the same live
    ``graph.direct_call_shaping`` kill switch and ``graph.direct_call_shaping_exclude``
    list so an operator can disable both paths (globally or per op) at once; fails open
    (returns the raw result) on any shaping error rather than breaking the call.
    """
    try:
        from core.context.response_shape_middleware import _direct_call_shaping_config

        enabled, excluded = _direct_call_shaping_config()
        if not enabled or operation_id in excluded:
            return result
    except Exception:
        logger.debug("execute_operation: shaping kill-switch check failed; shaping anyway", exc_info=True)

    try:
        from utils.api_utils import apply_response_shape

        return await apply_response_shape(
            result,
            operation_id=operation_id,
            tool_name=operation_id,
            request_params=args,
            shape=shape_override,
        )
    except Exception:
        logger.warning("execute_operation: response shaping failed for %r; returning raw result", operation_id, exc_info=True)
        return result


async def execute_operation(
    plugin_id: str,
    operation_id: str,
    args: dict,
    *,
    caller_scopes: list[str] | None,
    instance_id: str = "default",
) -> Any:
    """Resolve, authorize, schema-validate, and invoke a fast-path operation.

    Identity is strictly (plugin_id, operation_id). Frontend validation is not trusted.
    """
    if not plugin_id or not operation_id:
        raise ExecuteError(
            "identity_required",
            "plugin_id and operation_id are required",
            status=400,
        )

    catalog = get_operation_catalog()
    op = catalog.get(plugin_id, operation_id)
    if op is None:
        # Fallback: live RouteRegistry may have the callable if catalog lag
        from core.context import route_registry
        route = route_registry.get(operation_id, plugin_id)
        if route is None:
            raise ExecuteError(
                "not_found",
                f"Unknown operation ({plugin_id!r}, {operation_id!r})",
                status=404,
            )
        op = route.to_operation()

    _authorize(op, caller_scopes)

    # Pop the response-shaping override before schema validation — it is never a real
    # operation parameter and would fail jsonschema on any op whose input_schema sets
    # additionalProperties: false. Skip the pop when the op's own input_schema already
    # declares the key (mirrors OperationDescriptor._catalog_input_schema): shape_override
    # stays None so _maybe_shape_result is never reached and the op shapes its own output.
    # v1: only ever shapes when the caller explicitly passed it (shape_override=None is a
    # pure no-op below), so an existing GOAP/FlowSpec caller that never passes
    # _response_shape sees a byte-identical raw result — see
    # core/context/response_shape_middleware.py for the equivalent MCP direct-call path
    # this mirrors. Copies (never mutates) the caller's args dict.
    properties = (op.input_schema or {}).get("properties")
    declares_shape = isinstance(properties, dict) and _SHAPE_ARG_KEY in properties

    shape_override: dict[str, Any] | None = None
    if isinstance(args, dict) and _SHAPE_ARG_KEY in args and not declares_shape:
        args = dict(args)
        shape_override = args.pop(_SHAPE_ARG_KEY)

    _validate_args(op.input_schema, args)

    # Prefer binding override then descriptor callable
    from core.context import route_registry
    fn = route_registry.fast_path_callable(
        operation_id, plugin_id=plugin_id, instance_id=instance_id,
    )
    if fn is None:
        fn = op.callable_ref
    if fn is None or not op.is_fast_path:
        raise ExecuteError(
            "not_executable",
            "Operation is not a fast-path callable (HTTP proxy execute not in v1)",
            status=501,
        )

    try:
        import functools

        check_fn = fn
        while isinstance(check_fn, functools.partial):
            check_fn = check_fn.func
        if hasattr(check_fn, "__call__") and not inspect.isfunction(check_fn):
            check_fn = check_fn.__call__

        if inspect.iscoroutinefunction(check_fn):
            result = await fn(**args)
        else:
            # Sync / hybrid callables: run off the event loop when needed
            result = await asyncio.to_thread(fn, **args)
            if inspect.isawaitable(result):
                result = await result

        if shape_override is not None:
            result = await _maybe_shape_result(result, operation_id, args, shape_override)
        return result
    except TypeError as exc:
        raise ExecuteError(
            "invoke_type_error",
            str(exc),
            status=422,
        ) from exc
    except ExecuteError:
        raise
    except Exception as exc:
        logger.exception("execute_operation failed %s/%s", plugin_id, operation_id)
        raise ExecuteError(
            "invoke_failed",
            str(exc),
            status=500,
        ) from exc
