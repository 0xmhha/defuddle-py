"""Decode HTML bytes, following the order the WHATWG encoding sniffing rules use.

Upstream does this in src/fetch.ts for pages it downloads. Here the same order applies
to bytes the caller already holds: byte order mark, then the encoding the caller knows
from the HTTP header, then the page's own declaration, then a UTF-8 check.
"""

import re
from typing import Final

from defuddle_py.errors import UnsupportedEncodingError

_UTF_8: Final = "utf-8"
_FALLBACK: Final = "cp1252"
_PRESCAN_BYTES: Final = 1024

_BYTE_ORDER_MARKS: Final = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

# Page content chooses the label, so only these codecs are reachable. An open lookup
# would admit utf-7 and iso-2022 variants, whose escape sequences can hide markup from
# the sanitizer, and Python-only codecs such as unicode_escape. Browsers treat the
# legacy labels below as supersets, so they map to the superset here too.
_CODECS_BY_LABEL: Final[dict[str, str]] = {
    "utf-8": _UTF_8,
    "utf8": _UTF_8,
    "ascii": _FALLBACK,
    "us-ascii": _FALLBACK,
    "latin1": _FALLBACK,
    "iso-8859-1": _FALLBACK,
    "iso8859-1": _FALLBACK,
    "windows-1252": _FALLBACK,
    "cp1252": _FALLBACK,
    "euc-kr": "cp949",
    "euckr": "cp949",
    "ks_c_5601-1987": "cp949",
    "ksc5601": "cp949",
    "cp949": "cp949",
    "windows-949": "cp949",
    "euc-jp": "euc_jp",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "windows-31j": "cp932",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "big5": "big5hkscs",
    "koi8-r": "koi8_r",
    "koi8-u": "koi8_u",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "windows-1253": "cp1253",
    "windows-1254": "cp1254",
    "windows-1255": "cp1255",
    "windows-1256": "cp1256",
    "windows-1257": "cp1257",
    "windows-1258": "cp1258",
    "iso-8859-2": "iso8859_2",
    "iso-8859-5": "iso8859_5",
    "iso-8859-7": "iso8859_7",
    "iso-8859-15": "iso8859_15",
}

_COMMENT: Final = re.compile(rb"<!--.*?-->", re.DOTALL)
_META_TAG: Final = re.compile(rb"<meta\b[^>]*>", re.IGNORECASE)
_CHARSET_VALUE: Final = re.compile(rb"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_:.-]+)", re.IGNORECASE)


def _codec_for(label: str) -> str | None:
    return _CODECS_BY_LABEL.get(label.strip().lower())


def _declared_codec(data: bytes) -> str | None:
    """Return the first supported encoding declared in a meta tag near the top."""
    head = _COMMENT.sub(b"", data[:_PRESCAN_BYTES])
    for tag in _META_TAG.findall(head):
        match = _CHARSET_VALUE.search(tag)
        if match is None:
            continue
        return _codec_for(match.group(1).decode("ascii", "ignore"))
    return None


def decode_html(data: bytes, encoding: str | None = None) -> str:
    """Decode page bytes to text.

    Args:
        data: The page source.
        encoding: The label the caller knows, for example from a ``Content-Type``
            header. A byte order mark still wins, as it does in browsers.

    Returns:
        The decoded text. Undecodable bytes become U+FFFD.

    Raises:
        UnsupportedEncodingError: If ``encoding`` is not a supported label.
    """
    for mark, codec in _BYTE_ORDER_MARKS:
        if data.startswith(mark):
            return data.decode(codec, "replace")
    if encoding is not None:
        chosen = _codec_for(encoding)
        if chosen is None:
            raise UnsupportedEncodingError(f"unsupported encoding: {encoding[:64]!r}")
        return data.decode(chosen, "replace")
    declared = _declared_codec(data)
    if declared is not None:
        return data.decode(declared, "replace")
    try:
        return data.decode(_UTF_8)
    except UnicodeDecodeError:
        return data.decode(_FALLBACK, "replace")
