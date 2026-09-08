"""Unit tests for TerminalTokenService."""

from plugins.cat_terminal_relay_plugin.services.terminal_token_service import (
    TerminalTokenService,
)


def test_issue_and_consume_once():
    svc = TerminalTokenService(ttl_seconds=300)
    token, ttl = svc.issue("alice")
    assert ttl == 300
    assert svc.consume(token) == "alice"
    # Single-use: a second consume fails.
    assert svc.consume(token) is None



def test_one_token_per_subject_revokes_prior():
    svc = TerminalTokenService()
    first, _ = svc.issue("bob")
    second, _ = svc.issue("bob")
    assert first != second
    # The first token is revoked when the second is issued.
    assert svc.consume(first) is None
    assert svc.consume(second) == "bob"


def test_expired_token_rejected():
    svc = TerminalTokenService(ttl_seconds=0)
    token, _ = svc.issue("carol")
    assert svc.peek(token) is None
    assert svc.consume(token) is None


def test_peek_does_not_consume():
    svc = TerminalTokenService()
    token, _ = svc.issue("dave")
    assert svc.peek(token) == "dave"
    assert svc.consume(token) == "dave"


def test_unknown_token():
    svc = TerminalTokenService()
    assert svc.consume("nope") is None
    assert svc.peek("nope") is None
