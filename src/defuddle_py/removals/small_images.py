"""Small image removal ported from ``src/removals/small-images.ts`` of Defuddle.

Layout measurement is unavailable outside a browser, so sizes come from attributes,
inline styles, ``viewBox`` and CDN width hints.
"""

import re
from typing import Final

from bs4 import BeautifulSoup, Tag

from defuddle_py import dom
from defuddle_py.scoring import parse_leading_int
from defuddle_py.text import bounded

MIN_DIMENSION: Final = 33
_MAX_PLACEHOLDER_BASE64_CHARS: Final = 133
_STYLE_WIDTH: Final = re.compile(r"width\s*:\s*(\d+)")
_STYLE_HEIGHT: Final = re.compile(r"height\s*:\s*(\d+)")
URL_WIDTH: Final = re.compile(r"(?:width[=:/]|[/,?&]w[_:=])(\d+)")
_ONE_X_ENTRY: Final = re.compile(r"(\S+)\s+1x")
_BASE64_DATA_URL: Final = re.compile(r"^data:image/([^;]+);base64,")
_LATEX: Final = re.compile(r"\\[a-zA-Z]{2,}")
_MAX_DIGITS: Final = 9


def is_base64_placeholder(src: str) -> bool:
    """Return whether ``src`` is a tiny inline image used as a lazy-load stand-in."""
    match = _BASE64_DATA_URL.search(bounded(src))
    if match is None or match.group(1) == "svg+xml":
        return False
    return len(src) - match.end() < _MAX_PLACEHOLDER_BASE64_CHARS


def _element_identifier(element: Tag) -> str | None:
    if element.name == "img":
        data_src = dom.attr(element, "data-src")
        if data_src:
            return f"src:{data_src}"
        for attribute, prefix in (("src", "src"), ("srcset", "srcset"), ("data-srcset", "srcset")):
            value = dom.attr(element, attribute)
            if value:
                return f"{prefix}:{value}"
    element_id = dom.element_id(element)
    if element_id:
        return f"id:{element_id}"
    view_box = dom.attr_or_empty(element, "viewBox") if element.name == "svg" else ""
    if view_box:
        return f"viewBox:{view_box}"
    class_name = dom.class_name(element)
    return f"class:{class_name}" if class_name else None


def _view_box_size(element: Tag) -> tuple[float, float]:
    if element.name != "svg":
        return 0, 0
    parts = re.split(r"[\s,]+", bounded(dom.attr_or_empty(element, "viewBox")))
    if len(parts) != 4:
        return 0, 0
    try:
        return float(parts[2]), float(parts[3])
    except ValueError:
        return 0, 0


def _style_number(pattern: re.Pattern[str], style: str) -> int:
    match = pattern.search(style)
    return int(match.group(1)[:_MAX_DIGITS]) if match else 0


def _one_x_url_width(srcset: str) -> int:
    match = _ONE_X_ENTRY.search(srcset)
    if match is None:
        return 0
    width = URL_WIDTH.search(match.group(1))
    return int(width.group(1)[:_MAX_DIGITS]) if width else 0


def _is_math_image(element: Tag) -> bool:
    if element.name != "img":
        return False
    tokens = dom.class_name(element).split()
    return bool(
        _LATEX.search(bounded(dom.attr_or_empty(element, "alt")))
        or "latex" in tokens
        or "tex" in tokens
        or dom.attr(element, "data-latex")
        or dom.attr(element, "data-math")
    )


def _is_small(element: Tag) -> bool:
    style = bounded(dom.attr_or_empty(element, "style"))
    view_width, view_height = _view_box_size(element)
    widths: list[float] = [
        size
        for size in (
            parse_leading_int(bounded(dom.attr_or_empty(element, "width"))),
            _style_number(_STYLE_WIDTH, style),
            view_width,
        )
        if size > 0
    ]
    heights: list[float] = [
        size
        for size in (
            parse_leading_int(bounded(dom.attr_or_empty(element, "height"))),
            _style_number(_STYLE_HEIGHT, style),
            view_height,
        )
        if size > 0
    ]
    if not widths and not heights and element.name == "img":
        url_width = _one_x_url_width(bounded(dom.attr_or_empty(element, "srcset")))
        if url_width > 0:
            widths.append(url_width)
    if not widths and not heights:
        return False
    effective_width = min(widths) if widths else float("inf")
    effective_height = min(heights) if heights else float("inf")
    return effective_width < MIN_DIMENSION or effective_height < MIN_DIMENSION


def find_small_images(document: BeautifulSoup) -> frozenset[str]:
    """Collect identifiers of images too small to be content."""
    found: set[str] = set()
    for element in dom.select(document, "img, svg"):
        if _is_small(element) and not _is_math_image(element):
            identifier = _element_identifier(element)
            if identifier:
                found.add(identifier)
    return frozenset(found)


def _should_remove_image(element: Tag) -> bool:
    src = dom.attr_or_empty(element, "src")
    has_alternative = any(
        dom.attr(element, name)
        for name in ("srcset", "data-src", "data-srcset", "data-lazy-src", "data-original")
    )
    if not src and not has_alternative:
        return True
    return (
        not has_alternative
        and dom.closest(element, "picture") is None
        and is_base64_placeholder(src)
    )


def remove_small_images(document: BeautifulSoup, small_images: frozenset[str]) -> None:
    """Remove images found to be small, and images with no usable source."""
    for element in dom.by_tag(document, "img", "svg"):
        if element.name == "img" and _should_remove_image(element):
            dom.remove(element)
            continue
        identifier = _element_identifier(element)
        if identifier is not None and identifier in small_images:
            dom.remove(element)
