import os


def is_local_stdio() -> bool:
    """True unless the process explicitly serves a network transport (WHISKERS_TRANSPORT=http)."""
    return os.environ.get("WHISKERS_TRANSPORT", "stdio") != "http"
