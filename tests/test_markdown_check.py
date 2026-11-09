"""The final check that repairs or drops Markdown exposing raw HTML."""

import logging

import pytest
from bs4 import BeautifulSoup, Tag

from defuddle_py import markdown, parse
from defuddle_py.markdown import to_markdown

_EXPOSED = "<img src=x onerror=alert(1)>"


def _body(html: str) -> Tag:
    body = BeautifulSoup(html, "html5lib").body
    assert body is not None
    return body


@pytest.mark.parametrize(
    "text",
    [
        _EXPOSED,
        f"text\n\n{_EXPOSED}\n\ntext",
        "<!-- note -->",
        "[link](javascript:alert(1))",
        "![image](vbscript:x)",
        "| a |\n| --- |\n| [l](javascript:x) |",
    ],
)
def test_detects_active_markdown(text: str) -> None:
    assert not markdown._is_inert(text)


@pytest.mark.parametrize(
    "text",
    [
        "plain words",
        "`<img src=x onerror=alert(1)>`",
        f"```\n{_EXPOSED}\n```",
        r"\<img src=x onerror=alert(1)\>",
        "[link](https://example.com/)",
        "![image](data:image/png;base64,AAAA)",
    ],
)
def test_accepts_inert_markdown(text: str) -> None:
    assert markdown._is_inert(text)


def test_bad_conversion_is_redone_with_code_as_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    good_convert = markdown._convert

    def expose_code(root: Tag) -> str:
        return _EXPOSED if root.find("code") else good_convert(root)

    monkeypatch.setattr(markdown, "_convert", expose_code)
    with caplog.at_level(logging.WARNING, logger="defuddle_py.markdown"):
        result = to_markdown(_body("<p><code>&lt;img src=x onerror=alert(1)&gt;</code></p>"))
    assert result == r"&lt;img src\=x onerror\=alert(1)\>"
    assert "converting code as plain text" in caplog.text


def test_output_that_stays_active_is_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(markdown, "_convert", lambda _root: _EXPOSED)
    with caplog.at_level(logging.WARNING, logger="defuddle_py.markdown"):
        assert to_markdown(_body("<p>x</p>")) == ""
    assert "Dropping Markdown" in caplog.text


def test_check_runs_on_the_parse_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_check = markdown._is_inert

    def recording_check(text: str) -> bool:
        calls.append(text)
        return real_check(text)

    monkeypatch.setattr(markdown, "_is_inert", recording_check)
    result = parse("<article><p>Some article words for the page.</p></article>")
    assert calls == [result.content_markdown]
