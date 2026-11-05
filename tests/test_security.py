"""Security behavior: sanitization, escaping and resource limits."""

import ast
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import defuddle_py
from defuddle_py import InputTooLargeError, Limits, TreeTooDeepError, parse
from defuddle_py.cli import main
from defuddle_py.dom import check_source_depth
from defuddle_py.extractor import count_html_words
from defuddle_py.limits import DEFAULT_MAX_INPUT_BYTES, DEFAULT_MAX_TREE_DEPTH
from defuddle_py.markdown import escape_inline_text, format_link_destination, format_link_title
from defuddle_py.sanitize import is_dangerous_url
from defuddle_py.scoring import score_element

_PROSE = "The committee reviewed the proposal in detail, and it found the plan sound. " * 40
_HOSTILE_BUDGET_SECONDS = 5.0


def _article(inner: str) -> str:
    return f"<html><body><article><p>{_PROSE}</p>{inner}<p>{_PROSE}</p></article></body></html>"


@pytest.mark.parametrize(
    ("url", "dangerous"),
    [
        ("javascript:alert(1)", True),
        ("JaVaScRiPt:alert(1)", True),
        ("java\nscript:alert(1)", True),
        ("\x01javascript:alert(1)", True),
        ("vbscript:msgbox(1)", True),
        ("blob:https://example.com/id", True),
        ("data:text/html,<script>alert(1)</script>", True),
        ("data:image/png;base64,AAAA", False),
        ("https://example.com/", False),
        ("/relative", False),
    ],
)
def test_detects_dangerous_urls(url: str, *, dangerous: bool) -> None:
    assert is_dangerous_url(url) is dangerous


def test_svg_data_url_is_dangerous_when_inline_images_are_not_allowed() -> None:
    assert is_dangerous_url("data:image/svg+xml,<svg/>", allow_inline_image=False)


@pytest.mark.parametrize(
    ("markup", "forbidden"),
    [
        ('<a href="javascript:alert(1)">link text here</a>', "javascript:"),
        ('<a href=" jav&#10;ascript:alert(1)">link text here</a>', "alert(1)"),
        ('<img src="https://example.com/a.png" width="300" onerror="alert(1)">', "onerror"),
        ('<div onclick="alert(1)">clickable words</div>', "onclick"),
        ('<iframe src="https://example.com/" srcdoc="<script>x</script>"></iframe>', "srcdoc"),
        ('<iframe src="data:image/svg+xml,evil"></iframe>', "data:image/svg"),
        ("<script>alert(1)</script>", "<script"),
        ("<style>body{}</style>", "<style"),
        ('<object data="https://example.com/x"></object>', "<object"),
        ('<embed src="https://example.com/x">', "<embed"),
        ('<svg><animate attributeName="href" to="javascript:alert(1)"/></svg>', "animate"),
        ('<a href="data:text/html;base64,AAAA">link text here</a>', "data:text"),
    ],
)
def test_strips_unsafe_markup(markup: str, forbidden: str) -> None:
    result = parse(_article(markup), url="https://example.com/post")
    assert forbidden not in result.content
    assert forbidden not in result.content_markdown


def test_fallback_body_is_sanitized() -> None:
    html = "<html><body onload='alert(1)'><script>alert(1)</script></body></html>"
    result = parse(html)
    assert "alert" not in result.content


def test_schema_text_is_sanitized() -> None:
    body = "A long schema article body sentence. " * 80
    html = (
        '<html><head><script type="application/ld+json">'
        f'{{"articleBody":"{body}<script>alert(1)</script>"}}'
        "</script></head><body><div>short</div></body></html>"
    )
    assert "<script" not in parse(html).content


def test_unsafe_content_selector_root_is_not_serialized() -> None:
    html = "<html><body><template id='t'><p>x</p></template></body></html>"
    assert "<template" not in parse(html, content_selector="#t").content


def test_link_destination_drops_dangerous_urls() -> None:
    assert format_link_destination("javascript:alert(1)") == ""


def test_link_destination_escapes_breakout_characters() -> None:
    assert format_link_destination("https://example.com/a)b") == r"https://example.com/a\)b"
    assert format_link_destination("https://example.com/<x>") == ("https://example.com/%3Cx%3E")
    assert "\n" not in format_link_destination("https://example.com/a\nb")


def test_link_title_cannot_close_the_quote() -> None:
    assert format_link_title('a"b\nc') == ' "a&quot;b c"'


def test_image_alt_brackets_are_escaped() -> None:
    assert escape_inline_text("Photo [one]") == r"Photo \[one\]"


def test_markdown_image_with_bracket_alt_stays_literal() -> None:
    html = _article('<img src="https://example.com/p.png" width="300" alt="a](javascript:x)">')
    markdown = parse(html, url="https://example.com/").content_markdown
    assert r"a\]\(javascript:x)](https://example.com/p.png)" in markdown


def test_markdown_escapes_tag_like_text() -> None:
    markdown = parse(_article("<p>Monte&lt;video&gt; game</p>")).content_markdown
    assert "<video>" not in markdown


def test_markdown_drops_javascript_link_but_keeps_text() -> None:
    markdown = parse(_article('<p><a href="javascript:x()">kept words</a></p>')).content_markdown
    assert "kept words" in markdown
    assert "javascript" not in markdown


def test_rejects_oversized_bytes() -> None:
    with pytest.raises(InputTooLargeError, match="limit is 10"):
        parse(b"x" * 11, limits=Limits(max_input_bytes=10))


def test_rejects_oversized_str_by_encoded_size() -> None:
    with pytest.raises(InputTooLargeError):
        parse("é" * 6, limits=Limits(max_input_bytes=10))


def test_default_input_limit_is_ten_megabytes() -> None:
    assert DEFAULT_MAX_INPUT_BYTES == 10 * 1024 * 1024


def test_cli_rejects_oversized_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = tmp_path / "page.html"
    page.write_bytes(b"x" * 20)
    assert main(["parse", str(page), "--max-bytes", "10"]) == 1
    assert "exceeds 10 bytes" in capsys.readouterr().err


def test_cli_reports_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", str(tmp_path / "absent.html")]) == 1
    assert "defuddle-py:" in capsys.readouterr().err


def test_cli_prints_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = tmp_path / "page.html"
    page.write_text(_article("<h2>Section title</h2>"), encoding="utf-8")
    assert main(["parse", str(page), "--md"]) == 0
    assert "## Section title" in capsys.readouterr().out


def test_deep_nesting_raises_domain_error_before_parsing() -> None:
    with pytest.raises(TreeTooDeepError):
        parse("<div>" * 200_000)


def test_depth_check_ignores_elements_with_optional_end_tags() -> None:
    check_source_depth("<p>x" * 2000 + "<li>y" * 2000, 10)


def test_depth_check_ignores_markup_inside_scripts_and_comments() -> None:
    check_source_depth("<script>" + "<div>" * 100 + "</script><!--" + "<div>" * 100 + "-->", 10)


def test_nesting_at_the_source_limit_gives_a_result_or_domain_error() -> None:
    depth = DEFAULT_MAX_TREE_DEPTH
    html = "<div>" * depth + "<p>text</p>" + "</div>" * depth
    with pytest.raises(TreeTooDeepError):
        parse(html)


def test_nesting_below_the_limit_produces_markdown_without_recursion_error() -> None:
    html = "<div>" * 200 + "<p>deep text here</p>" + "</div>" * 200
    assert "deep text here" in parse(html).content_markdown


def test_markdown_recursion_error_becomes_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def overflow(_root: object) -> str:
        raise RecursionError

    monkeypatch.setattr("defuddle_py.to_markdown", overflow)
    with pytest.raises(TreeTooDeepError, match="too deep"):
        parse("<p>x</p>")


def test_mixed_inline_nesting_is_rejected() -> None:
    with pytest.raises(TreeTooDeepError):
        parse("<b><i>" * 100_000)


def test_oversized_json_ld_is_skipped() -> None:
    html = (
        '<html><head><script type="application/ld+json">{"author":{"name":"Too Big"}}'
        "</script></head><body><p>x</p></body></html>"
    )
    assert parse(html, limits=Limits(max_json_ld_bytes=5)).author == ""


def test_deep_json_ld_is_skipped_without_recursion_error() -> None:
    payload = "[" * 100_000 + "]" * 100_000
    html = f'<html><head><script type="application/ld+json">{payload}</script></head></html>'
    assert parse(html).schema_org_data == []


def test_json_ld_over_depth_limit_is_skipped() -> None:
    payload = '{"a":' * 10 + '"x"' + "}" * 10
    html = f'<html><head><script type="application/ld+json">{payload}</script></head></html>'
    assert parse(html, limits=Limits(max_json_ld_depth=5)).schema_org_data == []


def test_malformed_json_ld_is_skipped() -> None:
    html = '<html><head><script type="application/ld+json">{not json</script></head></html>'
    assert parse(html).schema_org_data == []


def _timed(call: Callable[[], object]) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


@pytest.mark.parametrize(
    ("label", "inner"),
    [
        ("month word", "<p>" + "Jan" * 300_000 + "</p>"),
        ("whitespace run", "<p>by" + " " * 500_000 + "!</p>"),
        ("digits", "<p>" + "1" * 500_000 + "</p>"),
        ("repeated by", "<p>" + "by " * 100_000 + "!</p>"),
        ("attribute class", '<div class="' + "a-" * 300_000 + '">x</div>'),
        ("attribute id", '<div id="' + "logo" * 200_000 + '">x</div>'),
        ("srcset spaces", '<img src="a.png" srcset="' + "a " * 300_000 + '">'),
        ("srcset commas", '<img src="a.png" srcset="' + "a 1w," * 100_000 + '">'),
        ("style spaces", '<div style="display' + " " * 500_000 + ':none">x</div>'),
        ("email-like", "<p>" + "a." * 300_000 + "</p>"),
    ],
)
def test_hostile_text_finishes_within_budget(label: str, inner: str) -> None:
    html = _article(inner)
    assert _timed(lambda: parse(html, url="https://example.com/")) < _HOSTILE_BUDGET_SECONDS, label


@pytest.mark.parametrize(
    ("label", "head"),
    [
        ("title month", "<title>" + "Jan" * 300_000 + "</title>"),
        ("title spaces", "<title>a" + " " * 500_000 + "| b</title>"),
        ("meta author spaces", '<meta name="author" content="by' + " " * 500_000 + 'x">'),
        ("meta site name", '<meta property="og:site_name" content="' + "a " * 3 + '">'),
        (
            "json-ld comments",
            '<script type="application/ld+json">' + "\n" * 900_000 + "{}</script>",
        ),
        ("json-ld stars", '<script type="application/ld+json">' + "/*" * 400_000 + "</script>"),
    ],
)
def test_hostile_metadata_finishes_within_budget(label: str, head: str) -> None:
    html = f"<html><head>{head}</head><body><p>x</p></body></html>"
    assert _timed(lambda: parse(html)) < _HOSTILE_BUDGET_SECONDS, label


def test_unclosed_angle_brackets_count_in_linear_time() -> None:
    assert _timed(lambda: count_html_words("<" * 2_000_000)) < 1.0


def test_score_element_collapses_whitespace_before_regexes() -> None:
    html = "<article>by" + " " * 500_000 + "!</article>"
    element = BeautifulSoup(html, "html5lib").article
    assert element is not None
    assert _timed(lambda: score_element(element)) < 1.0


def test_package_imports_no_execution_or_network_modules() -> None:
    forbidden = {"pickle", "subprocess", "socket", "http", "urllib.request", "ssl", "ctypes"}
    for path in Path(defuddle_py.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "compile"), path
            assert not forbidden.intersection(names), path
