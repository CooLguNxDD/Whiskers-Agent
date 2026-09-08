"""Unit tests for DCR scope leniency (sanitize_register_scope)."""

import json

from api.middleware import sanitize_register_scope

VALID = ["whiskers", "terminal:use", "terminal:host"]


def _scope(body: bytes):
    return json.loads(body).get("scope")


def test_drops_unknown_scopes_keeps_valid():
    body = json.dumps({"redirect_uris": ["x"], "scope": "openid whiskers profile"}).encode()
    out = sanitize_register_scope(body, VALID)
    assert _scope(out) == "whiskers"


def test_all_invalid_scopes_field_removed():
    body = json.dumps({"redirect_uris": ["x"], "scope": "openid profile"}).encode()
    out = sanitize_register_scope(body, VALID)
    assert "scope" not in json.loads(out)


def test_all_valid_body_unchanged():
    body = json.dumps({"redirect_uris": ["x"], "scope": "whiskers terminal:use"}).encode()
    assert sanitize_register_scope(body, VALID) == body


def test_no_scope_field_unchanged():
    body = json.dumps({"redirect_uris": ["x"]}).encode()
    assert sanitize_register_scope(body, VALID) == body


def test_none_valid_scopes_unchanged():
    body = json.dumps({"redirect_uris": ["x"], "scope": "openid"}).encode()
    assert sanitize_register_scope(body, None) == body


def test_non_json_body_unchanged():
    body = b"not-json"
    assert sanitize_register_scope(body, VALID) == body
