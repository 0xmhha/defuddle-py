"""The extraction pipeline, ported from ``src/defuddle.ts`` of Defuddle."""

import copy
import itertools
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Final

from bs4 import BeautifulSoup, Tag
from soupsieve import SelectorSyntaxError

from defuddle_py import dom
from defuddle_py.constants import (
    BLOCK_ELEMENTS_SELECTOR,
    ENTRY_POINT_ELEMENTS,
    HIDDEN_EXACT_SKIP_SELECTOR,
)
from defuddle_py.limits import Limits
from defuddle_py.metadata import Metadata, MetaTag, collect_meta_tags, extract_metadata
from defuddle_py.removals.content_patterns import remove_by_content_pattern, remove_eyebrow_label
from defuddle_py.removals.hidden import remove_hidden_elements
from defuddle_py.removals.metadata_block import remove_metadata_block
from defuddle_py.removals.selectors import remove_by_selector
from defuddle_py.removals.small_images import URL_WIDTH, find_small_images, remove_small_images
from defuddle_py.sanitize import strip_unsafe_elements
from defuddle_py.schema_org import JsonValue, extract_schema_org, schema_text
from defuddle_py.scoring import (
    find_best_element,
    parse_leading_int,
    score_and_remove,
    score_element,
)
from defuddle_py.standardize import standardize_content
from defuddle_py.text import bounded, collapse_whitespace, count_words
from defuddle_py.urls import parse_url

logger = logging.getLogger(__name__)

_RETRY_WORDS: Final = 200
_LOW_WORDS: Final = 50
_SCHEMA_RATIO: Final = 1.5
_LAYOUT_TABLE_MIN_WIDTH: Final = 400
_MIN_CHILD_WORDS: Final = 50
_MIN_HIDDEN_WORDS: Final = 30
_MAX_DIGITS: Final = 9
_ENTITY_NAME: Final = re.compile(r"&\w+;")
_ENTITY_NUMBER: Final = re.compile(r"&#\d+;")
_SRCSET_DESCRIPTOR: Final = re.compile(r" (\d+(?:\.\d+)?[wx])(?=$|[ ,])")
_PARAGRAPH_BREAK: Final = re.compile(r"\n\s*\n")
_EDGE_QUOTES_START: Final = re.compile(r"^\\?[\"']+")
_EDGE_QUOTES_END: Final = re.compile(r"\\?[\"']+$")
_CDN_WIDTH: Final = re.compile(r",w_\d+")
_CDN_CROP: Final = re.compile(r",c_\w+")
_PLAIN_IDENTIFIER: Final = re.compile(r"^(?:--|-?[a-zA-Z_])[a-zA-Z0-9_-]*$")
_UNSELECTABLE: Final = re.compile(r"\0|[^\S\t\n\f\r ]")
_CONTROL_CHARS: Final = re.compile(r"[\x01-\x1f\x7f]")
_MATH_CLASS: Final = "math"


@dataclass(frozen=True, slots=True)
class _PassOptions:
    remove_exact_selectors: bool = True
    remove_partial_selectors: bool = True
    remove_hidden_elements: bool = True
    remove_low_scoring: bool = True
    remove_small_images: bool = True
    remove_content_patterns: bool = True
    content_selector: str | None = None


@dataclass(slots=True)
class Extraction:
    """The result of one extraction, as a detached content tree."""

    root: Tag | None
    word_count: int
    metadata: Metadata
    schema_org_data: list[JsonValue]
    meta_tags: list[MetaTag] = field(default_factory=list)


def count_html_words(content: str) -> int:
    """Count words in serialized HTML without parsing it.

    Scans for tags by hand. The original regex rescans the tail for each ``<`` that
    has no closing ``>``, which is quadratic on hostile input.
    """
    pieces: list[str] = []
    position = 0
    while True:
        start = content.find("<", position)
        end = content.find(">", start) if start >= 0 else -1
        if start < 0 or end < 0:
            pieces.append(content[position:])
            break
        pieces.append(content[position:start])
        pieces.append(" ")
        position = end + 1
    text = "".join(pieces).replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return count_words(_ENTITY_NAME.sub(" ", _ENTITY_NUMBER.sub(" ", text)))


def _split_srcset(srcset: str) -> list[tuple[str, str]]:
    """Split a srcset on its descriptors, since URLs themselves may hold commas."""
    collapsed = collapse_whitespace(srcset)
    entries: list[tuple[str, str]] = []
    position = 0
    for match in _SRCSET_DESCRIPTOR.finditer(collapsed):
        url = collapsed[position : match.start()].strip().removeprefix(",").strip()
        entries.append((url, match.group(1)))
        position = match.end()
    return entries


def _normalize_src(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").split("?")[0]


def _url_width(image: Tag) -> int:
    match = URL_WIDTH.search(bounded(dom.attr_or_empty(image, "src")))
    return int(match.group(1)[:_MAX_DIGITS]) if match else 0


def _image_tier(image: Tag) -> int:
    if dom.attr(image, "srcset"):
        return 2
    return 1 if dom.closest(image, "picture") is not None else 0


def _pick_best_image(first: Tag, second: Tag) -> Tag:
    tier_first, tier_second = _image_tier(first), _image_tier(second)
    if tier_first != tier_second:
        return first if tier_first > tier_second else second
    width_first, width_second = _url_width(first), _url_width(second)
    if width_first != width_second:
        return first if width_first > width_second else second
    return first


def _keep_best_image(group: list[Tag]) -> None:
    best = group[0]
    for candidate in group[1:]:
        winner = _pick_best_image(best, candidate)
        dom.remove(candidate if winner is best else best)
        best = winner


def _no_visible_content_between(first: Tag, second: Tag) -> bool:
    node = first.next_element
    while node is not None and node is not second:
        if not isinstance(node, Tag) and str(node).strip():
            return False
        node = node.next_element
    return True


def _usable_src(image: Tag) -> str:
    src = dom.attr_or_empty(image, "src")
    return "" if src.startswith("data:") else src


def _dedupe_figures(body: Tag) -> None:
    for figure in dom.by_tag(body, "figure"):
        images = [
            image
            for image in dom.by_tag(figure, "img")
            if dom.closest(image, "noscript") is None and dom.parent_element(image) is not None
        ]
        if len(images) < 2:
            continue
        groups: dict[str | None, list[Tag]] = {}
        for image in images:
            if _usable_src(image):
                key = dom.attr_or_empty(image, "alt").strip() or None
                groups.setdefault(key, []).append(image)
        for key, group in groups.items():
            if len(group) < 2:
                continue
            first_src = dom.attr(group[0], "src")
            if key is not None and all(dom.attr(item, "src") == first_src for item in group):
                continue
            _keep_best_image(group)


def _is_free_image(image: Tag) -> bool:
    return dom.parent_element(image) is not None and dom.closest(image, "noscript, figure") is None


def _dedupe_adjacent(body: Tag) -> None:
    images = dom.by_tag(body, "img")
    for image, other in itertools.pairwise(images):
        if not _is_free_image(image) or not _is_free_image(other):
            continue
        alt = dom.attr_or_empty(image, "alt").strip()
        src, other_src = _usable_src(image), _usable_src(other)
        if not alt or not src or not other_src or other_src == src:
            continue
        if dom.attr_or_empty(other, "alt").strip() != alt:
            continue
        if _no_visible_content_between(image, other):
            _keep_best_image([image, other])


def _dedupe_lightbox(body: Tag) -> None:
    for image in dom.by_tag(body, "img"):
        parent = dom.parent_element(image)
        src = _usable_src(image)
        if parent is None or not src or dom.closest(image, "a, figure, noscript") is not None:
            continue
        normalized = _normalize_src(src)
        for link in dom.child_elements(parent):
            if (
                link.name == "a"
                and dom.attr(link, "href") is not None
                and dom.has(link, "img")
                and normalized == _normalize_src(dom.attr_or_empty(link, "href"))
            ):
                dom.remove(image)
                break


def _largest_image_src(image: Tag) -> str:
    srcset = dom.attr_or_empty(image, "srcset")
    if not srcset:
        return dom.attr_or_empty(image, "src")
    best_url = ""
    best_width = 0.0
    for url, descriptor in _split_srcset(srcset):
        if descriptor.endswith("w") and url and float(descriptor[:-1]) > best_width:
            best_width = float(descriptor[:-1])
            best_url = url
    chosen = best_url or dom.attr_or_empty(image, "src")
    return _CDN_CROP.sub("", _CDN_WIDTH.sub("", bounded(chosen)))


def _remove_cover_image(body: Tag, metadata_image: str) -> str | None:
    if not metadata_image:
        return None
    wanted = _normalize_src(metadata_image)
    for image in dom.by_tag(body, "img"):
        src = _usable_src(image)
        if not src or _normalize_src(src) != wanted:
            continue
        best_url = _largest_image_src(image)
        figure = dom.closest(image, "figure")
        if figure is None or not dom.has(figure, "figcaption"):
            dom.remove(image)
        return best_url
    return None


@dataclass(frozen=True, slots=True)
class _UrlResolver:
    base_url: str

    def resolve(self, url: str) -> str:
        normalized = _EDGE_QUOTES_END.sub("", _EDGE_QUOTES_START.sub("", url.strip()))
        if normalized.startswith("#"):
            return normalized
        parsed = parse_url(normalized, self.base_url)
        if parsed is not None:
            return parsed.href
        return normalized or url

    def resolve_srcset(self, srcset: str) -> str:
        entries = _split_srcset(srcset)
        if entries:
            return ", ".join(f"{self.resolve(url)} {size}" for url, size in entries)
        resolved: list[str] = []
        for entry in srcset.split(","):
            parts = entry.split()
            if parts:
                parts[0] = self.resolve(parts[0])
            resolved.append(" ".join(parts))
        return ", ".join(resolved)


_WEB_SCHEMES: Final = ("http://", "https://")


def _resolve_relative_urls(root: Tag, document: BeautifulSoup, page_url: str) -> None:
    if not page_url:
        return
    base = page_url
    base_element = dom.select_one(document, "base[href]")
    if base_element is not None:
        parsed = parse_url(dom.attr_or_empty(base_element, "href"), page_url)
        # urljoin leaves relative URLs unresolved under other schemes.
        if parsed is not None and parsed.href.lower().startswith(_WEB_SCHEMES):
            base = parsed.href
    resolver = _UrlResolver(base)
    for element in [root, *(node for node in root.descendants if isinstance(node, Tag))]:
        for name in ("href", "src", "poster"):
            value = dom.attr(element, name)
            if value:
                element.attrs[name] = resolver.resolve(value)
        srcset = dom.attr(element, "srcset")
        if srcset:
            element.attrs["srcset"] = resolver.resolve_srcset(srcset)


def _is_lazy_image_context(noscript: Tag) -> bool:
    if dom.closest(noscript, "figure") is not None:
        return True
    parent = dom.parent_element(noscript)
    if parent is None:
        return False
    for sibling in dom.child_elements(parent):
        if sibling is not noscript and "lazy" in dom.class_name(sibling).lower():
            return True
    parent_class = dom.class_name(parent).lower()
    return any(word in parent_class for word in ("image", "img", "picture", "photo", "media"))


def _noscript_image(noscript: Tag) -> Tag | None:
    image = dom.select_one(noscript, "img")
    if image is not None:
        return image
    source = dom.text_of(noscript)
    if "<img" not in source:
        return None
    return dom.select_one(dom.parse_fragment(source), "img")


def _replace_placeholder(parent: Tag, alt: str | None, source_image: Tag, real_src: str) -> bool:
    if not alt:
        return False
    for image in dom.child_elements(parent):
        if (
            image.name == "img"
            and dom.attr_or_empty(image, "src").startswith("data:")
            and dom.attr(image, "alt") == alt
        ):
            image.attrs["src"] = real_src
            srcset = dom.attr(source_image, "srcset")
            if srcset:
                image.attrs["srcset"] = srcset
            return True
    return False


def _resolve_noscript_images(body: Tag) -> None:
    for noscript in dom.by_tag(body, "noscript"):
        source_image = _noscript_image(noscript)
        parent = dom.parent_element(noscript)
        if source_image is None or parent is None:
            continue
        real_src = dom.attr_or_empty(source_image, "src")
        if not real_src or real_src.startswith("data:"):
            continue
        alt = dom.attr(source_image, "alt")
        if _replace_placeholder(parent, alt, source_image, real_src):
            continue
        if not _is_lazy_image_context(noscript):
            continue
        container = dom.closest(noscript, "figure") or parent
        has_real_image = any(
            dom.closest(image, "noscript") is None and _usable_src(image)
            for image in dom.by_tag(container, "img")
        )
        if not has_real_image:
            noscript.insert_before(copy.copy(source_image))


def _class_or_id_selector(attribute: str, value: str) -> str | None:
    if not value or _UNSELECTABLE.search(bounded(value)):
        return None
    if _PLAIN_IDENTIFIER.match(value):
        return ("." if attribute == "class" else "#") + value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = _CONTROL_CHARS.sub(lambda match: f"\\{ord(match.group()):x} ", escaped)
    operator = "~" if attribute == "class" else ""
    return f'[{attribute}{operator}="{escaped}"]'


def _element_selector(element: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = element
    while current is not None and current.name != "html":
        selector = current.name
        id_selector = _class_or_id_selector("id", dom.element_id(current))
        class_names: list[str] = []
        if id_selector is None:
            class_names = [
                name
                for name in dom.class_name(current).split()
                if _class_or_id_selector("class", name) is not None
            ]
            selector += "".join(_class_or_id_selector("class", name) or "" for name in class_names)
        else:
            selector += id_selector
        parent = dom.parent_element(current)
        if id_selector is None and parent is not None:
            same_tag = [child for child in dom.child_elements(parent) if child.name == current.name]
            matching = [
                sibling
                for sibling in same_tag
                if all(name in dom.class_name(sibling).split() for name in class_names)
            ]
            if len(matching) > 1:
                position = next(i for i, sib in enumerate(same_tag, 1) if sib is current)
                selector += f":nth-of-type({position})"
        parts.insert(0, selector)
        current = parent
    return " > ".join(parts)


def _find_element_by_schema_text(root: Tag, text: str) -> Tag | None:
    first_paragraph = _PARAGRAPH_BREAK.split(bounded(text))[0].strip()
    phrase = first_paragraph[:100].strip()
    if not phrase:
        return None
    wanted_words = count_words(text)
    best: Tag | None = None
    best_size = float("inf")
    for element in root.descendants:
        if not isinstance(element, Tag):
            continue
        element_text = dom.text_of(element)
        if phrase not in element_text:
            continue
        words = count_words(element_text)
        if words >= wanted_words * 0.8 and words < best_size:
            best_size = words
            best = element
    return best


def _table_looks_like_layout(table: Tag) -> bool:
    table_class = dom.class_name(table).lower()
    if (
        parse_leading_int(dom.attr_or_empty(table, "width")) > _LAYOUT_TABLE_MIN_WIDTH
        or dom.attr_or_empty(table, "align") == "center"
        or "content" in table_class
        or "article" in table_class
    ):
        return True
    for row in dom.by_tag(table, "tr"):
        cells = [child for child in dom.child_elements(row) if child.name == "td"]
        if len(cells) >= 2 and any(dom.attr(cell, "width") for cell in cells):
            return True
    return False


def _find_table_based_content(document: BeautifulSoup) -> Tag | None:
    if not any(_table_looks_like_layout(table) for table in dom.by_tag(document, "table")):
        return None
    best = find_best_element(dom.by_tag(document, "td"))
    if best is None:
        return None
    body = dom.select_one(document, "body")
    body_words = count_words(dom.text_of(body if body is not None else document))
    return None if count_words(dom.text_of(best)) * 2 < body_words else best


def _find_content_by_scoring(document: BeautifulSoup) -> Tag | None:
    best: Tag | None = None
    best_score: float = 0
    for element in dom.select(document, BLOCK_ELEMENTS_SELECTOR):
        score = score_element(element)
        if score > best_score:
            best_score = score
            best = element
    return best


@dataclass(frozen=True, slots=True)
class _Candidate:
    element: Tag
    score: float
    selector_index: int


def _prefer_specific_child(candidates: list[_Candidate]) -> Tag:
    top = candidates[0]
    best = top
    for child in candidates[1:]:
        if child.selector_index >= best.selector_index:
            continue
        if not dom.contains(best.element, child.element):
            continue
        if count_words(dom.text_of(child.element)) <= _MIN_CHILD_WORDS:
            continue
        same_selector = sum(
            1
            for other in candidates
            if other.selector_index == child.selector_index
            and dom.contains(top.element, other.element)
        )
        if same_selector <= 1:
            best = child
    return best.element


def find_main_content(document: BeautifulSoup) -> Tag | None:
    """Choose the element most likely to hold the article."""
    candidates: list[_Candidate] = []
    total = len(ENTRY_POINT_ELEMENTS)
    for index, selector in enumerate(ENTRY_POINT_ELEMENTS):
        for element in dom.select(document, selector):
            score = (total - index) * 40 + score_element(element)
            candidates.append(_Candidate(element, score, index))
    if not candidates:
        return _find_content_by_scoring(document)
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    if len(candidates) == 1 and candidates[0].element.name == "body":
        table_content = _find_table_based_content(document)
        if table_content is not None:
            return table_content
    return _prefer_specific_child(candidates)


class Pipeline:
    """Run extraction passes over one document, with retries on thin results."""

    def __init__(
        self,
        document: BeautifulSoup,
        *,
        url: str,
        content_selector: str | None,
        limits: Limits,
    ) -> None:
        self._document = document
        self._url = url
        self._content_selector = content_selector
        self._schema = extract_schema_org(document, limits)
        self._meta_tags = collect_meta_tags(document)
        self._metadata = extract_metadata(document, self._schema, self._meta_tags, url)
        self._small_images: frozenset[str] | None = None

    def run(self) -> Extraction:
        """Extract the main content, falling back to the sanitized body."""
        body = dom.select_one(self._document, "body")
        if body is not None:
            _resolve_noscript_images(body)
        result = self._with_retries()
        return result if result is not None else self._fallback()

    def _extraction(self, root: Tag | None) -> Extraction:
        serialized = root.decode() if root is not None else ""
        return Extraction(
            root=root,
            word_count=count_html_words(serialized),
            metadata=self._metadata,
            schema_org_data=self._schema,
            meta_tags=self._meta_tags,
        )

    def _fallback(self) -> Extraction:
        clone = copy.copy(self._document)
        body = dom.select_one(clone, "body")
        if body is None:
            return self._extraction(None)
        # Sanitizing runs last so it also sees the URLs that resolving produced.
        _resolve_relative_urls(body, clone, self._url)
        return self._extraction(None if strip_unsafe_elements(body) else body)

    def _with_retries(self) -> Extraction | None:
        result = self._parse_pass(_PassOptions(content_selector=self._content_selector))
        result = self._retry_without_partial(result)
        result = self._retry_hidden(result)
        result = self._retry_index(result)
        return self._retry_schema(result)

    @staticmethod
    def _words(result: Extraction | None) -> int:
        return result.word_count if result is not None else 0

    def _retry_without_partial(self, result: Extraction | None) -> Extraction | None:
        if self._words(result) >= _RETRY_WORDS:
            return result
        retry = self._parse_pass(
            _PassOptions(content_selector=self._content_selector, remove_partial_selectors=False)
        )
        if retry is not None and (result is None or retry.word_count > result.word_count * 2):
            return retry
        return result

    def _retry_hidden(self, result: Extraction | None) -> Extraction | None:
        if self._words(result) >= _LOW_WORDS:
            return result
        retry = self._parse_pass(
            _PassOptions(content_selector=self._content_selector, remove_hidden_elements=False)
        )
        if retry is not None and (result is None or retry.word_count > result.word_count * 2):
            result = retry
        selector = self._largest_hidden_selector()
        if selector is None:
            return result
        focused = self._parse_pass(
            _PassOptions(
                remove_hidden_elements=False,
                remove_partial_selectors=False,
                content_selector=selector,
            )
        )
        if focused is not None and self._focused_is_better(focused, result):
            return focused
        return result

    @staticmethod
    def _focused_is_better(focused: Extraction, result: Extraction | None) -> bool:
        if result is None or focused.word_count > result.word_count:
            return True
        if focused.root is None or result.root is None:
            return False
        floor = max(20, result.word_count * 0.7)
        return focused.word_count > floor and len(focused.root.decode()) < len(result.root.decode())

    def _retry_index(self, result: Extraction | None) -> Extraction | None:
        if self._words(result) >= _LOW_WORDS:
            return result
        retry = self._parse_pass(
            _PassOptions(
                content_selector=self._content_selector,
                remove_low_scoring=False,
                remove_partial_selectors=False,
                remove_content_patterns=False,
            )
        )
        if retry is not None and (result is None or retry.word_count > result.word_count):
            return retry
        return result

    def _retry_schema(self, result: Extraction | None) -> Extraction | None:
        text = schema_text(self._schema)
        if not text or count_html_words(text) <= self._words(result) * _SCHEMA_RATIO:
            return result
        safe_document = copy.copy(self._document)
        safe_body = dom.select_one(safe_document, "body")
        if safe_body is None:
            return result
        strip_unsafe_elements(safe_body)
        match = _find_element_by_schema_text(safe_body, text)
        if match is None:
            logger.debug("Using schema.org text as content")
            return self._extraction(self._schema_text_root(text))
        live_document = self._document
        self._document = safe_document
        try:
            retry = self._parse_pass(_PassOptions(content_selector=_element_selector(match)))
        finally:
            self._document = live_document
        return retry if retry is not None else result

    @staticmethod
    def _schema_text_root(text: str) -> Tag:
        root = dom.parse_fragment(text)
        strip_unsafe_elements(root)
        root.name = "div"
        return root

    def _largest_hidden_selector(self) -> str | None:
        body = dom.select_one(self._document, "body")
        if body is None:
            return None
        best: Tag | None = None
        best_words = 0
        for element in dom.select(body, HIDDEN_EXACT_SKIP_SELECTOR):
            if _MATH_CLASS in dom.class_name(element):
                continue
            words = count_words(dom.text_of(element))
            if words > best_words:
                best = element
                best_words = words
        if best is None or best_words < _MIN_HIDDEN_WORDS:
            return None
        return _element_selector(best)

    def _select_content(self, clone: BeautifulSoup, options: _PassOptions) -> Tag | None:
        found: Tag | None = None
        if options.content_selector:
            try:
                found = dom.select_one(clone, options.content_selector)
            except SelectorSyntaxError:
                logger.warning("Ignoring invalid content selector")
        if found is None:
            found = find_main_content(clone)
        if found is None:
            return None
        ancestor = dom.closest(found, "[data-defuddle]")
        if ancestor is not None:
            found = ancestor
        if found.name == "body":
            text = schema_text(self._schema)
            if text:
                match = _find_element_by_schema_text(found, text)
                if match is not None:
                    return match
        return found

    def _remove_clutter(
        self, clone: BeautifulSoup, main: Tag, options: _PassOptions, small: frozenset[str]
    ) -> None:
        if self._metadata.published or self._metadata.author:
            remove_metadata_block(main)
        for element in dom.by_tag(main, "wbr"):
            dom.remove(element)
        if options.remove_small_images:
            remove_small_images(clone, small)
        if options.remove_hidden_elements:
            remove_hidden_elements(clone)
        if options.remove_content_patterns:
            remove_eyebrow_label(main)
        if options.remove_exact_selectors or options.remove_partial_selectors:
            remove_by_selector(
                clone,
                remove_exact=options.remove_exact_selectors,
                remove_partial=options.remove_partial_selectors,
                main_content=main,
                skip_hidden_exact=not options.remove_hidden_elements,
            )
        if options.remove_low_scoring:
            score_and_remove(clone, main)
        if options.remove_content_patterns:
            remove_by_content_pattern(
                main, self._url, self._metadata.title, self._metadata.description
            )

    def _parse_pass(self, options: _PassOptions) -> Extraction | None:
        if self._small_images is None:
            self._small_images = find_small_images(self._document)
        clone = copy.copy(self._document)
        main = self._select_content(clone, options)
        if main is None:
            return None
        self._remove_clutter(clone, main, options, self._small_images)
        standardize_content(main, self._metadata.title)
        _resolve_relative_urls(main, self._document, self._url)
        _dedupe_figures(main)
        _dedupe_adjacent(main)
        _dedupe_lightbox(main)
        better_image = _remove_cover_image(main, self._metadata.image)
        if better_image:
            self._metadata = replace(self._metadata, image=better_image)
        unsafe_root = strip_unsafe_elements(main)
        return self._extraction(None if unsafe_root else main)
