"""defuddle-py: a Python port of the Defuddle main-content extraction algorithm."""

from dataclasses import dataclass, field

from defuddle_py.deadline import deadline
from defuddle_py.dom import parse_document
from defuddle_py.encoding import decode_html
from defuddle_py.errors import (
    DefuddleError,
    InputTooLargeError,
    ParseTimeoutError,
    TreeTooDeepError,
    UnsupportedEncodingError,
)
from defuddle_py.extractor import Pipeline
from defuddle_py.limits import Limits
from defuddle_py.markdown import to_markdown
from defuddle_py.metadata import MetaTag
from defuddle_py.schema_org import JsonValue
from defuddle_py.text import replace_lone_surrogates

__all__ = [
    "DefuddleError",
    "InputTooLargeError",
    "Limits",
    "MetaTag",
    "ParseResult",
    "ParseTimeoutError",
    "TreeTooDeepError",
    "UnsupportedEncodingError",
    "parse",
]


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Extracted content and page metadata."""

    content: str
    content_markdown: str
    word_count: int
    title: str
    description: str
    domain: str
    favicon: str
    image: str
    language: str
    published: str
    author: str
    site: str
    schema_org_data: list[JsonValue] = field(default_factory=list)
    meta_tags: list[MetaTag] = field(default_factory=list)


def _to_text(html: str | bytes, limits: Limits, encoding: str | None) -> str:
    size = len(html) if isinstance(html, bytes) else len(html.encode("utf-8", "replace"))
    if size > limits.max_input_bytes:
        raise InputTooLargeError(f"input is {size} bytes, limit is {limits.max_input_bytes}")
    return decode_html(html, encoding) if isinstance(html, bytes) else replace_lone_surrogates(html)


def parse(
    html: str | bytes,
    *,
    url: str = "",
    content_selector: str | None = None,
    encoding: str | None = None,
    limits: Limits | None = None,
) -> ParseResult:
    """Extract the main content of an HTML page.

    The input is treated as untrusted. Nothing is fetched: ``url`` only resolves
    relative links and supplies the page domain.

    Args:
        html: The page source. Bytes are decoded by ``decode_html`` order.
        url: The address the page came from.
        content_selector: A CSS selector that overrides content detection.
        encoding: The encoding of ``html`` when it is bytes, for example from a
            ``Content-Type`` header. Without it the page declaration is used.
        limits: Resource limits. Defaults to ``Limits()``.

    Returns:
        The extracted content as sanitized HTML and Markdown, with metadata.

    Raises:
        InputTooLargeError: If the input exceeds ``limits.max_input_bytes``.
        TreeTooDeepError: If the input nests too deeply to process.
        ParseTimeoutError: If parsing exceeds ``limits.timeout_seconds``.
        UnsupportedEncodingError: If ``encoding`` is not a supported label.
    """
    active_limits = limits or Limits()
    text = _to_text(html, active_limits, encoding)
    try:
        with deadline(active_limits.timeout_seconds):
            document = parse_document(text, active_limits.max_tree_depth)
            extraction = Pipeline(
                document, url=url, content_selector=content_selector, limits=active_limits
            ).run()
            content = extraction.root.decode() if extraction.root is not None else ""
            markdown = to_markdown(extraction.root) if extraction.root is not None else ""
    except RecursionError as err:
        raise TreeTooDeepError("element nesting is too deep to process") from err
    metadata = extraction.metadata
    return ParseResult(
        content=content,
        content_markdown=markdown,
        word_count=extraction.word_count,
        title=metadata.title,
        description=metadata.description,
        domain=metadata.domain,
        favicon=metadata.favicon,
        image=metadata.image,
        language=metadata.language,
        published=metadata.published,
        author=metadata.author,
        site=metadata.site,
        schema_org_data=extraction.schema_org_data,
        meta_tags=extraction.meta_tags,
    )
