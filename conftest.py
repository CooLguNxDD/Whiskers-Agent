# conftest.py (repo root) — module-level env isolation for test/ and plugins/*/tests/
import os
import sys

# Guard: several test fixtures do unfiltered `delete(ApiKey)` / `delete(User)`.
# If DATABASE_URL is already set to a non-test database (e.g. inside the dev
# container where compose exports the live DATABASE_URL), setdefault below is
# a no-op and those fixtures would silently wipe real octk API keys / users.
# Refuse to collect rather than risk that. scripts/run_tests.py sets DATABASE_URL
# to a "..._test"-suffixed DB before invoking pytest; set WHISKERS_TESTS_ALLOW_DB=1
# to bypass intentionally (e.g. already-isolated CI DB with a different name).
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url and not _db_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test") \
        and os.environ.get("WHISKERS_TESTS_ALLOW_DB") != "1":
    sys.exit(
        "REFUSING TO RUN TESTS: DATABASE_URL is set to a non-test database "
        f"({_db_url.rsplit('@', 1)[-1]!r}). Test fixtures delete rows unconditionally "
        "and will destroy real data. Use scripts/run_tests.py (which points at a "
        "'_test' DB), or set WHISKERS_TESTS_ALLOW_DB=1 to override."
    )

os.environ.setdefault("DATABASE_URL", "")        # disables _DB_AVAILABLE, vault, oauth init
os.environ.setdefault("OAUTH_ENABLED", "false")  # disables OAuthService construction
os.environ.setdefault("ENV", "dev")              # keeps cookies non-secure in tests
os.environ.setdefault("MASTER_KEY", "")
os.environ.setdefault("PLUGIN_API_URL", "https://test.api.example.com")
os.environ.setdefault("DEFAULT_PROJECT_ID", "1")
