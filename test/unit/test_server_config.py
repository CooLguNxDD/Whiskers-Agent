import pytest
from utils.server_config import _to_int

def test_to_int_valid_integer():
    assert _to_int(10, 0) == 10

def test_to_int_valid_string():
    assert _to_int("42", 0) == 42

def test_to_int_float_truncates():
    assert _to_int(3.14, 0) == 3

def test_to_int_invalid_string_returns_default():
    assert _to_int("not_an_int", 99) == 99

def test_to_int_none_returns_default():
    assert _to_int(None, 99) == 99

def test_to_int_unsupported_type_returns_default():
    assert _to_int([], 99) == 99
    assert _to_int({}, 99) == 99
