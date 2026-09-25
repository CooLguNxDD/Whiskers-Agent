"""OAuth selection from credentials and explicit override."""

import importlib


def test_oauth_auto_selection(monkeypatch):
    import core.context._env as env

    with monkeypatch.context() as context:
        context.delenv("OAUTH_ENABLED", raising=False)
        context.setenv("USERNAME", "user")
        context.setenv("PASSWORD", "password")
        assert importlib.reload(env).OAUTH_ENABLED is False
        context.delenv("PASSWORD")
        assert importlib.reload(env).OAUTH_ENABLED is True
        context.setenv("OAUTH_ENABLED", "false")
        assert importlib.reload(env).OAUTH_ENABLED is False
        context.setenv("OAUTH_ENABLED", "true")
        assert importlib.reload(env).OAUTH_ENABLED is True
    importlib.reload(env)
