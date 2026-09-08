"""Unit tests for step-up configuration helpers in server_config."""

from utils.server_config import _clamp_int, _validate_step_up_method


def test_clamp_int():
    """Verify that _clamp_int properly coerces, handles defaults, and bounds inputs."""
    # a value of 30 clamps to 60
    assert _clamp_int(30, default=300, lo=60, hi=900) == 60
    # 5000 clamps to 900
    assert _clamp_int(5000, default=300, lo=60, hi=900) == 900
    # 300 stays 300
    assert _clamp_int(300, default=300, lo=60, hi=900) == 300
    # non-numeric/None falls back to default then clamps
    assert _clamp_int("not-a-number", default=300, lo=60, hi=900) == 300
    assert _clamp_int(None, default=300, lo=60, hi=900) == 300
    # default itself respected and clamped if out of range
    assert _clamp_int("invalid", default=10, lo=60, hi=900) == 60
    assert _clamp_int("invalid", default=1000, lo=60, hi=900) == 900


def test_validate_step_up_method():
    """Verify that _validate_step_up_method validates acceptable methods and defaults to totp."""
    # "totp"/"password"/"both" pass through
    assert _validate_step_up_method("totp") == "totp"
    assert _validate_step_up_method("password") == "password"
    assert _validate_step_up_method("both") == "both"
    # "bogus"/None/"" -> "totp"
    assert _validate_step_up_method("bogus") == "totp"
    assert _validate_step_up_method(None) == "totp"
    assert _validate_step_up_method("") == "totp"
