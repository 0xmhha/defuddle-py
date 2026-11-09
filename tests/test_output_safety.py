"""Hostile input must stay inert after a browser or Markdown renderer reads the output.

These tests check the reparsed result, not the output text, because the bugs they
guard against only appear once a second parser reads what this package wrote.
"""

import pytest
from bs4 import BeautifulSoup, Tag
from markdown_it import MarkdownIt

from defuddle_py import parse
from defuddle_py.markdown import format_link_destination, to_markdown
from defuddle_py.sanitize import is_dangerous_url, strip_unsafe_elements

_PROSE = "The committee reviewed the proposal in detail, and it found the plan sound. " * 40
_ACTIVE_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "meta", "link", "base"})
_URL_ATTRIBUTES = ("href", "src", "action", "formaction", "poster", "xlink:href")
_ALERT_IMAGE = "&lt;img src=x onerror=alert(1)&gt;"
_RAW_ALERT_IMAGE = "<img src=x onerror=alert(1)>"


@pytest.fixture(autouse=True)
def _conversion_without_output_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check repairs bad output, which would hide a broken conversion rule here.
    monkeypatch.setattr("defuddle_py.markdown._is_inert", lambda _markdown: True)


def _article(inner: str) -> str:
    return f"<html><body><article><p>{_PROSE}</p>{inner}<p>{_PROSE}</p></article></body></html>"


def _active_parts(html: str, *, sanitized_html: bool = False) -> list[str]:
    """Return every part of ``html`` that a browser would act on.

    Sanitized HTML may keep math scripts and embeds. Rendered Markdown holds neither.
    """
    found: list[str] = []
    for element in BeautifulSoup(html, "html5lib").find_all(True):
        assert isinstance(element, Tag)
        is_math_script = (
            element.name == "script"
            and str(element.get("type", "")).lower().startswith("math/")
            and element.namespace == "http://www.w3.org/1999/xhtml"
        )
        allowed = sanitized_html and (is_math_script or element.name == "iframe")
        if element.name in _ACTIVE_TAGS and not allowed:
            found.append(element.name)
        found.extend(name for name in element.attrs if name.lower().startswith("on"))
        found.extend(
            name
            for name in _URL_ATTRIBUTES
            if name in element.attrs and is_dangerous_url(str(element.attrs[name]))
        )
    return found


class _LinkKeepingRenderer(MarkdownIt):
    """Render every link, as a renderer without a scheme filter does."""

    def validateLink(self, url: str) -> bool:
        return True


def _render(markdown: str) -> str:
    renderer = MarkdownIt("commonmark", {"html": True}).enable("table")
    return str(renderer.render(markdown))


def _assert_inert(html: str) -> None:
    result = parse(_article(html), url="https://example.com/post")
    assert _active_parts(result.content, sanitized_html=True) == []
    assert _active_parts(_render(result.content_markdown)) == []


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            f'<svg><script type="math/tex">&lt;/script&gt;{_ALERT_IMAGE}</script></svg>',
            id="svg script closed by decoded entities",
        ),
        pytest.param(
            f'<script type="math/tex">x</script >{_RAW_ALERT_IMAGE}</script>',
            id="script end tag completed by space replacement",
        ),
        pytest.param(
            f'<script type="math/tex"><!-- <script> </script> {_RAW_ALERT_IMAGE}</script>',
            id="script double escape state",
        ),
        pytest.param(
            f'<math><script type="math/tex">&lt;/script&gt;{_ALERT_IMAGE}</script></math>',
            id="math namespace script",
        ),
        pytest.param(
            f'<math><annotation-xml encoding="text/html"><script type="math/tex">'
            f"{_RAW_ALERT_IMAGE}</script></annotation-xml></math>",
            id="math script after its integration point is lost",
        ),
        pytest.param(
            f'<svg><foreignObject><script type="math/tex">{_RAW_ALERT_IMAGE}</script>'
            "</foreignObject></svg>",
            id="math script in foreignObject",
        ),
        pytest.param(f"<xmp>{_RAW_ALERT_IMAGE}</xmp>", id="xmp raw text"),
        pytest.param(f"<noembed>{_RAW_ALERT_IMAGE}</noembed>", id="noembed raw text"),
        pytest.param(f"<noframes>{_RAW_ALERT_IMAGE}</noframes>", id="noframes raw text"),
        pytest.param(f"<textarea></textarea>{_RAW_ALERT_IMAGE}", id="textarea sibling"),
        pytest.param(f"<svg><title>{_RAW_ALERT_IMAGE}</title></svg>", id="svg title"),
        pytest.param(
            f"<svg><![CDATA[{_RAW_ALERT_IMAGE}]]></svg>",
            id="cdata in foreign content",
        ),
        pytest.param(
            f"<math><annotation-xml encoding='text/html'>{_RAW_ALERT_IMAGE}"
            "</annotation-xml></math>",
            id="math html integration point",
        ),
        pytest.param(
            f"<form><math><mtext></form><form><mglyph><style></math>{_RAW_ALERT_IMAGE}",
            id="namespace confusion through style",
        ),
        pytest.param(
            f"<svg></p><style><a id='</style>{_RAW_ALERT_IMAGE}'>",
            id="style attribute breakout",
        ),
        pytest.param(f"<!-- x --!>{_RAW_ALERT_IMAGE}<!-- y -->", id="comment bang end"),
        pytest.param(
            '<pre><meta http-equiv="refresh" content="0;url=https://example.com/"></pre>',
            id="meta refresh in pre",
        ),
        pytest.param(
            '<pre><link rel="stylesheet" href="https://example.com/x.css"></pre>',
            id="stylesheet in pre",
        ),
    ],
)
def test_hostile_html_stays_inert_after_reparsing(markup: str) -> None:
    _assert_inert(markup)


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            f"<pre><code>x\n```\n\n{_ALERT_IMAGE}\n\n```\ny</code></pre>",
            id="pre content closes the fence",
        ),
        pytest.param(
            f"<pre>x\n````````\n\n{_ALERT_IMAGE}\n\n````````\ny</pre>",
            id="pre content with a long fence",
        ),
        pytest.param(f"<p><code>x\n\n{_ALERT_IMAGE}</code></p>", id="code with blank line"),
        pytest.param(f"<p><kbd>x\n\n{_ALERT_IMAGE}</kbd></p>", id="kbd with blank line"),
        pytest.param(f"<p><samp>x\n\n{_ALERT_IMAGE}</samp></p>", id="samp with blank line"),
        pytest.param(f"<p><code>x`{_ALERT_IMAGE}`y</code></p>", id="code closed by backtick"),
        pytest.param(f"<p><code>x``{_ALERT_IMAGE}`</code></p>", id="code mixed backticks"),
        pytest.param(f"<p><code>`{_ALERT_IMAGE}</code></p>", id="code leading backtick"),
        pytest.param(f"<p><code>x&#13;&#13;{_ALERT_IMAGE}</code></p>", id="code carriage return"),
        pytest.param(
            f'<a href="https://example.com/"><pre>x\n\n{_ALERT_IMAGE}\n</pre></a>',
            id="pre inside a link",
        ),
        pytest.param(f"<strong><pre>x\n\n{_ALERT_IMAGE}\n</pre></strong>", id="pre inside bold"),
        pytest.param(f"<em><pre>x\n\n{_ALERT_IMAGE}\n</pre></em>", id="pre inside emphasis"),
        pytest.param(f"<del><pre>x\n\n{_ALERT_IMAGE}\n</pre></del>", id="pre inside deletion"),
        pytest.param(f"<h2><pre>x\n\n{_ALERT_IMAGE}\n</pre></h2>", id="pre inside heading"),
        pytest.param(
            f"<table><tr><td><pre>x\n\n{_ALERT_IMAGE}</pre></td></tr></table>",
            id="pre inside table cell",
        ),
        pytest.param(
            "<table><tr><th>a</th><th>b</th></tr>"
            f"<tr><td><code>x | {_ALERT_IMAGE}</code></td><td>y</td></tr></table>",
            id="code pipe splits table cell",
        ),
        pytest.param(
            "<table><tr><th>a</th><th>b</th></tr>"
            f"<tr><td><pre>x | {_ALERT_IMAGE}</pre></td><td>y</td></tr></table>",
            id="pre pipe splits table cell",
        ),
        pytest.param(
            "<table><tr><th>a</th><th>b</th></tr>"
            f"<tr><td><code>x\\| {_ALERT_IMAGE}</code></td><td>y</td></tr></table>",
            id="backslash before the pipe undoes its escape",
        ),
        pytest.param(
            "<table><tr><th>a</th><th>b</th></tr>"
            f"<tr><td><kbd>x\\\\| {_ALERT_IMAGE}</kbd></td><td>y</td></tr></table>",
            id="two backslashes before the pipe",
        ),
        pytest.param(
            f"<code><table><tr><td>x\n\n{_ALERT_IMAGE}</td></tr></table></code>",
            id="table inside code",
        ),
        pytest.param(f"<code><p>x</p><p>{_ALERT_IMAGE}</p></code>", id="paragraphs inside code"),
        pytest.param(
            f"<ul><li><pre>x\n```\n\n{_ALERT_IMAGE}\n```\n</pre></li></ul>",
            id="pre in list closes the fence",
        ),
        pytest.param(
            f"<blockquote><pre>x\n```\n\n{_ALERT_IMAGE}\n```\n</pre></blockquote>",
            id="pre in quote closes the fence",
        ),
        pytest.param(
            '<video src="https://a.example/x)<script>alert(1)</script>"></video>',
            id="video src closes destination",
        ),
        pytest.param(
            '<video src="https://a.example/x)[click](javascript:alert(1))">zz</video>',
            id="video src adds a link",
        ),
        pytest.param(
            '<video><source src="https://a.example/x)<script>alert(1)</script>"></video>',
            id="video source closes destination",
        ),
    ],
)
def test_hostile_markup_stays_inert_after_markdown_rendering(markup: str) -> None:
    _assert_inert(markup)


def test_direct_markdown_call_filters_video_poster() -> None:
    root = BeautifulSoup(
        '<video poster="https://a.example/p)<script>alert(1)</script>"'
        ' src="https://a.example/v.mp4">words</video>',
        "html5lib",
    ).body
    assert root is not None
    assert _active_parts(_render(to_markdown(root))) == []


def test_direct_markdown_call_drops_dangerous_video_urls() -> None:
    root = BeautifulSoup(
        '<video poster="javascript:alert(1)"><source src="javascript:alert(2)"></video>',
        "html5lib",
    ).body
    assert root is not None
    assert "javascript" not in to_markdown(root)


def test_math_script_without_markup_is_kept() -> None:
    result = parse(_article('<script type="math/tex">x^2 + y^2</script>'))
    assert '<script type="math/tex">x^2 + y^2</script>' in result.content


@pytest.mark.parametrize(
    "body",
    ["</script", "</SCRIPT", "<script", "<!--", "a<!--b", "x </sCrIpT > y"],
)
def test_math_script_that_could_end_early_is_removed(body: str) -> None:
    root = BeautifulSoup("<div><script type='math/tex'></script></div>", "html5lib").div
    assert root is not None
    script = root.find("script")
    assert isinstance(script, Tag)
    script.string = body
    strip_unsafe_elements(root)
    assert "script" not in root.decode().lower()


def test_comments_are_removed_on_the_fallback_path() -> None:
    root = BeautifulSoup("<div><!-- note --><p>x</p></div>", "html5lib").div
    assert root is not None
    strip_unsafe_elements(root)
    assert "<!--" not in root.decode()


def test_fenced_code_keeps_multiple_lines() -> None:
    markdown = parse(_article("<pre><code>first\nsecond</code></pre>")).content_markdown
    assert "```\nfirst\nsecond\n```" in markdown


def test_fence_is_longer_than_backticks_in_the_code() -> None:
    markdown = parse(_article("<pre>a\n`````\nb</pre>")).content_markdown
    assert "``````\na\n`````\nb\n``````" in markdown


def test_inline_code_keeps_its_text() -> None:
    assert "`x = 1`" in parse(_article("<p><code>x = 1</code></p>")).content_markdown


def test_inline_code_with_backtick_uses_a_longer_delimiter() -> None:
    assert "`` a`b ``" in parse(_article("<p><code>a`b</code></p>")).content_markdown


def test_code_in_a_table_cell_renders_as_one_cell() -> None:
    html = _article(
        "<table><tr><th>a</th><th>b</th></tr><tr><td><code>x|y</code></td><td>z</td></tr></table>"
    )
    rendered = _render(parse(html).content_markdown)
    cells = BeautifulSoup(rendered, "html5lib").find_all("td")
    assert [cell.get_text() for cell in cells] == ["x|y", "z"]


def test_dangerous_resolved_url_is_stripped_on_the_fallback_path() -> None:
    html = (
        '<html><head><base href="javascript:alert(1)//"></head>'
        '<body><a href="x">t</a></body></html>'
    )
    result = parse(html, url="https://example.com/post")
    assert "javascript:" not in result.content


_WRAPPERS = (
    "{}",
    "<p>{}</p>",
    "<a href='https://example.com/'>{}</a>",
    "<strong>{}</strong>",
    "<em>{}</em>",
    "<del>{}</del>",
    "<sub>{}</sub>",
    "<sup>{}</sup>",
    "<q>{}</q>",
    "<mark>{}</mark>",
    "<h3>{}</h3>",
    "<ul><li>{}</li></ul>",
    "<ul><li>before<ul><li>{}</li></ul></li></ul>",
    "<ul><li>before<ol start='2'><li>{}</li></ol></li></ul>",
    "<ol start='1234567890'><li>{}</li></ol>",
    "<ol start='0'><li>{}</li></ol>",
    "<ol><li>before {}</li></ol>",
    "<blockquote>{}</blockquote>",
    "<dl><dt>{}</dt><dd>{}</dd></dl>",
    "<figure><figcaption>{}</figcaption></figure>",
    "<details><summary>{}</summary>{}</details>",
    "<table><caption>{}</caption><tr><th>{}</th><th>b</th></tr><tr><td>{}</td><td>c</td></tr></table>",
    "<a href='https://example.com/'><strong>{}</strong></a>",
    "<blockquote><ul><li><em>{}</em></li></ul></blockquote>",
)
_CODE_TAGS = (
    "<pre>{}</pre>",
    "<code>{}</code>",
    "<kbd>{}</kbd>",
    "<samp>{}</samp>",
    "<pre><code>{}</code></pre>",
    "<code><pre>{}</pre></code>",
    "<xmp>{}</xmp>",
    "<listing>{}</listing>",
)
_CODE_BODIES = (
    f"x\n\n{_ALERT_IMAGE}",
    f"x\n```\n\n{_ALERT_IMAGE}\n\n```\ny",
    f"x\n~~~\n\n{_ALERT_IMAGE}\n\n~~~\ny",
    f"x`\n\n{_ALERT_IMAGE}\n\n`y",
    f"```` | {_ALERT_IMAGE} | ````",
    f"\\| {_ALERT_IMAGE} \\\\| {_ALERT_IMAGE}",
    f"x&#13;&#13;{_ALERT_IMAGE}",
    f"{_ALERT_IMAGE}\n\ny",
    f"\ue000`\ue001`{_ALERT_IMAGE}",
)


@pytest.mark.parametrize("wrapper", _WRAPPERS)
@pytest.mark.parametrize("code_tag", _CODE_TAGS)
def test_code_stays_inert_in_every_context(wrapper: str, code_tag: str) -> None:
    for body in _CODE_BODIES:
        _assert_inert(wrapper.replace("{}", code_tag.format(body)))


_BREAKING_LINKS = (
    '<a href="https://x.example/`"><p>a</p><p>b</p></a>',
    '<a href="https://x.example/" title="`"><p>a</p><p>b</p></a>',
    '<a href="https://x.example/`"><iframe src="https://b.example/"></iframe></a>',
    '<a href="https://x.example/|`">l</a>',
    '<a href="https://x.example/a b`"><p>a</p><p>b</p></a>',
    '<video src="https://x.example/`">a<p>x</p><p>y</p></video>',
    '<video poster="https://x.example/`">a<p>x</p><p>y</p></video>',
    '<img src="https://x.example/`" alt="`">',
    "<code>a``b</code>",
    "<kbd>```</kbd>",
    "<samp>`</samp>",
)
_SEPARATORS = ("", "<span></span>", "<b></b>", "<span>", " ")
_FOLLOWERS = (
    f"<code>{_ALERT_IMAGE}</code>",
    f"<kbd>{_ALERT_IMAGE}</kbd>",
    f"<a href='https://x.example/`'>{_ALERT_IMAGE}</a>",
    f"<pre>{_ALERT_IMAGE}</pre>",
)
_NEIGHBOR_WRAPPERS = (
    "{}",
    "<p>{}</p>",
    "<ul><li>{}</li></ul>",
    "<blockquote>{}</blockquote>",
    "<h3>{}</h3>",
    "<table><tr><th>a</th><th>b</th></tr><tr><td>{}</td><td>c</td></tr></table>",
)


@pytest.mark.parametrize("wrapper", _NEIGHBOR_WRAPPERS)
@pytest.mark.parametrize("first", _BREAKING_LINKS)
def test_neighbors_cannot_expose_code(wrapper: str, first: str) -> None:
    for separator in _SEPARATORS:
        for follower in _FOLLOWERS:
            _assert_inert(wrapper.format(f"{first}{separator}{follower}"))


def test_blank_line_link_without_a_page_url_stays_inert() -> None:
    markup = '<a href="img src=x onerror=alert(1)"><p>a</p><p>b</p></a>'
    result = parse(_article(markup))
    assert _active_parts(_render(result.content_markdown)) == []


def test_failed_link_cannot_add_a_javascript_link() -> None:
    renderer = _LinkKeepingRenderer("commonmark", {"html": True}).enable("table")
    markup = '<a href="https://x.example/[y](javascript:alert(1)) z"><p>a</p><p>b</p></a>'
    assert _active_parts(renderer.render(parse(_article(markup)).content_markdown)) == []


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(f"<p>https://a.example/<code>{_ALERT_IMAGE}</code></p>", id="autolink"),
        pytest.param(f"<p><em>x <code>a* `{_ALERT_IMAGE}</code> y</em></p>", id="emphasis"),
        pytest.param(
            f"<p><a href='https://a.example/'><code>``{_ALERT_IMAGE}](x)</code></a></p>",
            id="link label",
        ),
        pytest.param(f"<p><del><code>{_ALERT_IMAGE}`~~</code>x</del></p>", id="strikethrough"),
        pytest.param(f"<p><kbd>{_ALERT_IMAGE}</kbd></p>", id="kbd"),
        pytest.param(f"<p><samp>{_ALERT_IMAGE}</samp></p>", id="samp"),
        pytest.param(f"<h3><pre>{_ALERT_IMAGE}</pre></h3>", id="pre as a span"),
    ],
)
def test_inline_code_never_holds_a_literal_tag(markup: str) -> None:
    markdown = parse(_article(markup)).content_markdown
    assert _RAW_ALERT_IMAGE not in markdown
    assert "&lt;img" in markdown


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(f"<p>https://a.example/x{_ALERT_IMAGE}</p>", id="text after a url"),
        pytest.param(f"<p>www.a.example/{_ALERT_IMAGE}</p>", id="text after a www address"),
        pytest.param(
            f"<p><a href='https://a.example/`'><code>x</code></a>{_ALERT_IMAGE}</p>",
            id="text after a link",
        ),
        pytest.param(f"<p><code>a</code> \\{_ALERT_IMAGE}</p>", id="text after a backslash"),
    ],
)
def test_text_never_holds_a_backslash_before_a_tag(markup: str) -> None:
    markdown = parse(_article(markup)).content_markdown
    assert "<img" not in markdown
    assert "&lt;img" in markdown


def test_text_cannot_form_a_link_inside_link_text() -> None:
    markup = "<p><a href='https://a.example/'>![x](javascript:alert(1))</a></p>"
    assert r"\]\(javascript" in parse(_article(markup)).content_markdown


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            '<p><img src="https://a.example/x.png" alt="a">'
            "{<code>: onerror=alert(1) &lt;</code>}</p>",
            id="code between braces",
        ),
        pytest.param('<p><a href="https://a.example/">x</a>{: onclick=y}</p>', id="text braces"),
        pytest.param("<p>text</p><p>{<kbd>: onclick=y ](</kbd>}</p>", id="paragraph attributes"),
    ],
)
def test_attribute_lists_stay_escaped(markup: str) -> None:
    markdown = parse(_article(markup)).content_markdown
    assert r"\{" in markdown
    assert r"\}" in markdown
    assert "=" not in markdown.replace(r"\=", "")


def test_braces_in_code_are_kept() -> None:
    assert "`{a}`" in parse(_article("<p><code>{a}</code></p>")).content_markdown


def test_inline_code_cannot_hold_a_link() -> None:
    markup = "<p>https://a.example/<code>[x](javascript:alert(1))</code></p>"
    assert "[x](" not in parse(_article(markup)).content_markdown


def test_inline_code_without_markup_keeps_its_style() -> None:
    assert (
        "`a > b && c`"
        in parse(_article("<p><code>a &gt; b &amp;&amp; c</code></p>")).content_markdown
    )


def test_fenced_code_keeps_tags() -> None:
    markdown = parse(_article("<pre>&lt;div&gt;x&lt;/div&gt;\ny</pre>")).content_markdown
    assert "```\n<div>x</div>\ny\n```" in markdown


@pytest.mark.parametrize(
    "href",
    [
        "java&Tab;script:alert(1)",
        "java&NewLine;script:alert(1)",
        "java&#9script:alert(1)",
        "javascript&colon;alert(1)",
        "javascript&amp;colon;alert(1)",
        "page.html",
    ],
)
def test_unknown_destination_stays_relative(href: str) -> None:
    assert format_link_destination(href).startswith("./")


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://example.com/", "https://example.com/"),
        ("HTTP://example.com/", "HTTP://example.com/"),
        ("mailto:a@example.com", "mailto:a@example.com"),
        ("/path", "/path"),
        ("../path", "../path"),
        ("#section", "#section"),
        ("?q=1", "?q=1"),
        ("  https://example.com/  ", "https://example.com/"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_known_destinations_are_kept(href: str, expected: str) -> None:
    assert format_link_destination(href) == expected


def test_marker_in_an_attribute_cannot_hide_an_entity() -> None:
    markup = '<a href="java&amp;&#xE000;Tab;script:alert(1)">click</a>'
    assert "](java" not in parse(_article(markup)).content_markdown


def test_marker_in_an_attribute_does_not_blank_the_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    markup = '<a href="java&#xE000;script:alert(1)">click</a>'
    assert "committee" in parse(_article(markup)).content_markdown


def test_base_with_another_scheme_is_ignored() -> None:
    html = (
        '<html><head><base href="foo:bar"></head><body><article>'
        f'<p>{_PROSE}</p><a href="page.html">link</a><p>{_PROSE}</p>'
        "</article></body></html>"
    )
    content = parse(html, url="https://site.example/post").content
    assert "https://site.example/page.html" in content


def test_adjacent_code_spans_are_separated() -> None:
    markdown = parse(_article("<p><code>a</code><code>b</code></p>")).content_markdown
    assert "`a` `b`" in markdown


def test_span_markers_in_page_text_are_removed() -> None:
    markdown = parse(_article("<p>a\ue000b\ue001c</p>")).content_markdown
    assert "abc" in markdown


@pytest.mark.parametrize(
    "markup",
    [
        "<soup>kept words</soup>",
        '<table><tr><td colspan="\u00b2">kept words</td></tr></table>',
        '<table><tr><th colspan="\u00b2">kept words</th></tr></table>',
        '<ol start="\u00b2"><li>kept words</li></ol>',
        '<ol start="' + "1" * 5000 + '"><li>kept words</li></ol>',
        '<table><tr><td colspan="' + "1" * 5000 + '">kept words</td></tr></table>',
        '<table><tr><th colspan="' + "1" * 5000 + '">kept words</th></tr></table>',
        "<h" + "1" * 5000 + ">kept words</h" + "1" * 5000 + ">",
        '<table width="' + "1" * 5000 + '"><tr><td>kept words</td></tr></table>',
    ],
)
def test_unusual_markup_converts_without_a_crash(markup: str) -> None:
    assert "kept words" in parse(_article(markup)).content_markdown
