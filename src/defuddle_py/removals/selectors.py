"""Selector based removal ported from ``src/removals/selectors.ts`` of Defuddle."""

import re
from typing import Final

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from defuddle_py import dom
from defuddle_py.constants import (
    EXACT_SELECTORS_JOINED,
    FOOTNOTE_LIST_SELECTORS,
    HIDDEN_EXACT_SELECTOR,
    HIDDEN_EXACT_SKIP_SELECTOR,
    PARTIAL_SELECTORS_ANCHORED_REGEX,
    PARTIAL_SELECTORS_REGEX,
    TEST_ATTRIBUTES_SELECTOR,
)
from defuddle_py.removals.hidden import has_responsive_show_class
from defuddle_py.text import bounded

_SCREEN_READER_LINK_TEXT: Final = ", ".join(
    f'a [class*="{name}"]' for name in ("sr-only", "visually-hidden", "screen-reader-text")
)
_LINK_ANNOTATION: Final = re.compile(
    r"^[\s,(–-]*(?:(?:link )?opens in (?:a )?new (?:window|tab)|new (?:window|tab)"
    r"|external(?: link| site| website)?)[\s).]*$",
    re.IGNORECASE,
)
_ID_DELIMITER: Final = re.compile(r"[\s_\-:.]")
_HEADING_TAG: Final = re.compile(r"^h[1-6]$")
_HEADINGS: Final = "h1, h2, h3, h4, h5, h6"
_MEDIA: Final = "img, picture, video"
_PARTIAL_ATTRIBUTES: Final = (
    "data-component",
    "data-test",
    "data-testid",
    "data-test-id",
    "data-qa",
    "data-cy",
)


def _skip_exact(element: Tag, *, skip_hidden: bool) -> bool:
    if skip_hidden:
        role = dom.attr_or_empty(element, "role").lower()
        if dom.matches(element, HIDDEN_EXACT_SELECTOR) or (
            role == "dialog" and dom.closest(element, HIDDEN_EXACT_SKIP_SELECTOR) is not None
        ):
            return True
    if dom.closest(element, "pre, code") is not None:
        return True
    return dom.matches(element, HIDDEN_EXACT_SELECTOR) and has_responsive_show_class(
        dom.class_name(element)
    )


def _partial_match(element: Tag) -> bool:
    is_heading = _HEADING_TAG.match(element.name) is not None
    parts = [dom.class_name(element)]
    if not is_heading:
        parts.extend(dom.attr_or_empty(element, name) for name in _PARTIAL_ATTRIBUTES)
    attributes = bounded(" ".join(parts).lower())
    element_id = "" if is_heading else bounded(dom.element_id(element).lower())
    has_attributes = attributes.strip() != ""
    if not has_attributes and not element_id:
        return False
    if has_attributes and PARTIAL_SELECTORS_REGEX.search(attributes):
        return True
    if not element_id:
        return False
    if _ID_DELIMITER.search(element_id):
        return PARTIAL_SELECTORS_REGEX.search(element_id) is not None
    return PARTIAL_SELECTORS_ANCHORED_REGEX.search(element_id) is not None


def _is_code_structure(element: Tag) -> bool:
    return (
        element.name in ("code", "pre")
        or dom.has(element, "pre")
        or dom.closest(element, "code, pre") is not None
    )


def _has_visible_text(node: Tag, excluded: dom.ElementSet) -> bool:
    for child in node.children:
        if isinstance(child, Tag):
            if child not in excluded and _has_visible_text(child, excluded):
                return True
        elif isinstance(child, NavigableString) and child.strip():
            return True
    return False


def _collect_partial(
    document: BeautifulSoup, main_content: Tag | None, marked: dom.ElementSet
) -> None:
    elements = dom.ElementSet(dom.select(document, TEST_ATTRIBUTES_SELECTOR))
    if main_content is not None:
        for element in dom.select(main_content, TEST_ATTRIBUTES_SELECTOR):
            elements.add(element)
    for element in elements:
        if element in marked or dom.closest(element, "[data-defuddle]") is not None:
            continue
        if not _is_code_structure(element) and _partial_match(element):
            marked.add(element)

    annotations = dom.ElementSet(dom.select(document, _SCREEN_READER_LINK_TEXT))
    excluded = dom.ElementSet([*annotations, *marked])
    for element in annotations:
        link = dom.closest(element, "a")
        if (
            link is None
            or element in marked
            or dom.closest(element, "code, pre, [data-defuddle]") is not None
            or not _LINK_ANNOTATION.search(bounded(dom.text_of(element)))
            or not _has_visible_text(link, excluded)
        ):
            continue
        marked.add(element)


def _is_protected(element: Tag, main_content: Tag | None) -> bool:
    if main_content is not None and dom.contains(element, main_content):
        return True
    if element.name == "a" and dom.closest(element, _HEADINGS) is not None:
        return True
    if dom.matches(element, FOOTNOTE_LIST_SELECTORS) or dom.has(element, FOOTNOTE_LIST_SELECTORS):
        return True
    parent = dom.parent_element(element)
    if parent is not None and dom.matches(parent, FOOTNOTE_LIST_SELECTORS):
        return True
    return (
        "footnote-backref" in dom.class_name(element).split()
        and dom.closest(element, "#footnotes") is not None
    )


def _remove_button(element: Tag) -> bool:
    media = dom.select(element, _MEDIA)
    parent = dom.parent_element(element)
    if media:
        if parent is not None:
            for item in media:
                element.insert_before(item.extract())
            dom.remove(element)
        return True
    if dom.closest(element, "p, li, td, th, span, h1, h2, h3, h4, h5, h6") is not None:
        dom.unwrap(element)
        return True
    return False


def remove_by_selector(
    document: BeautifulSoup,
    *,
    remove_exact: bool,
    remove_partial: bool,
    main_content: Tag | None,
    skip_hidden_exact: bool,
) -> None:
    """Remove known clutter matched by exact selectors and class fragments."""
    marked = dom.ElementSet()
    if remove_exact:
        for element in dom.select(document, EXACT_SELECTORS_JOINED):
            if not _skip_exact(element, skip_hidden=skip_hidden_exact):
                marked.add(element)
    if remove_partial:
        _collect_partial(document, main_content, marked)

    for element in marked:
        if _is_protected(element, main_content):
            continue
        if element.name == "button" and _remove_button(element):
            continue
        dom.remove(element)
