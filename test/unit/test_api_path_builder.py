"""Unit tests for the API path builder and parser utility functions."""

from core.route_registry import AuthPolicy, build_api_path, parse_api_gate


def test_build_api_path_cases():
    assert (
        build_api_path("admin", AuthPolicy.PUBLIC, "login")
        == "/api/admin/public/login"
    )
    assert (
        build_api_path("plugins", AuthPolicy.SESSION_GATED, "{id}/tools")
        == "/api/plugins/session_gated/{id}/tools"
    )
    assert (
        build_api_path("health", AuthPolicy.SESSION_GATED, "")
        == "/api/health/session_gated"
    )
    assert (
        build_api_path("terminal", AuthPolicy.NONE, "ws/{session_id}")
        == "/api/terminal/none/ws/{session_id}"
    )


def test_parse_api_gate_cases():
    assert parse_api_gate("/api/admin/public/login") == "public"
    assert parse_api_gate("/api/plugins/session_gated/{id}/tools") == "session_gated"
    assert parse_api_gate("/api/health/scope_required") == "scope_required"
    assert parse_api_gate("/api/terminal/none/ws/{session_id}") == "none"

    # Edge cases
    assert parse_api_gate("/not-api/foo/public") is None
    assert parse_api_gate("/api/foo") is None
    assert parse_api_gate("/api/foo/invalid_gate/endpoint") is None
    assert parse_api_gate("/") is None
    assert parse_api_gate("") is None
