"""Tests for byte decoding and the parse time limit."""

import threading
import time
from pathlib import Path

import pytest

from defuddle_py import Limits, ParseTimeoutError, UnsupportedEncodingError, parse
from defuddle_py.cli import main
from defuddle_py.deadline import deadline
from defuddle_py.encoding import decode_html

KOREAN = "안녕하세요 본문입니다"


def _page(declaration: str, text: str = KOREAN) -> str:
    return f"<html><head>{declaration}<title>t</title></head><body><p>{text}</p></body></html>"


@pytest.mark.parametrize(
    ("declaration", "codec"),
    [
        ('<meta charset="euc-kr">', "euc-kr"),
        ("<meta charset=EUC-KR>", "euc-kr"),
        ('<meta http-equiv="Content-Type" content="text/html; charset=euc-kr">', "euc-kr"),
        ('<meta charset="utf-8">', "utf-8"),
        ("", "utf-8"),
    ],
)
def test_page_declaration_selects_the_codec(declaration: str, codec: str) -> None:
    assert KOREAN in decode_html(_page(declaration).encode(codec))


@pytest.mark.parametrize(
    ("declaration", "text", "codec"),
    [
        ('<meta charset="shift_jis">', "こんにちは", "cp932"),
        ('<meta charset="gb2312">', "你好世界", "gb2312"),
        ('<meta charset="big5">', "你好世界", "big5"),
        ('<meta charset="iso-8859-1">', "café", "latin-1"),
        ('<meta charset="windows-1251">', "Привет", "cp1251"),
    ],
)
def test_legacy_encodings(declaration: str, text: str, codec: str) -> None:
    assert text in decode_html(_page(declaration, text).encode(codec))


def test_caller_encoding_beats_page_declaration() -> None:
    data = _page('<meta charset="utf-8">').encode("euc-kr")
    assert KOREAN in decode_html(data, "euc-kr")


def test_byte_order_mark_beats_caller_encoding() -> None:
    data = b"\xef\xbb\xbf" + _page("").encode("utf-8")
    assert KOREAN in decode_html(data, "euc-kr")


def test_undeclared_non_utf8_falls_back_to_windows_1252() -> None:
    assert "café" in decode_html(_page("", "café").encode("latin-1"))


def test_declaration_inside_a_comment_is_ignored() -> None:
    data = _page('<!-- <meta charset="euc-kr"> -->').encode("utf-8")
    assert KOREAN in decode_html(data)


@pytest.mark.parametrize("label", ["utf-7", "unicode_escape", "iso-2022-jp", "hz", "zlib"])
def test_page_cannot_select_a_dangerous_codec(label: str) -> None:
    data = _page(f'<meta charset="{label}">', "+ADw-script+AD4-").encode("ascii")
    decoded = decode_html(data)
    assert "+ADw-script+AD4-" in decoded
    assert "<script>" not in decoded


@pytest.mark.parametrize("label", ["utf-7", "base64", "nonsense"])
def test_unsupported_caller_encoding_is_refused(label: str) -> None:
    with pytest.raises(UnsupportedEncodingError, match="unsupported encoding"):
        decode_html(b"<p>x</p>", label)


def test_parse_handles_declared_encoding_end_to_end() -> None:
    body = "".join(f"<p>{KOREAN}</p>" for _ in range(40))
    data = _page('<meta charset="euc-kr">', body).encode("euc-kr")
    assert KOREAN in parse(data).content_markdown


def test_cli_passes_encoding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "page.html"
    source.write_bytes(_page("").encode("euc-kr"))
    assert main(["parse", str(source), "--md", "--encoding", "euc-kr"]) == 0
    assert KOREAN in capsys.readouterr().out


def test_deadline_stops_a_long_body() -> None:
    started = time.perf_counter()
    with pytest.raises(ParseTimeoutError, match="exceeded"), deadline(0.05):
        while time.perf_counter() - started < 5:
            pass
    assert time.perf_counter() - started < 1


def test_deadline_is_cleared_after_success() -> None:
    with deadline(0.05):
        pass
    time.sleep(0.1)


@pytest.mark.parametrize("seconds", [None, 0, -1])
def test_deadline_can_be_disabled(seconds: float | None) -> None:
    with deadline(seconds):
        time.sleep(0.01)


def test_parse_raises_on_timeout() -> None:
    page = "<article>" + "<p>word word word</p>" * 50_000 + "</article>"
    with pytest.raises(ParseTimeoutError):
        parse(page, limits=Limits(timeout_seconds=0.05))


def test_deadline_warns_off_the_main_thread(caplog: pytest.LogCaptureFixture) -> None:
    def work() -> None:
        with deadline(1):
            pass

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert "not enforced" in caplog.text


def test_cli_reports_timeout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "page.html"
    source.write_text("<article>" + "<p>word</p>" * 50_000 + "</article>", encoding="utf-8")
    assert main(["parse", str(source), "--timeout", "0.05"]) == 1
    assert "exceeded" in capsys.readouterr().err
