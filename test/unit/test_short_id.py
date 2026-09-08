"""Unit tests for utils.short_id (slugify + generate_short_id)."""

import re

import pytest

from utils.short_id import ShortIdCollisionError, generate_short_id, slugify

# Mirrors plugins/portfolio_plugin/routes.py JOB_ID_RE
_PUBLIC_JOB_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def test_slugify_basic():
    assert slugify("Whiskers Successor") == "whiskers_successor"


def test_slugify_strips_non_alnum():
    assert slugify("Whiskers & Co., Inc.!") == "whiskers_co_inc"


def test_slugify_caps_word_count():
    assert slugify("one two three four five", max_words=3) == "one_two_three"


def test_slugify_caps_length():
    result = slugify("aaaaaaaaaa bbbbbbbbbb cccccccccc", max_len=12)
    assert len(result) <= 12


def test_slugify_empty_input():
    assert slugify("") == ""


def test_generate_short_id_shape():
    short_id = generate_short_id(["Whiskers", "Successor"])
    assert short_id.startswith("whiskers_successor_")
    suffix = short_id.rsplit("_", 1)[-1]
    assert len(suffix) >= 10
    assert re.fullmatch(r"[a-z0-9]+", suffix)
    assert _PUBLIC_JOB_ID_RE.match(short_id)


def test_generate_short_id_suffix_entropy():
    """Suffix must be wide enough that company/role alone is not enumerable."""
    short_id = generate_short_id(["Acme", "Engineer"])
    suffix = short_id.rsplit("_", 1)[-1]
    assert len(suffix) >= 10
    # Not the old 3-digit decimal form.
    assert not re.fullmatch(r"\d{3}", suffix)


def test_generate_short_id_no_exists_hook():
    short_id = generate_short_id(["a", "b"], exists=None)
    assert short_id.startswith("a_b_")
    assert _PUBLIC_JOB_ID_RE.match(short_id)


def test_generate_short_id_falls_back_when_seed_empty():
    short_id = generate_short_id([], exists=None)
    assert short_id.startswith("portfolio_")


def test_generate_short_id_retries_on_collision():
    seen = []

    def exists(candidate: str) -> bool:
        seen.append(candidate)
        return len(seen) < 3  # first two collide, third is free

    short_id = generate_short_id(["x"], exists=exists, max_attempts=5)
    assert short_id == seen[-1]
    assert len(seen) == 3


def test_generate_short_id_exhausts_attempts():
    with pytest.raises(ShortIdCollisionError):
        generate_short_id(["x"], exists=lambda c: True, max_attempts=3)
