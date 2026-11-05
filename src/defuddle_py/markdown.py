"""Safe Markdown output, covering the security rules of ``src/markdown.ts`` of Defuddle.

The document conversion itself uses markdownify instead of the Turndown rule set, so
output differs from the original in formatting detail.
"""

import logging
import re
from typing import Final
from urllib.parse import quote

from bs4 import Tag
from bs4.element import NavigableString
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdownify import MarkdownConverter

from defuddle_py import dom
from defuddle_py.sanitize import is_dangerous_url, strip_unsafe_elements

logger = logging.getLogger(__name__)

_ENTITY_LIKE: Final = re.compile(r"&(?=#\d+;|#x[\da-f]+;|[a-z][\da-z]*;)", re.IGNORECASE)
# These stay safe only while the link parses. Encoding them keeps them inert when other
# content breaks the link, which turns its destination back into ordinary Markdown.
_DESTINATION_UNSAFE: Final = re.compile(r"[\s`|\[\]<>\u0000-\u001f\u007f]")
_TITLE_ESCAPES: Final = str.maketrans(
    {
        "\\": "\\\\",
        "&": "&amp;",
        '"': "&quot;",
        "<": "&lt;",
        ">": "&gt;",
        "`": "&#96;",
        "|": "&#124;",
        "[": "&#91;",
        "]": "&#93;",
    }
)
_INLINE_BREAKS: Final = re.compile(r"[\r\n]+")
_INLINE_SPECIAL: Final = re.compile(r"([\\`*_{}\[\]>|=])")
# Attribute lists such as {: onerror=x} set HTML attributes in some renderers, even with
# raw HTML turned off, and CommonMark reads them as plain text.
_BRACES: Final = str.maketrans({"{": "\\{", "}": "\\}"})
# A backslash does not protect "<" everywhere: a renderer's autolink can take the backslash
# into a URL and leave the tag. An entity stays text wherever a renderer splits the line.
_ESCAPED_ANGLE: Final = "\\<"
_ANGLE_ENTITY: Final = "&lt;"
_ESCAPED_LABEL_END: Final = "\\]("
_PROTECTED_LABEL_END: Final = "\\]\\("
_PARENTHESES: Final = re.compile(r"([()])")
_LONG_WHITESPACE: Final = re.compile(r"\s{64,}")
_BACKTICK_RUN: Final = re.compile(r"`+")
# Renderers disagree about where a code span starts, for example when an autolink takes
# its backtick, and a span has no way to encode this text. A fenced block has no such doubt.
_UNSAFE_IN_CODE_SPAN: Final = re.compile(r"<|\]\(")
# Renderers also disagree about entities, so a destination that does not start with a
# known form may decode into a scheme. Starting it with "./" fixes it as a relative path.
_SAFE_DESTINATION_START: Final = re.compile(
    r"(?:https?:|ftp:|mailto:|tel:|data:image/|/|\./|\.\./|#|\?)", re.IGNORECASE
)
_RELATIVE_PREFIX: Final = "./"
_LINE_ENDING: Final = re.compile(r"\r\n?")
_UNESCAPED_PIPE: Final = re.compile(r"(?<!\\)((?:\\\\)*)\|")
_MIN_FENCE_LENGTH: Final = 3
_TABLE_CELLS: Final = frozenset({"td", "th"})
# A fence only opens a code block at the start of a line. Markup that wraps its content
# (links, emphasis, table cells) puts text in front of it or joins its lines, so the
# code would be read as ordinary Markdown. Anything not listed gets a code span.
_FENCE_CONTEXT: Final = frozenset(
    {
        "[document]",
        "html",
        "body",
        "div",
        "span",
        "main",
        "article",
        "section",
        "aside",
        "header",
        "footer",
        "nav",
        "figure",
        "details",
        "blockquote",
        "ul",
        "ol",
        "li",
        "dl",
    }
)
_ONE_BASED_STARTS: Final = frozenset({"", "1"})
_CODE_TAG_REPLACEMENTS: Final = {"pre": "div", "code": "span", "kbd": "span", "samp": "span"}
# markdownify finds handlers by name, so these tags would call its internal methods.
_TAG_NAMING_CONVERTER_METHOD: Final = re.compile(r"soup|h\d{2,}")
_INTEGER_ATTRIBUTES: Final = ("start", "colspan")
_MAX_INTEGER_DIGITS: Final = 9
# Private-use characters that mark code spans during conversion. Two spans written side
# by side would otherwise merge their backticks, and neither would close.
_SPAN_OPEN: Final = "\ue000"
_SPAN_CLOSE: Final = "\ue001"
_MAX_INSPECTED_NESTING: Final = 512
_VIDEO_AS_TEXT: Final = frozenset({"_inline", "a"})


def _escape_entity_like(value: str) -> str:
    return _ENTITY_LIKE.sub("&amp;", value)


def format_link_destination(href: str) -> str:
    """Return a Markdown destination, or an empty string for a dangerous URL."""
    href = href.strip()
    if not href or is_dangerous_url(href):
        return ""
    if not _SAFE_DESTINATION_START.match(href):
        href = f"{_RELATIVE_PREFIX}{href}"
    destination = _escape_entity_like(href).replace("\\", "\\\\")
    destination = _DESTINATION_UNSAFE.sub(lambda match: quote(match.group(), safe=""), destination)
    return _PARENTHESES.sub(r"\\\1", destination)


def format_link_title(title: str | None) -> str:
    """Return a quoted Markdown link title, or an empty string."""
    if not title:
        return ""
    return f' "{_INLINE_BREAKS.sub(" ", title).translate(_TITLE_ESCAPES)}"'


def _protect_text(escaped: str) -> str:
    # Inside link text some renderers read an escaped "](" as the end of a link label.
    return escaped.replace(_ESCAPED_LABEL_END, _PROTECTED_LABEL_END)


def escape_inline_text(value: str) -> str:
    """Escape text placed inside Markdown link or image brackets."""
    flattened = _INLINE_BREAKS.sub(" ", value)
    escaped = _escape_entity_like(_INLINE_SPECIAL.sub(r"\\\1", flattened))
    return _protect_text(escaped.replace("<", _ANGLE_ENTITY))


def _longest_backtick_run(text: str) -> int:
    return max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)


def _code_span(text: str) -> str:
    code = text.strip()
    if not code:
        return ""
    prefix = " " if text[0].isspace() else ""
    suffix = " " if text[-1].isspace() else ""
    delimiter = "`" * (_longest_backtick_run(code) + 1)
    padded = f" {code} " if "`" in code else code
    return f"{prefix}{delimiter}{padded}{delimiter}{suffix}"


def _starts_counting_from_one(element: Tag) -> bool:
    # Only a list starting at 1 can interrupt a paragraph, and a list marker allows nine
    # digits, so other lists may not open a block for a fence to sit in.
    return all(
        dom.attr_or_empty(parent, "start") in _ONE_BASED_STARTS
        for parent in element.parents
        if parent.name == "ol"
    )


def _video_source(video: Tag) -> str:
    source = dom.attr_or_empty(video, "src")
    if source:
        return source
    children = dom.by_tag(video, "source")
    return next(filter(None, (dom.attr_or_empty(child, "src") for child in children)), "")


class _SafeConverter(MarkdownConverter):
    """Convert links, media and code without letting content become Markdown syntax."""

    def escape(self, text: str, parent_tags: set[str]) -> str:
        escaped: str = super().escape(text, parent_tags)  # type: ignore[misc]  # Not in the stubs.
        if "_noformat" in parent_tags:
            return escaped
        return _protect_text(escaped.replace(_ESCAPED_ANGLE, _ANGLE_ENTITY)).translate(_BRACES)

    def convert_a(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        content = _INLINE_BREAKS.sub(" ", text).strip()
        destination = format_link_destination(dom.attr_or_empty(el, "href"))
        if not destination or not content:
            return text
        return f"[{content}]({destination}{format_link_title(dom.attr(el, 'title'))})"

    def convert_img(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        destination = format_link_destination(dom.attr_or_empty(el, "src"))
        if not destination:
            return ""
        alt = escape_inline_text(dom.attr_or_empty(el, "alt"))
        return f"![{alt}]({destination}{format_link_title(dom.attr(el, 'title'))})"

    def convert_iframe(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if "a" in parent_tags:
            return ""  # A link inside a link does not parse.
        destination = format_link_destination(dom.attr_or_empty(el, "src"))
        return f"[Embedded content]({destination})" if destination else ""

    def convert_video(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if parent_tags & _VIDEO_AS_TEXT:
            return text
        label = _INLINE_BREAKS.sub(" ", text)
        destination = format_link_destination(_video_source(el))
        poster = format_link_destination(dom.attr_or_empty(el, "poster"))
        if destination and poster:
            return f"[![{label}]({poster})]({destination})"
        if destination:
            return f"[{label}]({destination})"
        return f"![{label}]({poster})" if poster else text

    def convert_code(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if "_noformat" in parent_tags:
            return text
        # Code text is not escaped, so a line break could end the span and a table pipe
        # could split it. Either one would expose the rest as raw Markdown.
        flattened = _INLINE_BREAKS.sub(" ", text)
        if _UNSAFE_IN_CODE_SPAN.search(flattened):
            return escape_inline_text(flattened)
        if parent_tags & _TABLE_CELLS:
            flattened = _UNESCAPED_PIPE.sub(r"\1\\|", flattened)
        span = _code_span(flattened)
        return f"{_SPAN_OPEN}{span}{_SPAN_CLOSE}" if span else ""

    convert_kbd = convert_code
    convert_samp = convert_code

    def convert_pre(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if "_noformat" in parent_tags:
            return text
        if not parent_tags <= _FENCE_CONTEXT or not _starts_counting_from_one(el):
            return self.convert_code(el, text, parent_tags)
        # Renderers end a line at a carriage return, but list and quote prefixes are
        # only added after a newline, so such a line would leave its container.
        code = _LINE_ENDING.sub("\n", text).strip("\n")
        if not code:
            return ""
        fence = "`" * max(_MIN_FENCE_LENGTH, _longest_backtick_run(code) + 1)
        return f"\n\n{fence}\n{code}\n{fence}\n\n"


def _prepare_tree(root: Tag) -> None:
    for node in [root, *root.descendants]:
        if isinstance(node, Tag):
            _prepare_element(node)
        elif dom.is_text_node(node) and isinstance(node, NavigableString):
            prepared = _prepare_text(node)
            if prepared != node:
                node.replace_with(NavigableString(prepared))


def _prepare_element(element: Tag) -> None:
    if _TAG_NAMING_CONVERTER_METHOD.fullmatch(element.name):
        element.name = "div"
    for name in list(element.attrs):
        # The markers are removed from the output after destinations are formatted, so
        # one left in an attribute could hide an entity from that step.
        value = dom.attr(element, name)
        if value is not None and (_SPAN_OPEN in value or _SPAN_CLOSE in value):
            element.attrs[name] = value.replace(_SPAN_OPEN, "").replace(_SPAN_CLOSE, "")
    for name in _INTEGER_ATTRIBUTES:
        # markdownify passes these to int() after a check that accepts "\u00b2" and any
        # length, and int() rejects both.
        value = dom.attr(element, name)
        if value is not None and not _is_small_integer(value):
            del element.attrs[name]


def _is_small_integer(value: str) -> bool:
    return value.isascii() and value.isdigit() and len(value) <= _MAX_INTEGER_DIGITS


def _prepare_text(text: str) -> str:
    # markdownify scans whitespace with backtracking regexes, which turns one long run
    # into quadratic time. No rendered text depends on a run this long.
    shortened = _LONG_WHITESPACE.sub(" ", text)
    # The span markers are removed from the output, so page text may not contain them:
    # removing one between two backticks would lengthen a run after it was measured.
    return shortened.replace(_SPAN_OPEN, "").replace(_SPAN_CLOSE, "")


def _convert(root: Tag) -> str:
    converter = _SafeConverter(
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
        escape_misc=True,
        autolinks=False,
    )
    converted: str = converter.convert_soup(root)
    separated = converted.replace(f"{_SPAN_CLOSE}{_SPAN_OPEN}", " ")
    return separated.replace(_SPAN_OPEN, "").replace(_SPAN_CLOSE, "").strip()


class _LinkPreservingParser(MarkdownIt):
    """Parse Markdown keeping every link, so the check below sees what renderers may."""

    def validateLink(self, url: str) -> bool:
        return True


_PARSER: Final = _LinkPreservingParser(
    "commonmark", {"html": True, "maxNesting": _MAX_INSPECTED_NESTING}
).enable("table")


def _token_is_inert(token: Token) -> bool:
    if token.type in ("html_block", "html_inline"):
        return False
    url = token.attrGet("href") or token.attrGet("src")
    return not (isinstance(url, str) and is_dangerous_url(url))


def _is_inert(markdown: str) -> bool:
    """Return whether ``markdown`` holds no raw HTML and no dangerous link.

    Correct output never has either, because text is escaped and code stays in a code
    block or span. This checks the result instead of trusting each syntax rule.
    """
    pending = list(_PARSER.parse(markdown))
    while pending:
        token = pending.pop()
        if not _token_is_inert(token):
            return False
        pending.extend(token.children or ())
    return True


def _drop_code_markup(root: Tag) -> None:
    for element in dom.by_tag(root, *_CODE_TAG_REPLACEMENTS):
        element.name = _CODE_TAG_REPLACEMENTS[element.name]


def to_markdown(root: Tag) -> str:
    """Convert sanitized content to Markdown.

    Sanitizing here too keeps direct callers safe, since this function can receive a
    tree that never went through the extraction pipeline. Output that fails ``_is_inert``
    is converted again with code as escaped text, and dropped if it still fails.
    """
    if strip_unsafe_elements(root):
        return ""
    _prepare_tree(root)
    markdown = _convert(root)
    if _is_inert(markdown):
        return markdown
    logger.warning("Markdown exposed raw HTML; converting code as plain text")
    _drop_code_markup(root)
    markdown = _convert(root)
    if _is_inert(markdown):
        return markdown
    logger.warning("Dropping Markdown that still exposes raw HTML")
    return ""
