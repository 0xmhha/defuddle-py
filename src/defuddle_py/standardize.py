"""The cleanup subset of ``src/standardize.ts`` that Markdown output depends on.

Footnote, math, code and callout standardization stay out of scope. This module keeps
only the steps that decide which text survives: heading normalization, attribute
stripping, link repair and removal of empty elements.
"""

from typing import Final

from bs4 import Comment, Tag
from bs4.element import NavigableString

from defuddle_py import dom
from defuddle_py.constants import ALLOWED_ATTRIBUTES, ALLOWED_EMPTY_ELEMENTS
from defuddle_py.text import normalize_text

_NBSP: Final = " "
_HEADINGS: Final = "h1, h2, h3, h4, h5, h6"


def _replace_non_breaking_spaces(root: Tag) -> None:
    for node in list(root.descendants):
        if isinstance(node, NavigableString) and _NBSP in node:
            parents = (parent.name for parent in node.parents)
            if not any(name in ("pre", "code", "svg") for name in parents):
                node.replace_with(NavigableString(node.replace(_NBSP, " ")))


def _remove_comments(root: Tag) -> None:
    for node in list(root.descendants):
        if isinstance(node, Comment):
            node.extract()


def _standardize_headings(root: Tag, title: str) -> None:
    for heading in dom.by_tag(root, "h1"):
        heading.name = "h2"
    second_level = dom.by_tag(root, "h2")
    normalized_title = normalize_text(title)
    if (
        second_level
        and normalized_title
        and normalize_text(dom.text_of(second_level[0])) == normalized_title
    ):
        dom.remove(second_level[0])


def _strip_attributes(root: Tag) -> None:
    for element in [root, *(node for node in root.descendants if isinstance(node, Tag))]:
        if dom.closest(element, "svg") is not None:
            element.attrs.pop("class", None)
            continue
        for name in list(element.attrs):
            value = dom.attr_or_empty(element, name)
            keep_id = name == "id" and (value.startswith(("fnref:", "fn:")) or value == "footnotes")
            keep_class = (
                name == "class" and element.name == "code" and value.startswith("language-")
            )
            if not keep_id and not keep_class and name.lower() not in ALLOWED_ATTRIBUTES:
                del element.attrs[name]


def _repair_links(root: Tag) -> None:
    for link in dom.select(root, "code a"):
        dom.unwrap(link)
    for link in dom.by_tag(root, "a"):
        href = dom.attr(link, "href")
        if not href or href.startswith("#"):
            continue
        heading = next(
            (child for child in dom.child_elements(link) if child.name in dom.HEADING_NAMES), None
        )
        if heading is None:
            continue
        inner = Tag(name="a", attrs={"href": href})
        for child in list(heading.contents):
            inner.append(child.extract())
        heading.append(inner)
        dom.unwrap(link)
    for link in dom.select(root, 'a[href^="#"]'):
        if dom.has(link, _HEADINGS):
            dom.unwrap(link)


def _is_empty(element: Tag) -> bool:
    if element.name.lower() in ALLOWED_EMPTY_ELEMENTS:
        return False
    children = dom.child_elements(element)
    if (
        element.name == "div"
        and children
        and all(child.name == "span" and dom.trimmed_text(child) in (",", "") for child in children)
    ):
        return True
    text = dom.text_of(element)
    if text.strip() or _NBSP in text:
        return False
    return all(child.name == "br" for child in children)


def _remove_empty_elements(root: Tag) -> None:
    for element in reversed([node for node in root.descendants if isinstance(node, Tag)]):
        if element.parent is not None and _is_empty(element):
            dom.remove(element)


def _has_content_after(element: Tag, root: Tag) -> bool:
    current = element
    while True:
        for sibling in current.next_siblings:
            text = dom.text_of(sibling) if isinstance(sibling, Tag) else str(sibling)
            if text.strip():
                return True
        parent = dom.parent_element(current)
        if parent is None or parent is root:
            return False
        current = parent


def _remove_trailing_headings(root: Tag) -> None:
    for heading in reversed(dom.select(root, _HEADINGS)):
        if _has_content_after(heading, root):
            return
        dom.remove(heading)


def _edge_tag(root: Tag, *, last: bool) -> Tag | None:
    children = reversed(root.contents) if last else iter(root.contents)
    for child in children:
        if isinstance(child, Tag):
            return child
        if str(child).strip():
            return None
    return None


def _remove_orphaned_dividers(root: Tag) -> None:
    for last in (False, True):
        edge = _edge_tag(root, last=last)
        while edge is not None and edge.name == "hr":
            dom.remove(edge)
            edge = _edge_tag(root, last=last)
    for divider in dom.by_tag(root, "hr"):
        if divider.parent is None:
            continue
        node = divider.next_sibling
        while node is not None:
            following = node.next_sibling
            if isinstance(node, Tag):
                if node.name != "hr":
                    break
                dom.remove(node)
            elif str(node).strip():
                break
            node = following


def standardize_content(root: Tag, title: str) -> None:
    """Apply the cleanup that shapes the extracted text."""
    _replace_non_breaking_spaces(root)
    _remove_comments(root)
    _standardize_headings(root, title)
    _strip_attributes(root)
    _repair_links(root)
    _remove_empty_elements(root)
    _remove_trailing_headings(root)
    _remove_orphaned_dividers(root)
