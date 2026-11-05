"""Text helpers ported from ``src/utils.ts`` of Defuddle."""

from typing import Final

from defuddle_py.limits import MAX_PATTERN_TEXT_CHARS

_CJK_RANGES: Final = (
    (0x3040, 0x309F),
    (0x30A0, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xAC00, 0xD7AF),
)
_MAX_CONTROL_CODE: Final = 32
_NORMALIZE_TABLE: Final = str.maketrans(
    {
        " ": " ",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "…": "...",
    }
)


def _is_cjk(code: int) -> bool:
    return any(low <= code <= high for low, high in _CJK_RANGES)


def count_words(text: str) -> int:
    """Count words, treating each CJK character as one word."""
    count = 0
    in_word = False
    for char in text:
        code = ord(char)
        if code <= _MAX_CONTROL_CODE:
            in_word = False
        elif _is_cjk(code):
            count += 1
            in_word = False
        elif not in_word:
            count += 1
            in_word = True
    return count


def collapse_whitespace(text: str) -> str:
    """Replace every whitespace run with one space and trim the ends."""
    return " ".join(text.split())


def normalize_text(text: str) -> str:
    """Canonicalize text so strings a person reads as equal compare equal."""
    return collapse_whitespace(text.translate(_NORMALIZE_TABLE)).lower()


def bounded(text: str) -> str:
    """Return a prefix of ``text`` that is safe to give to a backtracking regex."""
    return text[:MAX_PATTERN_TEXT_CHARS]
