"""DOM helpers over BeautifulSoup, mirroring the browser calls Defuddle relies on.

Tags compare and hash by content, so identity must always be tested with ``is``
or ``id()``. Every helper here follows that rule.
"""

from collections.abc import Iterable, Iterator
from functools import lru_cache
from typing import Final

import soupsieve
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PreformattedString
from soupsieve import SoupSieve

from defuddle_py.errors import TreeTooDeepError

_HTML_PARSER: Final = "html5lib"
_VOID_TAGS: Final = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
# End tags for these may be omitted, so counting them would overstate nesting.
_IMPLIED_END_TAGS: Final = frozenset(
    [
        "p",
        "li",
        "dt",
        "dd",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "tfoot",
        "colgroup",
        "option",
        "optgroup",
        "rt",
        "rp",
    ]
)
_RAW_TEXT_TAGS: Final = ("script", "style", "textarea", "title")
_TAG_NAME_START: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_TAG_NAME_END: Final = " \t\r\n\f/>"
_COMMENT_START: Final = "<!--"
_COMMENT_END: Final = "-->"
_MAX_TAG_NAME_CHARS: Final = 64
HEADING_NAMES: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


@lru_cache(maxsize=512)
def _compiled(selector: str) -> SoupSieve:
    return soupsieve.compile(selector)


def _read_tag_name(html: str, start: int) -> str:
    end = start
    limit = min(len(html), start + _MAX_TAG_NAME_CHARS)
    while end < limit and html[end] not in _TAG_NAME_END:
        end += 1
    return html[start:end].lower()


def _skip_raw_text(html: str, name: str, position: int) -> int:
    index = position
    needle = "</"
    while True:
        index = html.find(needle, index)
        if index < 0:
            return len(html)
        if html[index + 2 : index + 2 + len(name)].lower() == name:
            return index
        index += 2


def check_source_depth(html: str, max_depth: int) -> None:
    """Reject deep nesting before parsing, in linear time.

    html5lib rescans its open-element stack for each start tag, so a document of
    unclosed elements costs quadratic time. Checking first stops that input before
    the parser sees it.

    Raises:
        TreeTooDeepError: If open elements exceed ``max_depth``.
    """
    depth = 0
    position = 0
    while True:
        position = html.find("<", position)
        if position < 0:
            return
        if html.startswith(_COMMENT_START, position):
            end = html.find(_COMMENT_END, position + len(_COMMENT_START))
            if end < 0:
                return
            position = end + len(_COMMENT_END)
            continue
        closing = html.startswith("</", position)
        name_start = position + (2 if closing else 1)
        if name_start >= len(html) or html[name_start] not in _TAG_NAME_START:
            position += 1
            continue
        name = _read_tag_name(html, name_start)
        tag_end = html.find(">", name_start)
        if tag_end < 0:
            return
        position = tag_end + 1
        if name in _VOID_TAGS or name in _IMPLIED_END_TAGS:
            continue
        if closing:
            depth = max(0, depth - 1)
            continue
        if html[tag_end - 1] == "/":
            continue
        depth += 1
        if depth > max_depth:
            raise TreeTooDeepError(f"element nesting exceeds {max_depth}")
        if name in _RAW_TEXT_TAGS:
            position = _skip_raw_text(html, name, position)


def _check_tree_depth(document: BeautifulSoup, max_depth: int) -> None:
    stack: list[tuple[Tag, int]] = [(document, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise TreeTooDeepError(f"element nesting exceeds {max_depth}")
        stack.extend((child, depth + 1) for child in node.children if isinstance(child, Tag))


def parse_document(html: str, max_depth: int) -> BeautifulSoup:
    """Parse untrusted HTML the way a browser would.

    Raises:
        TreeTooDeepError: If the document nests deeper than ``max_depth``.
    """
    check_source_depth(html, max_depth)
    document = BeautifulSoup(html, _HTML_PARSER, multi_valued_attributes=None)
    _check_tree_depth(document, max_depth)
    return document


def parse_fragment(html: str) -> Tag:
    """Parse an HTML fragment and return the ``<body>`` holding its nodes."""
    document = BeautifulSoup(html, _HTML_PARSER, multi_valued_attributes=None)
    body = document.find("body")
    return body if isinstance(body, Tag) else document


def select(root: Tag, selector: str) -> list[Tag]:
    """Return descendants of ``root`` matching ``selector`` in document order."""
    return list(_compiled(selector).select(root))


def select_one(root: Tag, selector: str) -> Tag | None:
    """Return the first descendant of ``root`` matching ``selector``."""
    found = _compiled(selector).select(root, limit=1)
    return found[0] if found else None


def has(root: Tag, selector: str) -> bool:
    """Return whether any descendant of ``root`` matches ``selector``."""
    return select_one(root, selector) is not None


def matches(element: Tag, selector: str) -> bool:
    """Return whether ``element`` itself matches ``selector``."""
    return _compiled(selector).match(element)


def closest(element: Tag, selector: str) -> Tag | None:
    """Return ``element`` or its nearest ancestor matching ``selector``."""
    pattern = _compiled(selector)
    current: Tag | None = element
    while current is not None and not isinstance(current, BeautifulSoup):
        if pattern.match(current):
            return current
        current = current.parent
    return None


def by_tag(root: Tag, *names: str) -> list[Tag]:
    """Return descendants named one of ``names``, like ``getElementsByTagName``."""
    wanted = frozenset(names)
    return [node for node in root.descendants if isinstance(node, Tag) and node.name in wanted]


def count_tag(root: Tag, *names: str) -> int:
    """Count descendants named one of ``names`` without building a list."""
    wanted = frozenset(names)
    return sum(1 for node in root.descendants if isinstance(node, Tag) and node.name in wanted)


def is_text_node(node: object) -> bool:
    """Return whether ``node`` is text, as opposed to a comment or a declaration."""
    return isinstance(node, NavigableString) and not isinstance(node, PreformattedString)


def text_of(node: Tag) -> str:
    """Return the DOM ``textContent`` of ``node``, script and style text included."""
    return "".join(str(child) for child in node.descendants if is_text_node(child))


def trimmed_text(node: Tag) -> str:
    """Return ``textContent`` with surrounding whitespace removed."""
    return text_of(node).strip()


def attr(element: Tag, name: str) -> str | None:
    """Return an attribute as a string, or None when it is absent."""
    value = element.attrs.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else " ".join(value)


def attr_or_empty(element: Tag, name: str) -> str:
    """Return an attribute as a string, or an empty string when it is absent."""
    return attr(element, name) or ""


def class_name(element: Tag) -> str:
    """Return the raw ``class`` attribute."""
    return attr_or_empty(element, "class")


def element_id(element: Tag) -> str:
    """Return the raw ``id`` attribute."""
    return attr_or_empty(element, "id")


def parent_element(element: Tag) -> Tag | None:
    """Return the parent element, treating the document node as no parent."""
    parent = element.parent
    return None if parent is None or isinstance(parent, BeautifulSoup) else parent


def child_elements(element: Tag) -> list[Tag]:
    """Return the direct element children."""
    return [child for child in element.children if isinstance(child, Tag)]


def next_element_sibling(element: Tag) -> Tag | None:
    """Return the next sibling that is an element."""
    sibling = element.next_sibling
    while sibling is not None and not isinstance(sibling, Tag):
        sibling = sibling.next_sibling
    return sibling


def previous_element_sibling(element: Tag) -> Tag | None:
    """Return the previous sibling that is an element."""
    sibling = element.previous_sibling
    while sibling is not None and not isinstance(sibling, Tag):
        sibling = sibling.previous_sibling
    return sibling


def last_element_child(element: Tag) -> Tag | None:
    """Return the last child that is an element."""
    children = child_elements(element)
    return children[-1] if children else None


def is_connected(element: Tag) -> bool:
    """Return whether the node still has a parent."""
    return element.parent is not None


def contains(ancestor: Tag, element: Tag) -> bool:
    """Return whether ``element`` is ``ancestor`` or sits inside it."""
    current: Tag | None = element
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def remove(element: Tag) -> None:
    """Detach ``element`` from the tree."""
    element.extract()


def unwrap(element: Tag) -> None:
    """Replace ``element`` with its children."""
    element.unwrap()


def _path_to_root(node: Tag) -> list[Tag]:
    path: list[Tag] = []
    current: Tag | None = node
    while current is not None:
        path.append(current)
        current = current.parent
    return path


def is_node_before(node: Tag, other: Tag) -> bool:
    """Return whether ``node`` precedes ``other`` in document order."""
    if node is other:
        return False
    left = _path_to_root(node)
    right = _path_to_root(other)
    if left[-1] is not right[-1]:
        return False
    while left and right and left[-1] is right[-1]:
        left.pop()
        right.pop()
    if not left:
        return True
    if not right:
        return False
    target = right[-1]
    sibling = left[-1].next_sibling
    while sibling is not None:
        if sibling is target:
            return True
        sibling = sibling.next_sibling
    return False


def iter_elements_after(root: Tag, start: Tag | None) -> Iterator[Tag]:
    """Yield elements under ``root`` in document order, after ``start`` if given."""
    seen_start = start is None
    for node in root.descendants:
        if not isinstance(node, Tag):
            continue
        if seen_start:
            yield node
        elif node is start:
            seen_start = True


class ElementSet:
    """A set of tags keyed by identity, since tags hash by content."""

    def __init__(self, items: Iterable[Tag] = ()) -> None:
        self._items: dict[int, Tag] = {id(item): item for item in items}

    def add(self, item: Tag) -> None:
        """Add ``item``."""
        self._items[id(item)] = item

    def __contains__(self, item: Tag) -> bool:
        return id(item) in self._items

    def __iter__(self) -> Iterator[Tag]:
        return iter(list(self._items.values()))

    def __len__(self) -> int:
        return len(self._items)
