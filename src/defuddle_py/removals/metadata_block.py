"""H1-adjacent date block removal from ``src/removals/metadata-block.ts`` of Defuddle."""

import re
from typing import Final

from bs4 import Tag

from defuddle_py import dom

_MONTHS: Final = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    "|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_DATE: Final = re.compile(
    rf"\b(?:(?:{_MONTHS})\s+\d{{1,2}}[\s,]+\d{{4}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\s+\d{{4}}"
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b",
    re.IGNORECASE,
)
_MAX_BLOCK_CHARS: Final = 300


def remove_metadata_block(main_content: Tag) -> None:
    """Remove a short date block that directly follows the first h1."""
    heading = dom.select_one(main_content, "h1")
    if heading is None:
        return
    sibling = dom.next_element_sibling(heading)
    for _ in range(3):
        if sibling is None:
            return
        following = dom.next_element_sibling(sibling)
        text = dom.trimmed_text(sibling)
        if 0 < len(text) < _MAX_BLOCK_CHARS and (
            _DATE.search(text)
            or any(
                _DATE.search(dom.trimmed_text(child)[:_MAX_BLOCK_CHARS])
                for child in dom.select(sibling, "p, time")
            )
        ):
            dom.remove(sibling)
            return
        sibling = following
