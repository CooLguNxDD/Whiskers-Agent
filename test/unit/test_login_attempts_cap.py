import time
import pytest
import api.admin_routes as ar
from api.admin_routes import _RATE_LIMIT_MAX_IPS, _prune_login_attempts


def setup_function():
    ar._login_attempts.clear()
    ar._last_rate_limit_prune = 0.0


def test_login_attempts_capped_under_large_ip_attack():
    for i in range(_RATE_LIMIT_MAX_IPS + 500):
        ar._login_attempts[f"10.{(i >> 16) & 0xFF}.{(i >> 8) & 0xFF}.{i & 0xFF}"] = [time.monotonic()]

    _prune_login_attempts(time.monotonic())
    assert len(ar._login_attempts) <= _RATE_LIMIT_MAX_IPS


def test_login_attempts_small_count_unaffected():
    for i in range(100):
        ar._login_attempts[f"192.168.1.{i}"] = [time.monotonic()]

    _prune_login_attempts(time.monotonic())
    assert len(ar._login_attempts) == 100
