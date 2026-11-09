"""Hidden element removal ported from ``src/removals/hidden.ts`` of Defuddle.

Computed styles are unavailable outside a browser, so only inline styles and
utility classes are inspected.
"""

import re
from typing import Final

from bs4 import BeautifulSoup, Tag

from defuddle_py import dom
from defuddle_py.text import bounded

_HIDDEN_STYLE: Final = re.compile(
    r"(?:^|;\s*)(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0)(?:\s*;|\s*$)",
    re.IGNORECASE,
)
_RESPONSIVE_SHOW: Final = re.compile(
    r"^(sm|md|lg|xl|2xl|min-\[|max-\[):(?:block|flex|grid|inline|table|contents)"
)


def has_responsive_show_class(class_name: str) -> bool:
    """Return whether a class list shows the element at some breakpoint."""
    return any(_RESPONSIVE_SHOW.search(token) for token in class_name.split())


def _has_hidden_class(class_name: str) -> bool:
    tokens = class_name.split()
    if has_responsive_show_class(class_name):
        return False
    return any(
        token in ("hidden", "invisible")
        or ("[" not in token and token.endswith((":hidden", ":invisible")))
        for token in tokens
    )


def _is_hidden(element: Tag) -> bool:
    style = dom.attr(element, "style")
    hidden = bool(style and _HIDDEN_STYLE.search(bounded(style))) or _has_hidden_class(
        dom.class_name(element)
    )
    # The math check scans the subtree, so it runs last and only when needed.
    return (
        hidden
        and element.name != "math"
        and not dom.has(element, "math, [data-mathml], .katex-mathml")
    )


def remove_hidden_elements(document: BeautifulSoup) -> None:
    """Remove elements hidden by inline style or framework classes."""
    hidden = [node for node in document.descendants if isinstance(node, Tag) and _is_hidden(node)]
    for element in hidden:
        dom.remove(element)
