"""Path-parameter extract/substitute for dynamic tools (colon verbs + prefix tokens)."""
from pathlib import Path

from core.dynamic_tools.loader import _extract_path_params, _substitute_path


def test_openapi_brace_param_substituted_and_popped():
    kw = {"userId": "u1", "q": "keep"}
    url = _substitute_path("/v1/users/{userId}", kw)
    assert url == "/v1/users/u1"
    assert "userId" not in kw
    assert kw["q"] == "keep"


def test_express_prefix_param_does_not_clobber_longer_name():
    kw = {"id": "123", "identifier": "abc"}
    url = _substitute_path("/users/:id/action/:identifier", kw)
    assert url == "/users/123/action/abc"
    assert kw == {}


def test_colon_verb_is_not_a_path_param():
    path = "/v1alpha/sessions/{sessionId}:sendMessage"
    assert _extract_path_params(path) == frozenset({"sessionId"})
    kw = {"sessionId": "s1"}
    assert _substitute_path(path, kw) == "/v1alpha/sessions/s1:sendMessage"
    assert kw == {}


def test_leading_colon_param_without_slash():
    kw = {"id": "x"}
    assert _substitute_path(":id/items", kw) == "x/items"


def test_missing_param_left_unresolved():
    path = "/v1/users/{userId}"
    kw = {}
    assert _substitute_path(path, kw) == path
    unresolved = _extract_path_params(path)
    assert unresolved == frozenset({"userId"})


def test_generator_emits_span_safe_sub_not_str_replace():
    src = Path("Tools/tools_generator.py").read_text(encoding="utf-8")
    assert "_PATH_PARAM_RE.sub(_path_repl, _path)" in src
    assert "_path.replace(_token" not in src
