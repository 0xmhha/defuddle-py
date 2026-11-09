"""JSON-LD reading ported from ``src/defuddle.ts`` and ``src/metadata.ts`` of Defuddle."""

import html
import json
import logging
import re
from typing import Final, TypeAlias

from bs4 import BeautifulSoup

from defuddle_py import dom
from defuddle_py.limits import Limits
from defuddle_py.text import replace_lone_surrogates

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None

_LD_JSON_SELECTOR: Final = 'script[type="application/ld+json"]'
_BLOCK_COMMENT: Final = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT: Final = re.compile(r"^[ \t]*//[^\n]*$", re.MULTILINE)
_CDATA_START: Final = "<![CDATA["
_CDATA_END: Final = "]]>"
_MAX_SCHEMA_TEXT_DEPTH: Final = 10
_ARRAY_INDEX: Final = re.compile(r"^\[(\d+)\]$")


def _strip_wrappers(source: str) -> str:
    # Plain string edits, because the regexes in the original backtrack on whitespace.
    cleaned = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source)).strip()
    if cleaned.startswith(_CDATA_START) and cleaned.endswith(_CDATA_END):
        cleaned = cleaned[len(_CDATA_START) : -len(_CDATA_END)].strip()
    for marker in ("*/", "/*"):
        cleaned = cleaned.removeprefix(marker).removesuffix(marker).strip()
    return cleaned


def _depth_within(value: JsonValue, max_depth: int) -> bool:
    stack: list[tuple[JsonValue, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            return False
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
    return True


def _decode_strings(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return replace_lone_surrogates(html.unescape(value))
    if isinstance(value, list):
        return [_decode_strings(item) for item in value]
    if isinstance(value, dict):
        return {replace_lone_surrogates(key): _decode_strings(item) for key, item in value.items()}
    return value


def _load_script(source: str, limits: Limits) -> JsonValue:
    if len(source.encode("utf-8", "replace")) > limits.max_json_ld_bytes:
        logger.warning("Skipping JSON-LD block over the size limit")
        return None
    try:
        data: JsonValue = json.loads(_strip_wrappers(source))
    except (ValueError, RecursionError):
        logger.debug("Skipping JSON-LD block that does not parse")
        return None
    if not _depth_within(data, limits.max_json_ld_depth):
        logger.warning("Skipping JSON-LD block over the depth limit")
        return None
    return data


def extract_schema_org(document: BeautifulSoup, limits: Limits) -> list[JsonValue]:
    """Read every JSON-LD block that fits the limits, flattening ``@graph``."""
    items: list[JsonValue] = []
    for script in dom.select(document, _LD_JSON_SELECTOR):
        data = _load_script(dom.text_of(script), limits)
        if data is None:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        if isinstance(graph, list):
            items.extend(graph)
        else:
            items.append(data)
    return [_decode_strings(item) for item in items]


def schema_text(data: JsonValue, depth: int = 0) -> str:
    """Return the first ``text`` or ``articleBody`` string in the schema data."""
    if not data or depth > _MAX_SCHEMA_TEXT_DEPTH:
        return ""
    for item in data if isinstance(data, list) else [data]:
        if isinstance(item, list):
            found = schema_text(item, depth + 1)
            if found:
                return found
            continue
        if not isinstance(item, dict):
            continue
        for key in ("text", "articleBody"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        graph = item.get("@graph")
        if isinstance(graph, list):
            found = schema_text(graph, depth + 1)
            if found:
                return found
    return ""


def _search_list(data: list[JsonValue], props: list[str], *, exact: bool) -> list[str]:
    index = _ARRAY_INDEX.match(props[0]) if props else None
    if index is not None:
        position = int(index.group(1))
        if position < len(data) and data[position]:
            return _search(data[position], props[1:], exact=exact)
        return []
    is_scalar_list = all(
        isinstance(item, str | int | float) and not isinstance(item, bool) for item in data
    )
    if not props and is_scalar_list:
        return [str(item) for item in data]
    return [found for item in data for found in _search(item, props, exact=exact)]


def _search(data: JsonValue, props: list[str], *, exact: bool) -> list[str]:
    if isinstance(data, str):
        return [] if props else [data]
    if isinstance(data, list):
        return _search_list(data, props, exact=exact)
    if not isinstance(data, dict):
        return []
    if not props:
        name = data.get("name")
        return [name] if isinstance(name, str) and name else []
    current, remaining = props[0], props[1:]
    if current in data:
        return _search(data[current], remaining, exact=True)
    if exact:
        return []
    nested: list[str] = []
    for value in data.values():
        if isinstance(value, list | dict):
            nested.extend(_search(value, props, exact=False))
    return nested


def schema_property(data: JsonValue, path: str) -> str:
    """Look up a dotted property, falling back to a search of nested objects."""
    if not data:
        return ""
    props = path.split(".")
    results = _search(data, props, exact=True) or _search(data, props, exact=False)
    return ", ".join(dict.fromkeys(result for result in results if result))
