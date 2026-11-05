"""Tests for text helpers and URL handling."""

import pytest

from defuddle_py.extractor import count_html_words
from defuddle_py.text import count_words, normalize_text
from defuddle_py.urls import parse_url


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("one two  three", 3), ("漢字テスト", 5), ("hello 世界", 3), ("  \n\t ", 0)],
)
def test_count_words(text: str, expected: int) -> None:
    assert count_words(text) == expected


def test_normalize_text_folds_punctuation_and_case() -> None:
    assert normalize_text("It’s — A  Test…") == "it's - a test..."


def test_count_html_words_separates_adjacent_tags() -> None:
    assert count_html_words("<p>one</p><p>two&nbsp;three</p>") == 3


def test_count_html_words_keeps_text_after_stray_bracket() -> None:
    assert count_html_words("one < two") == 3


def test_parse_url_adds_root_path() -> None:
    parsed = parse_url("https://example.com")
    assert parsed is not None
    assert parsed.href == "https://example.com/"


def test_parse_url_resolves_against_base() -> None:
    parsed = parse_url("../x", "https://example.com/a/b/c")
    assert parsed is not None
    assert parsed.pathname == "/a/x"


@pytest.mark.parametrize("value", ["relative/path", "", "http://[bad"])
def test_parse_url_rejects_non_absolute(value: str) -> None:
    assert parse_url(value) is None
