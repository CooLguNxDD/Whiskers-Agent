import pytest
from utils.json_processor import extract_lists, flatten_dict, extract_by_keys, resolve_array_string

def test_extract_lists_finds_nested_list():
    data = {"a": 1, "b": {"c": [{"id": 1}, {"id": 2}]}}
    lists = extract_lists(data)
    assert len(lists) == 1
    assert lists[0] == [{"id": 1}, {"id": 2}]

def test_extract_lists_returns_largest_first():
    data = {
        "a": [{"id": 1}, {"id": 2}],
        "b": [{"id": 1}, {"id": 2}, {"id": 3}],
        "c": [{"id": 1}]
    }
    lists = extract_lists(data)
    assert len(lists) == 3
    assert lists[0] == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert lists[1] == [{"id": 1}, {"id": 2}]
    assert lists[2] == [{"id": 1}]

def test_extract_lists_ignores_meta_key():
    data = {
        "items": [{"id": 1}],
        "_meta": [{"id": 2}, {"id": 3}]
    }
    lists = extract_lists(data)
    assert len(lists) == 1
    assert lists[0] == [{"id": 1}]

def test_flatten_dict_basic():
    assert flatten_dict({"a": {"b": 1}}) == {"a_b": 1}

def test_flatten_dict_custom_separator():
    assert flatten_dict({"a": {"b": 1}}, sep="-") == {"a-b": 1}

def test_extract_by_keys_first_match():
    assert extract_by_keys({"a": 1, "b": 2}, ["b", "a"]) == "2"

def test_extract_by_keys_returns_none_when_all_missing():
    assert extract_by_keys({"a": 1}, ["b", "c"]) is None

def test_extract_by_keys_handles_errors_list():
    assert extract_by_keys({"errors": [{"message": "x"}]}, ["errors"]) == "x"

@pytest.mark.parametrize("value, joiner, expected", [
    (["a", "b"], "\n", "a\nb"),          # List of strings
    ("a,b", "\n", "a,b"),                # String
    (None, "\n", ""),                    # None
    ([], "\n", ""),                      # Empty list
    ([1, 2, 3], "-", "1-2-3"),           # List of integers with custom joiner
    (["a", 2, None, 3.14], ", ", "a, 2, None, 3.14"), # Mixed list
    (123, "\n", "123"),                  # Integer
    (3.14, "\n", "3.14"),                # Float
    ({"key": "val"}, "\n", "{'key': 'val'}"), # Dictionary
    (True, "\n", "True"),                # Boolean
    (False, "\n", "False"),              # Boolean
    (["a", "b"], "", "ab"),              # Empty joiner
    (["a", "b"], "---", "a---b"),        # Multicharacter joiner
])
def test_resolve_array_string_edge_cases(value, joiner, expected):
    assert resolve_array_string(value, joiner) == expected
