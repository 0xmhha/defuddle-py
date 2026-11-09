"""Behavior tests for the extraction pipeline, on hand-written HTML."""

import pytest

from defuddle_py import parse

_PROSE = (
    "The committee reviewed the proposal in detail, and it found that the plan was sound. "
    "Several members asked for more data, so the authors collected a second round of figures. "
)
_ARTICLE = f"<p>{_PROSE * 8}</p><p>{_PROSE * 8}</p>"


def test_picks_article_over_surrounding_navigation() -> None:
    html = (
        "<html><head><title>Findings</title></head><body>"
        '<nav><a href="/a">Home</a><a href="/b">About</a></nav>'
        f"<article><h1>Findings</h1>{_ARTICLE}</article>"
        "<footer>All rights reserved</footer></body></html>"
    )
    result = parse(html, url="https://example.com/post")
    assert "committee reviewed" in result.content
    assert "All rights reserved" not in result.content
    assert "Home" not in result.content


def test_prefers_contained_article_over_noisy_main() -> None:
    html = (
        f"<html><body><main><div>{'side note words here. ' * 40}</div>"
        f"<article id='body'>{_ARTICLE}</article></main></body></html>"
    )
    assert "side note" not in parse(html).content


def test_keeps_listing_container_when_many_articles_match() -> None:
    cards = "".join(f"<article><h2>Card {i}</h2><p>{_PROSE * 3}</p></article>" for i in range(3))
    result = parse(f"<html><body><main>{cards}</main></body></html>")
    assert "Card 0" in result.content
    assert "Card 2" in result.content


def test_recovers_content_hidden_by_runtime_wrapper() -> None:
    html = f'<html><body><div class="hidden"><article>{_ARTICLE}</article></div></body></html>'
    assert "committee reviewed" in parse(html).content


def test_uses_schema_text_when_page_has_little_content() -> None:
    body = "A long schema article body sentence. " * 80
    html = (
        '<html><head><script type="application/ld+json">'
        f'{{"@type":"Article","articleBody":"{body}"}}'
        "</script></head><body><div>short</div></body></html>"
    )
    assert "long schema article" in parse(html).content


def test_table_layout_page_selects_main_cell() -> None:
    html = (
        '<html><body><table width="800"><tr><td width="100">menu</td>'
        f"<td>{_ARTICLE}</td></tr></table></body></html>"
    )
    assert "committee reviewed" in parse(html).content


def test_content_selector_overrides_detection() -> None:
    html = (
        f'<html><body><article>{_ARTICLE}</article><div id="x"><p>Chosen.</p></div></body></html>'
    )
    assert "Chosen." in parse(html, content_selector="#x").content


def test_invalid_content_selector_falls_back_to_detection() -> None:
    html = f"<html><body><article>{_ARTICLE}</article></body></html>"
    assert "committee reviewed" in parse(html, content_selector="[[").content


def test_resolves_relative_urls() -> None:
    html = (
        f'<html><body><article>{_ARTICLE}<a href="/next">next page link</a>'
        '<img src="pic.png" alt="chart" width="400"></article></body></html>'
    )
    result = parse(html, url="https://example.com/dir/post")
    assert 'href="https://example.com/next"' in result.content
    assert 'src="https://example.com/dir/pic.png"' in result.content


def test_removes_small_images_and_keeps_large_ones() -> None:
    html = (
        f'<html><body><article>{_ARTICLE}<img src="/icon.png" width="16" height="16">'
        '<img src="/photo.png" width="600" height="400"></article></body></html>'
    )
    result = parse(html, url="https://example.com/")
    assert "icon.png" not in result.content
    assert "photo.png" in result.content


def test_input_document_is_not_mutated_between_retries() -> None:
    html = "<html><body><div><p>tiny</p></div></body></html>"
    first = parse(html)
    second = parse(html)
    assert first.content == second.content


@pytest.mark.parametrize(
    ("html", "field", "expected"),
    [
        ('<meta property="og:title" content="Og Title">', "title", "Og Title"),
        ("<title>A Longer Post Name | Example</title>", "title", "A Longer Post Name"),
        ('<meta name="author" content="By Ann Lee and Bo Kim">', "author", "Ann Lee, Bo Kim"),
        (
            '<meta property="article:published_time" content="2024-01-02T00:00:00Z">',
            "published",
            "2024-01-02T00:00:00Z",
        ),
        ('<meta property="og:site_name" content="Daily News">', "site", "Daily News"),
    ],
)
def test_reads_metadata(html: str, field: str, expected: str) -> None:
    result = parse(f"<html><head>{html}</head><body><p>x</p></body></html>")
    assert getattr(result, field) == expected


def test_reads_author_from_json_ld_graph() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@graph":[{"@type":"Article","author":{"name":"Rae Park"}}]}'
        "</script></head><body><p>x</p></body></html>"
    )
    assert parse(html).author == "Rae Park"


def test_domain_comes_from_url() -> None:
    assert parse("<p>x</p>", url="https://www.example.com/a").domain == "example.com"


def test_empty_input_gives_empty_result() -> None:
    result = parse("")
    assert result.word_count == 0
    assert result.title == ""
