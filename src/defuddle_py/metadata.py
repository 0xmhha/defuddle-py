"""Page metadata extraction ported from ``src/metadata.ts`` of Defuddle."""

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from bs4 import BeautifulSoup, Tag

from defuddle_py import dom
from defuddle_py.schema_org import JsonValue, schema_property
from defuddle_py.text import bounded, collapse_whitespace, count_words
from defuddle_py.urls import host_without_www, parse_url

_I: Final = re.IGNORECASE
_MONTH_NAMES: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_NUMBERS: Final = {name.lower(): f"{i:02d}" for i, name in enumerate(_MONTH_NAMES, 1)}
_MONTH_ALTERNATION: Final = "|".join(_MONTH_NAMES)
_DAY_MONTH_YEAR: Final = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALTERNATION})\s+(\d{{4}})\b", _I)
_MONTH_DAY_YEAR: Final = re.compile(rf"\b({_MONTH_ALTERNATION})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", _I)
_BYLINE: Final = re.compile(r"^By\s+([A-Z].+)$", _I)
_BY_PREFIX: Final = re.compile(r"^by\s+", _I)
_URL_IN_AUTHOR: Final = re.compile(r"\(?\s*https?://\S+\s*\)?", _I)
_AND_SEPARATOR: Final = re.compile(r",?\s+and\s+", _I)
_TRAILING_SEPARATOR: Final = re.compile(r"\s*[-–—|]\s*$")
_COMMA_NAME: Final = re.compile(r"(.*),\s(.*)")
_PLACEHOLDER: Final = re.compile(r"[{}]|^#[a-zA-Z]")
_NON_ALNUM: Final = re.compile(r"[^a-z0-9]")
_TLD: Final = re.compile(r"\.[^.]+$")
_SEPARATOR_CLASS: Final = r"[|\-–—/·]"
_ANY_SEPARATOR: Final = re.compile(rf"\s+{_SEPARATOR_CLASS}\s+")
_STRONG_SEPARATOR: Final = re.compile(r"\s+([|/·])\s+")
_DASH_SEPARATOR: Final = re.compile(r"\s+[-–—]\s+")
_HTTP_URL: Final = re.compile(r"^https?://")
_MAX_NAME_CHARS: Final = 100
_MAX_AUTHORS: Final = 10
_MAX_SITE_WORDS: Final = 6


@dataclass(frozen=True, slots=True)
class MetaTag:
    """One ``<meta>`` tag with content."""

    name: str | None
    property: str | None
    content: str


@dataclass(frozen=True, slots=True)
class Metadata:
    """Facts about a page, read from metadata and the document."""

    title: str
    description: str
    domain: str
    favicon: str
    image: str
    language: str
    published: str
    author: str
    site: str


def collect_meta_tags(document: BeautifulSoup) -> list[MetaTag]:
    """Read every ``<meta>`` tag that has content."""
    tags: list[MetaTag] = []
    for meta in dom.by_tag(document, "meta"):
        content = dom.attr(meta, "content")
        if content:
            tags.append(
                MetaTag(
                    name=dom.attr(meta, "name"),
                    property=dom.attr(meta, "property"),
                    content=html.unescape(bounded(content)),
                )
            )
    return tags


def _is_placeholder(value: str) -> bool:
    if _PLACEHOLDER.search(value):
        return True
    return not any(char.isalnum() for char in value)


def _first_valid(sources: Sequence[Callable[[], str]]) -> str:
    for source in sources:
        value = source()
        if value and not _is_placeholder(value):
            return value
    return ""


def _meta_contents(tags: list[MetaTag], attribute: str, value: str) -> list[str]:
    lowered = value.lower()
    found: list[str] = []
    for tag in tags:
        actual = tag.name if attribute == "name" else tag.property
        if actual is not None and actual.lower() == lowered:
            found.append(tag.content.strip())
    return found


def _meta(tags: list[MetaTag], attribute: str, value: str) -> str:
    found = _meta_contents(tags, attribute, value)
    return found[0] if found else ""


def parse_date_text(text: str) -> str:
    """Turn a written date into an ISO timestamp, or return an empty string."""
    short_text = bounded(text)
    match = _DAY_MONTH_YEAR.search(short_text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{_MONTH_NUMBERS[month.lower()]}-{day.zfill(2)}T00:00:00+00:00"
    match = _MONTH_DAY_YEAR.search(short_text)
    if match:
        month, day, year = match.groups()
        return f"{year}-{_MONTH_NUMBERS[month.lower()]}-{day.zfill(2)}T00:00:00+00:00"
    return ""


def _clean_author_string(value: str) -> str:
    cleaned = _BY_PREFIX.sub("", collapse_whitespace(value))
    cleaned = _URL_IN_AUTHOR.sub("", cleaned)
    cleaned = _AND_SEPARATOR.sub(", ", cleaned)
    return _TRAILING_SEPARATOR.sub("", cleaned).strip()


def _visible_text(element: Tag) -> str:
    excluded = dom.select(element, "script, style, noscript")
    if not excluded:
        return collapse_whitespace(dom.text_of(element))
    hidden = dom.ElementSet(excluded)
    pieces: list[str] = []
    for node in element.descendants:
        if isinstance(node, Tag):
            continue
        if any(parent in hidden for parent in node.parents):
            continue
        if dom.is_text_node(node):
            pieces.append(str(node))
    return collapse_whitespace("".join(pieces))


def _author_name(element: Tag) -> str:
    text = _visible_text(element)
    if not text:
        return ""
    for child in dom.select(element, "span, a, p"):
        child_text = collapse_whitespace(dom.text_of(child))
        if 2 <= len(child_text) <= 50 and child_text != text:
            return child_text
    return text if len(text) <= _MAX_NAME_CHARS else ""


def _unique_limited(names: list[str]) -> str:
    return ", ".join(list(dict.fromkeys(names))[:_MAX_AUTHORS])


def _flip_comma_name(name: str) -> str:
    if "," not in name:
        return name.strip()
    match = _COMMA_NAME.search(bounded(name))
    return f"{match.group(2)} {match.group(1)}" if match else name.strip()


def _authors_from_meta(tags: list[MetaTag]) -> str:
    candidate = _first_valid(
        [
            lambda: _meta(tags, "name", "sailthru.author"),
            lambda: _meta(tags, "property", "article:author"),
            lambda: _meta(tags, "property", "author"),
            lambda: _meta(tags, "name", "author"),
            lambda: _meta(tags, "name", "byl"),
            lambda: _meta(tags, "name", "authorList"),
        ]
    )
    if candidate:
        cleaned = _clean_author_string(candidate)
        if cleaned:
            return cleaned
    citation = _meta_contents(tags, "name", "citation_author")
    names = [name for name in citation if not _is_placeholder(name)]
    if not names:
        creators = _meta_contents(tags, "property", "dc.creator")
        names = [name for name in creators if not _is_placeholder(name)]
    return ", ".join(_flip_comma_name(name) for name in names)


def _authors_from_schema(schema: JsonValue) -> str:
    joined = schema_property(schema, "author.name") or schema_property(schema, "author.[].name")
    if not joined:
        return ""
    parts = [part.strip() for part in joined.split(",")]
    return _unique_limited([part for part in parts if part and not _is_placeholder(part)])


def _authors_from_rel(document: BeautifulSoup) -> str:
    elements = dom.select(document, 'a[rel~="author"], address[rel~="author"]')
    if not 0 < len(elements) <= 3:
        return ""
    names: list[str] = []
    for element in elements:
        text = _visible_text(element)
        if (
            text
            and len(text) < _MAX_NAME_CHARS
            and text.lower() not in ("author", "authors")
            and not _is_placeholder(text)
        ):
            names.append(text)
    return ", ".join(dict.fromkeys(names))


def _split_dom_author(value: str) -> list[str]:
    names: list[str] = []
    for part in value.split(","):
        cleaned = collapse_whitespace(part)
        if (
            cleaned
            and cleaned.lower() not in ("author", "authors")
            and not _is_placeholder(cleaned)
        ):
            names.append(cleaned)
    return names


def _authors_from_dom(document: BeautifulSoup) -> str:
    collected: list[str] = []
    selectors: tuple[tuple[str, int | None], ...] = (
        ('[itemprop="author"]', None),
        (".author", 3),
        ('[href*="/author/"]', 3),
        (".authors a", 3),
    )
    for selector, max_matches in selectors:
        matches = dom.select(document, selector)
        if max_matches is not None and len(matches) > max_matches:
            continue
        for element in matches:
            collected.extend(_split_dom_author(_author_name(element)))
    unique = list(dict.fromkeys(collected))
    if len(unique) > 1:
        unique = [a for a in unique if not any(b != a and b in a for b in unique)]
    return _unique_limited(unique)


def _sibling_date_author(sibling: Tag) -> str:
    text = dom.trimmed_text(sibling)
    children = dom.select(sibling, "p, time")
    has_date_child = any(parse_date_text(dom.trimmed_text(child)) for child in children)
    if not parse_date_text(text) and not has_date_child:
        return ""
    links = dom.by_tag(sibling, "a")
    if len(links) == 1:
        link_text = dom.trimmed_text(links[0]).replace(" ", " ")
        if 0 < len(link_text) < _MAX_NAME_CHARS and not parse_date_text(link_text):
            return link_text
    if has_date_child and len(text) < 300:
        for child in children:
            if child.name != "p":
                continue
            child_text = dom.trimmed_text(child).replace(" ", " ")
            if 0 < len(child_text) < 150 and not parse_date_text(child_text):
                return child_text
    return ""


def _extract_byline(element: Tag) -> str:
    for candidate in [element, *dom.select(element, "p, span, address")]:
        text = dom.trimmed_text(candidate).replace(" ", " ")
        if 0 < len(text) < 50:
            match = _BYLINE.search(text)
            if match:
                return match.group(1).strip()
    return ""


def _byline_near(scope: Tag) -> str:
    for step in (dom.previous_element_sibling, dom.next_element_sibling):
        candidate = step(scope)
        for _ in range(3):
            if candidate is None:
                break
            byline = _extract_byline(candidate)
            if byline:
                return byline
            candidate = step(candidate)
    return ""


def _author_near_heading(document: BeautifulSoup) -> str:
    heading = dom.select_one(document, "h1")
    if heading is None:
        return ""
    sibling = dom.next_element_sibling(heading)
    for _ in range(3):
        if sibling is None:
            break
        found = _sibling_date_author(sibling)
        if found:
            return found
        sibling = dom.next_element_sibling(sibling)
    scope: Tag | None = heading
    for _ in range(3):
        if scope is None:
            break
        byline = _byline_near(scope)
        if byline:
            return byline
        scope = dom.parent_element(scope)
    return ""


def _author(document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag]) -> str:
    return (
        _authors_from_meta(tags)
        or _authors_from_schema(schema)
        or _authors_from_rel(document)
        or _authors_from_dom(document)
        or _author_near_heading(document)
    )


def _site_name(schema: JsonValue, tags: list[MetaTag]) -> str:
    candidate = _first_valid(
        [
            lambda: schema_property(schema, "publisher.name"),
            lambda: _meta(tags, "property", "og:site_name"),
            lambda: _meta(tags, "name", "og:site_name"),
            lambda: schema_property(schema, "WebSite.name"),
            lambda: schema_property(schema, "sourceOrganization.name"),
            lambda: _meta(tags, "name", "copyright"),
            lambda: schema_property(schema, "copyrightHolder.name"),
            lambda: schema_property(schema, "isPartOf.name"),
            lambda: _meta(tags, "name", "application-name"),
        ]
    )
    return "" if count_words(candidate) > _MAX_SITE_WORDS else candidate


def _is_site_identifier(candidate: str, author: str, site: str, domain: str) -> bool:
    normalized = candidate.strip().lower()
    if (author and normalized == author) or (site and normalized == site):
        return True
    return bool(domain) and _NON_ALNUM.sub("", normalized) == domain


def _best_title(
    document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag], domain: str, site: str
) -> str:
    title_element = dom.select_one(document, "title")
    heading = dom.select_one(document, "h1")
    candidates = [
        _meta(tags, "property", "og:title"),
        _meta(tags, "name", "twitter:title"),
        schema_property(schema, "headline"),
        _meta(tags, "name", "title"),
        _meta(tags, "name", "sailthru.title"),
        dom.trimmed_text(title_element) if title_element is not None else "",
        dom.trimmed_text(heading) if heading is not None else "",
    ]
    usable = [bounded(c) for c in candidates if c and not _is_placeholder(bounded(c))]
    if not usable:
        return ""
    author = (_meta(tags, "property", "author") or _meta(tags, "name", "author")).strip().lower()
    site_normalized = site.strip().lower()
    domain_normalized = _NON_ALNUM.sub("", _TLD.sub("", domain).lower()) if domain else ""
    for candidate in usable:
        if not _is_site_identifier(candidate, author, site_normalized, domain_normalized):
            return candidate
    return usable[0]


def _positions(pattern: re.Pattern[str], title: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in pattern.finditer(title)]


def _strip_known_site(title: str, site: str) -> str | None:
    escaped = re.escape(site)
    for pattern in (
        rf"\s*{_SEPARATOR_CLASS}\s*{escaped}\s*$",
        rf"^\s*{escaped}\s*{_SEPARATOR_CLASS}\s*",
    ):
        regex = re.compile(pattern, _I)
        if regex.search(title):
            return regex.sub("", title).strip()
    positions = _positions(_ANY_SEPARATOR, title)
    if not positions:
        return None
    site_lower = site.lower()
    last_start, last_end = positions[-1]
    last_segment = title[last_end:].strip().lower()
    if last_segment and last_segment in site_lower:
        cut = last_start
        for start, end in reversed(positions[:-1]):
            if count_words(title[end:cut].strip()) > 3:
                break
            cut = start
        return title[:cut].strip()
    first_start, first_end = positions[0]
    prefix = title[:first_start].strip().lower()
    if prefix and prefix in site_lower:
        cut = first_end
        for start, end in positions[1:]:
            if count_words(title[cut:start].strip()) > 3:
                break
            cut = end
        return title[cut:].strip()
    return None


def _try_split(
    title: str,
    pattern: re.Pattern[str],
    guard: Callable[[int, int], bool],
    *,
    suffix_only: bool,
) -> tuple[str, str] | None:
    positions = _positions(pattern, title)
    if not positions:
        return None
    start, end = positions[-1]
    suffix_title, suffix_site = title[:start].strip(), title[end:].strip()
    if guard(count_words(suffix_title), count_words(suffix_site)):
        return suffix_title, suffix_site
    if suffix_only:
        return None
    start, end = positions[0]
    prefix_site, prefix_title = title[:start].strip(), title[end:].strip()
    if guard(count_words(prefix_title), count_words(prefix_site)):
        return prefix_title, prefix_site
    return None


def _clean_title(raw_title: str, site: str) -> tuple[str, str]:
    title = collapse_whitespace(raw_title)
    if not title:
        return title, ""
    if site and site.lower() != title.lower() and count_words(site) <= _MAX_SITE_WORDS:
        stripped = _strip_known_site(title, site)
        if stripped is not None:
            return stripped, site
    strong = _try_split(
        title,
        _STRONG_SEPARATOR,
        lambda title_words, site_words: (
            site_words <= 3 and title_words >= 2 and title_words >= site_words * 2
        ),
        suffix_only=False,
    )
    if strong:
        return strong
    dash = _try_split(
        title,
        _DASH_SEPARATOR,
        lambda title_words, site_words: (
            site_words <= 2 and title_words >= 2 and title_words > site_words
        ),
        suffix_only=True,
    )
    return dash if dash else (title, "")


def _description(schema: JsonValue, tags: list[MetaTag]) -> str:
    return _first_valid(
        [
            lambda: _meta(tags, "name", "description"),
            lambda: _meta(tags, "property", "description"),
            lambda: _meta(tags, "property", "og:description"),
            lambda: schema_property(schema, "description"),
            lambda: _meta(tags, "name", "twitter:description"),
            lambda: _meta(tags, "name", "sailthru.description"),
        ]
    )


def _image(schema: JsonValue, tags: list[MetaTag]) -> str:
    return (
        _meta(tags, "property", "og:image")
        or _meta(tags, "name", "twitter:image")
        or schema_property(schema, "image.url")
        or _meta(tags, "name", "sailthru.image.full")
    )


def _language(document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag]) -> str:
    root = dom.select_one(document, "html")
    language = dom.attr_or_empty(root, "lang").strip() if root is not None else ""
    if not language:
        language = _meta(tags, "name", "content-language") or _meta(tags, "property", "og:locale")
    if not language:
        element = dom.select_one(document, 'meta[http-equiv="Content-Language" i]')
        language = dom.attr_or_empty(element, "content").strip() if element is not None else ""
    if not language:
        language = schema_property(schema, "inLanguage")
    return language.replace("_", "-")


def _favicon(document: BeautifulSoup, base_url: str, tags: list[MetaTag]) -> str:
    from_meta = _meta(tags, "property", "og:image:favicon")
    if from_meta:
        return from_meta
    for selector in ("link[rel='icon']", "link[rel='shortcut icon']"):
        link = dom.select_one(document, selector)
        href = dom.attr(link, "href") if link is not None else None
        if href:
            return href
    if base_url and _HTTP_URL.match(base_url):
        parsed = parse_url("/favicon.ico", base_url)
        return parsed.href if parsed is not None else ""
    return ""


def _is_linked_to_other_page(element: Tag, page_url: str) -> bool:
    if not page_url:
        return False
    anchor = dom.closest(element, "a[href]")
    if anchor is None:
        return False
    href = dom.attr_or_empty(anchor, "href").strip()
    if not href or href.startswith("#"):
        return False
    target = parse_url(href, page_url)
    current = parse_url(page_url)
    if target is None or current is None or target.origin != current.origin:
        return False
    return target.pathname.rstrip("/") != current.pathname.rstrip("/")


def _time_element(document: BeautifulSoup, url: str) -> str:
    for element in dom.by_tag(document, "time"):
        if _is_linked_to_other_page(element, url):
            continue
        content = dom.attr_or_empty(element, "datetime").strip() or dom.trimmed_text(element)
        if content:
            return content
    return ""


def _scan_for_date(
    start: Tag | None, step: Callable[[Tag], Tag | None], *, children_only: bool
) -> str:
    sibling = start
    for _ in range(3):
        if sibling is None:
            break
        for child in dom.select(sibling, "p, time"):
            parsed = parse_date_text(dom.trimmed_text(child))
            if parsed:
                return parsed
        if not children_only:
            parsed = parse_date_text(dom.trimmed_text(sibling))
            if parsed:
                return parsed
        sibling = step(sibling)
    return ""


def _abbr_published(document: BeautifulSoup) -> str:
    element = dom.select_one(document, 'abbr[itemprop="datePublished"]')
    return dom.attr_or_empty(element, "title").strip() if element is not None else ""


def _published(document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag], url: str) -> str:
    result = _first_valid(
        [
            lambda: schema_property(schema, "datePublished"),
            lambda: _meta(tags, "name", "publishDate"),
            lambda: _meta(tags, "property", "article:published_time"),
            lambda: _abbr_published(document),
            lambda: _time_element(document, url),
            lambda: _meta(tags, "name", "sailthru.date"),
        ]
    )
    if result:
        return result
    heading = dom.select_one(document, "h1")
    if heading is None:
        return ""
    return _first_valid(
        [
            lambda: _scan_for_date(
                dom.next_element_sibling(heading), dom.next_element_sibling, children_only=False
            ),
            lambda: _scan_for_date(
                dom.previous_element_sibling(heading),
                dom.previous_element_sibling,
                children_only=True,
            ),
        ]
    )


def _page_url(
    document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag], page_url: str
) -> str:
    if page_url:
        return page_url
    canonical = dom.select_one(document, 'link[rel="canonical"]')
    return (
        _meta(tags, "property", "og:url")
        or _meta(tags, "property", "twitter:url")
        or schema_property(schema, "url")
        or schema_property(schema, "mainEntityOfPage.url")
        or schema_property(schema, "mainEntity.url")
        or schema_property(schema, "WebSite.url")
        or (dom.attr_or_empty(canonical, "href") if canonical is not None else "")
    )


def extract_metadata(
    document: BeautifulSoup, schema: JsonValue, tags: list[MetaTag], page_url: str
) -> Metadata:
    """Collect title, author, date and related facts about the page."""
    url = _page_url(document, schema, tags, page_url)
    parsed = parse_url(url) if url else None
    domain = host_without_www(parsed.hostname) if parsed is not None else ""
    site_name = _site_name(schema, tags)
    title, detected_site = _clean_title(
        _best_title(document, schema, tags, domain, site_name), site_name
    )
    author = _author(document, schema, tags)
    author_as_site = author if author and "," not in author else ""
    return Metadata(
        title=title,
        description=_description(schema, tags),
        domain=domain,
        favicon=_favicon(document, url, tags),
        image=_image(schema, tags),
        language=_language(document, schema, tags),
        published=_published(document, schema, tags, url),
        author=author,
        site=site_name or detected_site or author_as_site or domain,
    )
