"""Output sanitization ported from ``src/utils/dom.ts`` of Defuddle.

This is the boundary that every extraction path passes through. It runs whatever
pipeline options are set.
"""

import re
from typing import Final

from bs4 import Tag
from bs4.element import PreformattedString

from defuddle_py import dom

_UNSAFE_TAGS: Final = frozenset(
    {
        "style",
        "noscript",
        "template",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "base",
        "meta",
        "link",
        "animate",
        "set",
        "animatemotion",
        "animatetransform",
        "animatecolor",
        "discard",
    }
)
_URL_ATTRIBUTES: Final = frozenset({"href", "src", "action", "formaction", "xlink:href"})
_MATH_SCRIPT_TYPE_PREFIX: Final = "math/"
_HTML_NAMESPACE: Final = "http://www.w3.org/1999/xhtml"
_SCRIPT_BREAKOUT: Final = re.compile(r"<(?:!--|/?script)", re.IGNORECASE)
_DANGEROUS_SCHEMES: Final = ("javascript:", "blob:", "vbscript:")
_URL_NOISE: Final = re.compile(r"[\s\u0000-\u001f]+")


def is_dangerous_url(url: str, *, allow_inline_image: bool = True) -> bool:
    """Return whether ``url`` uses a scheme that can carry active content.

    Whitespace and control characters are dropped first because browsers ignore
    them inside a scheme. Inline images are the one ``data:`` form allowed.
    """
    normalized = _URL_NOISE.sub("", url).lower()
    if normalized.startswith(_DANGEROUS_SCHEMES):
        return True
    if normalized.startswith("data:"):
        return not (allow_inline_image and normalized.startswith("data:image/"))
    return False


def _has_foreign_ancestor(element: Tag) -> bool:
    return any(parent.namespace not in (None, _HTML_NAMESPACE) for parent in element.parents)


def _is_inert_math_script(element: Tag) -> bool:
    # Script text is written without escaping, so a browser reads it differently from
    # the tree here. Under foreign content a later reparse can treat that text as markup,
    # for example once an integration point loses its attribute, and these sequences
    # can end the element early or change where it ends in any context.
    if element.namespace != _HTML_NAMESPACE or _has_foreign_ancestor(element):
        return False
    script_type = dom.attr_or_empty(element, "type").lower()
    if not script_type.startswith(_MATH_SCRIPT_TYPE_PREFIX):
        return False
    return _SCRIPT_BREAKOUT.search(element.decode_contents()) is None


def _is_unsafe_element(element: Tag) -> bool:
    name = element.name.lower()
    if name == "script":
        return not _is_inert_math_script(element)
    return name in _UNSAFE_TAGS


def _strip_attributes(element: Tag) -> None:
    is_iframe = element.name.lower() == "iframe"
    for name in list(element.attrs):
        lowered = name.lower()
        if lowered.startswith("on") or lowered == "srcdoc":
            del element.attrs[name]
        elif lowered in _URL_ATTRIBUTES:
            allow_inline_image = not (lowered == "src" and is_iframe)
            value = dom.attr_or_empty(element, name)
            if is_dangerous_url(value, allow_inline_image=allow_inline_image):
                del element.attrs[name]


def strip_unsafe_elements(root: Tag) -> bool:
    """Remove active elements and attributes below and on ``root``.

    Returns:
        True when ``root`` itself is unsafe and must not be serialized.
    """
    unsafe_root = _is_unsafe_element(root)
    if unsafe_root:
        root.attrs.clear()
        root.clear()
    for node in list(root.descendants):
        # Comments and declarations are written verbatim, never escaped.
        if isinstance(node, PreformattedString):
            node.extract()
        elif isinstance(node, Tag) and node.parent is not None and _is_unsafe_element(node):
            dom.remove(node)
    _strip_attributes(root)
    for node in root.descendants:
        if isinstance(node, Tag):
            _strip_attributes(node)
    return unsafe_root
