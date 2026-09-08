"""Unit tests for the CORS policy resolver (utils/server_config.resolve_cors_policy).

Relaxed mode reflects any Origin with credentials, so the security-relevant
property under test is that it can *only* be reached by an explicit opt-in.
"""

from utils.server_config import CORS_DEFAULT_ORIGINS, resolve_cors_policy


def test_default_env_is_strict_localhost():
    """No CORS env at all → strict mode on the built-in localhost allowlist."""
    policy = resolve_cors_policy({})
    assert policy.relaxed is False
    assert policy.origins == CORS_DEFAULT_ORIGINS.split(",")
    assert policy.warning == ""


def test_explicit_opt_in_enables_relaxed():
    """MCP_CORS_RELAXED is the only way into origin-reflection mode."""
    assert resolve_cors_policy({"MCP_CORS_RELAXED": "1"}).relaxed is True
    assert resolve_cors_policy({"MCP_CORS_RELAXED": "true"}).relaxed is True
    assert resolve_cors_policy({"MCP_CORS_RELAXED": "on"}).relaxed is True


def test_relaxed_is_off_for_falsy_values():
    """Anything not in the truthy set leaves the strict path."""
    for value in ("", "0", "false", "no", "off", "maybe"):
        assert resolve_cors_policy({"MCP_CORS_RELAXED": value}).relaxed is False


def test_wildcard_origins_do_not_imply_relaxed():
    """Regression: MCP_CORS_ORIGINS=* must NOT silently enable credentialed reflection.

    The dev compose override used to ship this wildcard, which turned relaxed
    mode on for every developer without anyone opting in.
    """
    policy = resolve_cors_policy({"MCP_CORS_ORIGINS": "*"})
    assert policy.relaxed is False
    assert policy.origins == []
    assert "MCP_CORS_RELAXED" in policy.warning


def test_wildcard_with_opt_in_is_relaxed_and_silent():
    """Wildcard + explicit opt-in is a coherent dev config, so no warning."""
    policy = resolve_cors_policy({"MCP_CORS_ORIGINS": "all", "MCP_CORS_RELAXED": "1"})
    assert policy.relaxed is True
    assert policy.origins == []
    assert policy.warning == ""


def test_origins_are_split_and_trimmed():
    """Comma-separated allowlists are trimmed; blanks and wildcards are dropped."""
    policy = resolve_cors_policy(
        {"MCP_CORS_ORIGINS": " https://a.example , ,https://b.example, * "}
    )
    assert policy.origins == ["https://a.example", "https://b.example"]
    # A wildcard mixed into a real allowlist is not an opt-in either.
    assert policy.relaxed is False
