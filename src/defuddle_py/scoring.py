"""Content scoring ported from ``src/removals/scoring.ts`` of Defuddle."""

import re
from dataclasses import dataclass
from typing import Final

from bs4 import BeautifulSoup, Tag

from defuddle_py import dom
from defuddle_py.constants import (
    BLOCK_ELEMENTS_SELECTOR,
    FOOTNOTE_INLINE_REFERENCES,
    FOOTNOTE_LIST_SELECTORS,
)
from defuddle_py.text import bounded, collapse_whitespace, count_words

_CONTENT_INDICATORS: Final = (
    "admonition",
    "article",
    "content",
    "entry",
    "image",
    "img",
    "font",
    "figure",
    "figcaption",
    "pre",
    "main",
    "post",
    "story",
    "table",
)
_NAVIGATION_INDICATORS: Final = (
    "advertisement",
    "all rights reserved",
    "banner",
    "cookie",
    "comments",
    "copyright",
    "follow me",
    "follow us",
    "footer",
    "header",
    "homepage",
    "login",
    "menu",
    "more articles",
    "more like this",
    "most read",
    "nav",
    "navigation",
    "newsletter",
    "popular",
    "privacy",
    "recommended",
    "register",
    "related",
    "responses",
    "share",
    "sidebar",
    "sign in",
    "sign up",
    "signup",
    "social",
    "sponsored",
    "subscribe",
    "terms",
    "trending",
)
_NON_CONTENT_PATTERNS: Final = (
    "advert",
    "ad-",
    "ads",
    "banner",
    "cookie",
    "copyright",
    "footer",
    "header",
    "homepage",
    "menu",
    "nav",
    "newsletter",
    "popular",
    "privacy",
    "recommended",
    "related",
    "rights",
    "share",
    "sidebar",
    "social",
    "sponsored",
    "subscribe",
    "terms",
    "trending",
    "widget",
)
_MONTHS: Final = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_SOCIAL_PROFILE: Final = re.compile(
    r"\b(linkedin\.com/(in|company)/|twitter\.com/(?!intent\b)\w|x\.com/(?!intent\b)\w"
    r"|facebook\.com/(?!share\b)\w|instagram\.com/\w|threads\.net/\w|mastodon\.\w)",
    re.IGNORECASE,
)
_DATE: Final = re.compile(
    rf"(?:(?:{_MONTHS})[a-z]*\s+\d{{1,2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*)",
    re.IGNORECASE,
)
_BYLINE: Final = re.compile(r"\bBy\s+[A-Z]")
_NAVIGATION_REGEXES: Final = tuple(
    re.compile(r"\b" + indicator.replace(" ", r"\s+") + r"\b")
    for indicator in _NAVIGATION_INDICATORS
)
_NAVIGATION_HEADING: Final = re.compile(
    "|".join(indicator.replace(" ", r"\s+") for indicator in _NAVIGATION_INDICATORS),
    re.IGNORECASE,
)
_TOC_HEADING: Final = re.compile(
    r"^(?:table of )?contents$|^on this page$|^in this (?:article|guide|post)$", re.IGNORECASE
)
_CONTENT_DATE: Final = re.compile(
    rf"\b(?:(?:{_MONTHS})[a-z]*\s+\d{{1,2}},?\s+\d{{4}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*,?\s+\d{{4}})\b",
    re.IGNORECASE,
)
_CONTENT_AUTHOR: Final = re.compile(r"\b(?:by|written by|author:)\s+[A-Za-z\s]+\b", re.IGNORECASE)
_SENTENCE_END: Final = re.compile(r"[.?!]")
_HEADINGS: Final = "h1, h2, h3, h4, h5, h6"
_LAYOUT_TABLE_MIN_WIDTH: Final = 400
_MAX_LINK_DENSITY: Final = 0.5
_DEFAULT_MIN_SCORE: Final = 50


@dataclass(frozen=True, slots=True)
class _TextFacts:
    text: str
    collapsed: str
    words: int


def _facts(element: Tag) -> _TextFacts:
    text = dom.text_of(element)
    return _TextFacts(text=text, collapsed=collapse_whitespace(text), words=count_words(text))


_MAX_LEADING_DIGITS: Final = 18


def parse_leading_int(value: str) -> int:
    """Parse a leading integer the way JavaScript ``parseInt`` does, 0 if absent."""
    match = re.match(rf"\s*[+-]?\d{{1,{_MAX_LEADING_DIGITS}}}", value)
    return int(match.group()) if match else 0


def _table_cell_bonus(element: Tag) -> int:
    table = dom.closest(element, "table")
    if table is None:
        return 0
    table_class = dom.class_name(table).lower()
    is_layout = (
        parse_leading_int(dom.attr_or_empty(table, "width")) > _LAYOUT_TABLE_MIN_WIDTH
        or dom.attr_or_empty(table, "align") == "center"
        or "content" in table_class
        or "article" in table_class
    )
    if not is_layout:
        return 0
    cells = dom.by_tag(table, "td")
    index = next((i for i, cell in enumerate(cells) if cell is element), -1)
    return 10 if 0 < index < len(cells) - 1 else 0


def score_element(element: Tag) -> float:
    """Score how much ``element`` looks like the article body."""
    facts = _facts(element)
    score: float = facts.words
    score += dom.count_tag(element, "p") * 10
    score += facts.text.count(",")
    images = dom.count_tag(element, "img")
    score -= images / (facts.words or 1) * 3

    style = dom.attr_or_empty(element, "style")
    if (
        "float: right" in style
        or "text-align: right" in style
        or dom.attr_or_empty(element, "align") == "right"
    ):
        score += 5

    if _CONTENT_DATE.search(facts.collapsed):
        score += 10
    if _CONTENT_AUTHOR.search(facts.collapsed):
        score += 10

    class_name = dom.class_name(element).lower()
    if "content" in class_name or "article" in class_name or "post" in class_name:
        score += 15
    if dom.has(element, FOOTNOTE_INLINE_REFERENCES):
        score += 10
    if dom.has(element, FOOTNOTE_LIST_SELECTORS):
        score += 10
    score -= dom.count_tag(element, "table") * 5
    if element.name == "td":
        score += _table_cell_bonus(element)

    link_text = sum(len(dom.text_of(link)) for link in dom.by_tag(element, "a"))
    link_density = min(link_text / (len(facts.text) or 1), _MAX_LINK_DENSITY)
    return score * (1 - link_density)


def find_best_element(elements: list[Tag], min_score: float = _DEFAULT_MIN_SCORE) -> Tag | None:
    """Return the highest scoring element when it clears ``min_score``."""
    best: Tag | None = None
    best_score: float = 0
    for element in elements:
        score = score_element(element)
        if score > best_score:
            best_score = score
            best = element
    return best if best_score > min_score else None


def _is_card_grid(element: Tag, words: int) -> bool:
    if words < 3 or words >= 500:
        return False
    headings = dom.select(element, "h2, h3, h4")
    if len(headings) < 3 or dom.count_tag(element, "img") < 2:
        return False
    heading_words = sum(count_words(dom.text_of(heading)) for heading in headings)
    return (words - heading_words) / len(headings) < 20


def _has_social_link(element: Tag) -> bool:
    return any(
        _SOCIAL_PROFILE.search(bounded(dom.attr_or_empty(link, "href").lower()))
        for link in dom.by_tag(element, "a")
    )


def _heading_signals_content(element: Tag, text: str) -> bool:
    heading = dom.select_one(element, _HEADINGS)
    if heading is None:
        return False
    heading_text = dom.trimmed_text(heading)
    if not heading_text or heading_text != text.strip():
        return False
    lowered = bounded(heading_text.lower())
    return not _NAVIGATION_HEADING.search(lowered) and not _TOC_HEADING.search(lowered)


def _navigation_heading_rejects(element: Tag, words: int) -> bool:
    if words >= 1000:
        return False
    has_navigation_heading = any(
        _NAVIGATION_HEADING.search(bounded(dom.trimmed_text(heading).lower()))
        for heading in dom.select(element, _HEADINGS)
    )
    if not has_navigation_heading:
        return False
    if words < 200:
        return True
    return dom.count_tag(element, "a") / (words or 1) > 0.2


def _is_likely_content(element: Tag, facts: _TextFacts) -> bool:
    if dom.attr_or_empty(element, "role") in ("article", "main", "contentinfo"):
        return True
    class_name = dom.class_name(element).lower()
    element_id = dom.element_id(element).lower()
    if any(mark in class_name or mark in element_id for mark in _CONTENT_INDICATORS):
        return True
    if dom.has(element, "pre, table, figure, picture"):
        return True

    words = facts.words
    if _heading_signals_content(element, facts.text):
        return True
    if _navigation_heading_rejects(element, words):
        return False
    if _is_card_grid(element, words):
        return False
    if words < 80 and _has_social_link(element):
        return False

    content_blocks = dom.count_tag(element, "p", "li")
    if (words > 50 and content_blocks > 1) or words > 100 or (words > 30 and content_blocks > 0):
        return True
    if words >= 10 and _SENTENCE_END.search(facts.text):
        return dom.count_tag(element, "a") / words < 0.1
    return False


def _score_non_content_block(element: Tag, facts: _TextFacts) -> int:
    if (
        dom.matches(element, FOOTNOTE_LIST_SELECTORS)
        or dom.has(element, FOOTNOTE_LIST_SELECTORS)
        or dom.closest(element, FOOTNOTE_LIST_SELECTORS) is not None
    ):
        return 0
    words = facts.words
    if words < 3:
        return 0

    score = facts.text.count(",")
    lowered = facts.collapsed.lower()
    score -= sum(10 for regex in _NAVIGATION_REGEXES if regex.search(lowered))

    link_elements = dom.by_tag(element, "a")
    links = len(link_elements)
    if links / (words or 1) > 0.5:
        score -= 15
    if links > 1 and words < 80:
        link_text = sum(len(dom.text_of(link)) for link in link_elements)
        if facts.text and link_text / len(facts.text) > 0.8:
            score -= 15
    lists = dom.count_tag(element, "ul", "ol")
    if lists > 0 and links > lists * 3:
        score -= 10
    if words < 80 and _has_social_link(element):
        score -= 15
    if words < 15:
        short_text = bounded(facts.text)
        if _BYLINE.search(short_text) and _DATE.search(short_text):
            score -= 10
    if _is_card_grid(element, words):
        score -= 15

    class_name = dom.class_name(element).lower()
    element_id = dom.element_id(element).lower()
    score -= sum(8 for mark in _NON_CONTENT_PATTERNS if mark in class_name or mark in element_id)
    return score


def score_and_remove(document: BeautifulSoup, main_content: Tag | None) -> None:
    """Remove block elements that score as navigation or other non-content."""
    marked: list[Tag] = []
    for element in dom.select(document, BLOCK_ELEMENTS_SELECTOR):
        if main_content is not None and dom.contains(element, main_content):
            continue
        if dom.closest(element, "pre, td, th, [data-defuddle]") is not None:
            continue
        facts = _facts(element)
        if _is_likely_content(element, facts):
            continue
        if _score_non_content_block(element, facts) < 0:
            marked.append(element)
    for element in marked:
        dom.remove(element)
