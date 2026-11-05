"""Prose-body detection ported from ``src/content-boundary.ts`` of Defuddle."""

import re
from typing import Final

from bs4 import Tag

from defuddle_py import dom
from defuddle_py.text import bounded, count_words, normalize_text

_MONTHS: Final = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE: Final = re.compile(
    rf"(?:(?:{_MONTHS})[a-z]*\s+\d{{1,2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*"
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.IGNORECASE,
)
_BYLINE: Final = re.compile(r"^by\s+\S", re.IGNORECASE)
_SENTENCE_PUNCTUATION: Final = re.compile(r"[.!?]")
_HIDDEN_CLASS: Final = re.compile(r"\b(?:isHidden(?:-[A-Za-z0-9_]+)?|is-hidden)\b")
_CANDIDATE_TAGS: Final = frozenset({"p", "div", "section", "article", "blockquote", "font"})
_LEAF_CANDIDATE_TAGS: Final = frozenset({"p", "blockquote", "font"})
_DIALOG_SELECTOR: Final = '[role="dialog"], [role="alertdialog"]'
_SKIP_ANCESTOR_SELECTOR: Final = f"aside, nav, header, footer, form, {_DIALOG_SELECTOR}"
_PROSE_MIN_WORDS: Final = 7


def _find_title_element(main_content: Tag, title: str) -> Tag | None:
    normalized = normalize_text(title)
    if not normalized:
        return None
    for heading in dom.select(main_content, "h1, h2"):
        if normalize_text(dom.text_of(heading)) == normalized:
            return heading
    return None


def _is_prose_block(element: Tag) -> bool:
    if element.name not in _CANDIDATE_TAGS:
        return False
    if dom.closest(element, _SKIP_ANCESTOR_SELECTOR) is not None:
        return False
    if _HIDDEN_CLASS.search(bounded(dom.class_name(element))):
        return False
    if dom.has(element, _DIALOG_SELECTOR) or dom.has(element, "script, style"):
        return False

    text = dom.trimmed_text(element)
    if not text:
        return False
    words = count_words(text)
    if words < _PROSE_MIN_WORDS or not _SENTENCE_PUNCTUATION.search(text):
        return False
    short_text = bounded(text)
    if _BYLINE.search(short_text) and words < 15:
        return False
    if _DATE.search(short_text) and words < 20:
        return False
    link_text = sum(len(dom.text_of(link)) for link in dom.by_tag(element, "a"))
    if link_text > len(text) * 0.7:
        return False
    return not (element.name == "div" and not dom.has(element, "p"))


def _drill_down(container: Tag) -> Tag:
    result = container
    while True:
        qualifying = [child for child in dom.child_elements(result) if _is_prose_block(child)]
        if len(qualifying) != 1:
            return result
        result = qualifying[0]


def find_content_start(main_content: Tag, title: str) -> Tag | None:
    """Find the first block of real prose, anchored on the title when present."""
    start = _find_title_element(main_content, title)
    container_hit: Tag | None = None
    for element in dom.iter_elements_after(main_content, start):
        if not _is_prose_block(element):
            continue
        if element.name in _LEAF_CANDIDATE_TAGS:
            return element
        if container_hit is None:
            container_hit = element
    if container_hit is not None:
        return _drill_down(container_hit)
    return find_content_start(main_content, "") if start is not None else None


def is_above_content_start(element: Tag, boundary: Tag | None) -> bool:
    """Return whether ``element`` sits before ``boundary`` in document order."""
    return boundary is not None and dom.is_node_before(element, boundary)
