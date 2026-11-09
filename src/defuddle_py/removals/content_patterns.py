"""Text pattern removal ported from ``src/removals/content-patterns.ts`` of Defuddle.

Every regex here reads text cut by ``bounded``. The patterns target short labels,
so the cut never changes a real match and it caps backtracking on hostile input.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from bs4 import Tag

from defuddle_py import dom
from defuddle_py.constants import CONTENT_ELEMENT_SELECTOR
from defuddle_py.content_boundary import find_content_start, is_above_content_start
from defuddle_py.text import bounded, count_words, normalize_text
from defuddle_py.urls import ParsedUrl, host_without_www, parse_url

_MONTHS: Final = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_I: Final = re.IGNORECASE
_CONTENT_DATE: Final = re.compile(
    rf"(?:(?:{_MONTHS})[a-z]*\s+\d{{1,2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*"
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    _I,
)
_RELATIVE_TIME: Final = re.compile(
    r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b", _I
)
_READ_TIME: Final = re.compile(
    r"\d+\s*min(?:ute)?s?\s+read\b|(?:read(?:ing)?\s+time)\s*:?\s*\d+\s*min(?:ute)?s?\b", _I
)
_STARTS_WITH_BY: Final = re.compile(r"^(?:posted\s+)?by\s+\S", _I)
_METADATA_LABEL: Final = re.compile(r"^(?:date|published|updated|posted|from|to|subject)\s*:", _I)
_BOILERPLATE: Final = (
    re.compile(
        r"^This (?:article|story|piece) (?:appeared|was published|originally appeared) in\b", _I
    ),
    re.compile(r"^A version of this (?:article|story) (?:appeared|was published) in\b", _I),
    re.compile(r"^Originally (?:published|appeared) (?:in|on|at)\b", _I),
    re.compile(r"^Any re-?use permitted\b", _I),
    re.compile(r"^©\s*(?:Copyright\s+)?\d{4}", _I),
    re.compile(r"^Comments?$", _I),
    re.compile(r"^Leave a (?:comment|reply)$", _I),
    re.compile(r"^Loading\.{3}$"),
    re.compile(r"^Affiliate links\b.*\b(?:earn|commission)", _I),
    re.compile(r"\bRead our Comment Policy\b", _I),
    re.compile(r"^Thank you for (?:being part of|joining) our community\b", _I),
)
_NEWSLETTER: Final = re.compile(
    r"\bsubscribe\b[\s\S]{0,40}\bnewsletter\b|\bnewsletter\b[\s\S]{0,40}\bsubscribe\b"
    r"|\bsign[- ]up\b[\s\S]{0,80}\b(?:newsletter|email alert)"
    r"|\b(?:don[’']?t (?:want to )?miss|never miss)\b[\s\S]{0,80}"
    r"\b(?:latest|best|exclusive|reports?|updates?|source)",
    _I,
)
_SOCIAL_COUNTER: Final = re.compile(
    r"^\d+\s+(?:Likes?|Comments?|Shares?|Retweets?|Reposts?|Restacks?)$", _I
)
_TIMEZONE_WIDGET: Final = re.compile(r"^current time in$", _I)
_PINNED_LABEL: Final = re.compile(r"^pinned$", _I)
_AUTHOR_CONTACT_LABEL: Final = re.compile(
    r"^(?:written by|(?:author|contact|reporter|correspondent)s?)$", _I
)
_SHARE_AUTHOR_LABEL: Final = re.compile(r"^(?:share|follow|authors?|written\s+by)$", _I)
_CONTENT_ELEMENT_NO_IMAGE: Final = CONTENT_ELEMENT_SELECTOR.replace("img, picture, ", "")
_EMAIL: Final = re.compile(r"[\w.-]+@[\w.-]+\.\w+")
_PHONE: Final = re.compile(r"\(?\d{3}\)?[\s.‑–-]?\d{3}[\s.‑–-]?\d{4}")
_HEADING_TAG: Final = re.compile(r"^h[1-6]$")
_HEADINGS: Final = "h1, h2, h3, h4, h5, h6"
_RELATED_HEADING: Final = re.compile(
    r"^(?:related (?:posts?|articles?|content|stories|reads?|reading)"
    r"|you (?:might|may|could) (?:also )?(?:like|enjoy|be interested in)"
    r"|read (?:next|more|also)|further reading|see also"
    r"|more (?:from .*|from|articles?|posts?|like this)|more to (?:read|explore)|explore more"
    r"|about (?:the )?author"
    r"|latest (?:news|events?|posts?|articles?|stories)"
    r"(?:\s*[&+]\s*(?:news|events?|posts?|articles?|stories))?)$",
    _I,
)
_CTA_HEADING: Final = re.compile(
    r"^(?:subscribe|sign up|follow us|share this|stay (?:updated|connected)|join (?:us|our)"
    r"|search (?:the |our )?(?:site|blog|archives?|newsroom|website|catalog|store|shop|database))$",
    _I,
)
_RELATED_INTRO: Final = re.compile(r"^for more (?:on|about)\b", _I)
_TOC_LABEL: Final = re.compile(
    r"^(?:table of )?contents$|^on this page$|^in this (?:article|guide|post)$", _I
)
_LISTEN_WIDGET: Final = re.compile(
    r"\blisten\s+to\s+(?:this\s+)?(?:article|story|post|episode|podcast)\b", _I
)
_SENTENCE_PUNCTUATION: Final = re.compile(r"[.!?]")
_SENTENCE_END: Final = re.compile(r"[.!?]$")
_SENTENCE_GAP: Final = re.compile(r"[.!?]\s")
_SHALLOW_PATH: Final = re.compile(r"^/[a-zA-Z0-9_-]+/?$")
_PARENT_INDEX: Final = re.compile(r"^index\.(html?|php)$", _I)
_LOWER_UPPER: Final = re.compile(r"([a-z])([A-Z])")
_SEPARATORS: Final = re.compile(r"[,\s/\-]+")

_FULL_MONTHS: Final = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    "|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_METADATA_STRIP_BASE: Final = (
    re.compile(rf"\b(?:{_FULL_MONTHS})\b", _I),
    re.compile(
        r"\b(?:Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:rs(?:day)?)?|Fri(?:day)?"
        r"|Sat(?:urday)?|Sun(?:day)?)\b",
        _I,
    ),
    re.compile(r"\b\d+(?:st|nd|rd|th)?\b"),
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
)
_READ_TIME_STRIP: Final = (
    *_METADATA_STRIP_BASE,
    re.compile(r"\bmin(?:ute)?s?\b", _I),
    re.compile(r"\bread(?:ing)?\b", _I),
    re.compile(r"\btime\b", _I),
    re.compile(r"\bestimated\b", _I),
    re.compile(r"[/|·•—–\-,:.\s]+"),
)
_BYLINE_STRIP: Final = (
    *_METADATA_STRIP_BASE,
    re.compile(r"\bby\b", _I),
    re.compile(r"[/|·•—–\-,]+"),
)
_INLINE_WRAPPERS: Final = frozenset({"i", "em", "span", "b", "strong", "small"})
_EYEBROW_BLOCKERS: Final = (
    "img, picture, video, iframe, figure, table, pre, code, time, [datetime], "
    "h1, h2, h3, h4, h5, h6, ul, ol, blockquote"
)


def _strip_all(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    for pattern in patterns:
        text = pattern.sub("", text)
    return text


def _words_of(element: Tag) -> int:
    return count_words(dom.text_of(element))


def _link_text_length(links: list[Tag]) -> int:
    return sum(len(dom.trimmed_text(link)) for link in links)


def _is_or_contains_heading(element: Tag) -> bool:
    return _HEADING_TAG.match(element.name) is not None or dom.has(element, _HEADINGS)


def _is_newsletter_element(element: Tag, max_words: int) -> bool:
    text = bounded(dom.trimmed_text(element))
    words = count_words(text)
    if words < 2 or words > max_words or dom.has(element, CONTENT_ELEMENT_SELECTOR):
        return False
    normalized = _LOWER_UPPER.sub(r"\1 \2", text).replace("‘", "'").replace("’", "'")
    return _NEWSLETTER.search(normalized) is not None


def _has_following_prose(element: Tag, min_words: int = 25) -> bool:
    sibling = dom.next_element_sibling(element)
    while sibling is not None:
        if sibling.name == "p" and _words_of(sibling) >= min_words:
            return True
        if any(_words_of(p) >= min_words for p in dom.by_tag(sibling, "p")):
            return True
        sibling = dom.next_element_sibling(sibling)
    return False


def _preceding_prose_words(element: Tag, main_content: Tag) -> int:
    words = 0
    node: Tag | None = element
    while node is not None and node is not main_content:
        sibling = dom.previous_element_sibling(node)
        while sibling is not None:
            if sibling.name == "p":
                words += _words_of(sibling)
            else:
                words += sum(_words_of(p) for p in dom.by_tag(sibling, "p"))
            sibling = dom.previous_element_sibling(sibling)
        node = dom.parent_element(node)
    return words


def _walk_up_to_wrapper(element: Tag, text: str, main_content: Tag) -> Tag:
    target = element
    while True:
        parent = dom.parent_element(target)
        if parent is None or parent is main_content or dom.trimmed_text(parent) != text:
            return target
        target = parent


def _is_labeled_metadata(element: Tag, main_content: Tag) -> bool:
    current: Tag | None = element
    while current is not None and current is not main_content:
        text = dom.trimmed_text(current)
        if count_words(text) > 15:
            return False
        if _METADATA_LABEL.search(bounded(text)):
            return True
        current = dom.parent_element(current)
    return False


def _remove_trailing_siblings(element: Tag, *, remove_self: bool) -> None:
    sibling = dom.next_element_sibling(element)
    while sibling is not None:
        following = dom.next_element_sibling(sibling)
        if dom.element_id(sibling) != "footnotes":
            dom.remove(sibling)
        sibling = following
    if remove_self:
        dom.remove(element)


def _remove_trailing_with_cascade(target: Tag, main_content: Tag) -> None:
    ancestors: list[Tag] = []
    ancestor = dom.parent_element(target)
    while ancestor is not None and ancestor is not main_content:
        ancestors.append(ancestor)
        ancestor = dom.parent_element(ancestor)
    _remove_trailing_siblings(target, remove_self=True)
    for ancestor in ancestors:
        _remove_trailing_siblings(ancestor, remove_self=False)


def _walk_up_isolated(element: Tag, main_content: Tag) -> Tag:
    target = element
    while True:
        parent = dom.parent_element(target)
        if parent is None or parent is main_content:
            return target
        preceding = 0
        sibling = dom.previous_element_sibling(target)
        while sibling is not None and preceding <= 10:
            preceding += _words_of(sibling)
            sibling = dom.previous_element_sibling(sibling)
        if preceding > 10:
            return target
        target = parent


def _remove_thin_preceding_section(target: Tag) -> None:
    previous = dom.previous_element_sibling(target)
    if previous is None or _words_of(previous) >= 50:
        return
    if dom.has(previous, CONTENT_ELEMENT_SELECTOR):
        return
    before = dom.previous_element_sibling(previous)
    if before is not None and _is_or_contains_heading(before):
        return
    dom.remove(previous)


def _metadata_words(block: Tag) -> int:
    kept: list[Tag] = []
    for element in dom.select(block, "h1, h2, h3, time, [aria-label]"):
        if not any(dom.contains(existing, element) for existing in kept):
            kept.append(element)
    return sum(_words_of(element) for element in kept)


def _remove_hero_header(main_content: Tag, content_start: Tag | None) -> None:
    for time in dom.by_tag(main_content, "time"):
        if not is_above_content_start(time, content_start):
            continue
        best: Tag | None = None
        current = dom.parent_element(time)
        while current is not None and current is not main_content:
            if dom.has(current, "h1, h2") and dom.has(current, "time"):
                prose_words = count_words(dom.trimmed_text(current)) - _metadata_words(current)
                if prose_words >= 30:
                    break
                best = current
            current = dom.parent_element(current)
        if best is not None:
            dom.remove(best)
            return


def _is_breadcrumb_list(listing: Tag) -> bool:
    items = dom.by_tag(listing, "li")
    if not 2 <= len(items) <= 8:
        return False
    links = dom.by_tag(listing, "a")
    if not 1 <= len(links) < len(items):
        return False
    if dom.has(listing, "img, p, figure, blockquote"):
        return False
    if any(_words_of(item) > 8 for item in items):
        return False
    has_breadcrumb_link = False
    for link in links:
        href = dom.attr_or_empty(link, "href")
        if href.startswith(("http", "//")):
            return False
        if href == "/" or _SHALLOW_PATH.match(bounded(href)):
            has_breadcrumb_link = True
        if len(dom.text_of(link).split()) > 5:
            return False
    return has_breadcrumb_link


def remove_eyebrow_label(main_content: Tag) -> None:
    """Remove a short category label sitting directly above the first heading."""
    heading = dom.select_one(main_content, "h1") or dom.select_one(main_content, "h2")
    if heading is None:
        return
    current = heading
    while True:
        parent = dom.parent_element(current)
        if (
            parent is None
            or parent is main_content
            or dom.previous_element_sibling(current) is not None
        ):
            break
        current = parent
    previous = dom.previous_element_sibling(current)
    if previous is None:
        return
    text = dom.trimmed_text(previous)
    words = count_words(text)
    if not 1 <= words <= 6 or len(text) > 40:
        return
    if _SENTENCE_PUNCTUATION.search(text) or _CONTENT_DATE.search(text):
        return
    if not dom.has(previous, _EYEBROW_BLOCKERS):
        dom.remove(previous)


def _unwrap_single_child_chain(element: Tag, main_content: Tag) -> Tag:
    target = element
    while True:
        parent = dom.parent_element(target)
        if parent is None or parent is main_content or len(dom.child_elements(parent)) != 1:
            return target
        target = parent


@dataclass(slots=True)
class _Pass:
    main: Tag
    url: str
    title: str
    description: str
    content_start: Tag | None
    content_text: str
    page: ParsedUrl | None

    def is_pre_content(self, element: Tag) -> bool:
        return is_above_content_start(element, self.content_start)

    def is_near_end(self, text: str, limit: int) -> bool:
        position = self.content_text.find(text)
        return len(self.content_text) - (position + len(text)) <= limit


def _remove_breadcrumb_list(state: _Pass) -> None:
    first_list = dom.select_one(state.main, "ul, ol")
    if first_list is not None and _is_breadcrumb_list(first_list):
        dom.remove(_unwrap_single_child_chain(first_list, state.main))


def _remove_promotional_links(main_content: Tag) -> None:
    first_h1 = dom.select_one(main_content, "h1")
    if first_h1 is None:
        return
    for link in dom.select(main_content, "a[href]"):
        if not dom.is_connected(link) or not dom.is_node_before(link, first_h1):
            continue
        if not dom.has(link, "div") or dom.has(link, "img, picture, video"):
            continue
        text = dom.trimmed_text(link)
        if count_words(text) <= 25 and not _SENTENCE_GAP.search(bounded(text)):
            dom.remove(link)


def _remove_audio_widgets(state: _Pass) -> None:
    for media in dom.select(state.main, "audio, video"):
        if not dom.is_connected(media):
            continue
        if not dom.attr(media, "src") and not dom.has(media, "source"):
            continue
        container = media
        while True:
            parent = dom.parent_element(container)
            if parent is None or parent is state.main or _words_of(parent) > 25:
                break
            container = parent
        text = dom.trimmed_text(container)
        is_listen_widget = _LISTEN_WIDGET.search(bounded(text)) is not None
        is_pre_content_player = (
            not is_listen_widget and state.is_pre_content(container) and count_words(text) <= 25
        )
        if is_listen_widget or is_pre_content_player:
            dom.remove(container)


def _count_anchor_links(links: list[Tag], state: _Pass) -> int:
    count = 0
    for link in links:
        href = dom.attr_or_empty(link, "href")
        if href.startswith("#"):
            count += 1
        elif state.page is not None and "#" in href:
            resolved = parse_url(href, state.url)
            if (
                resolved is not None
                and resolved.pathname == state.page.pathname
                and resolved.hostname == state.page.hostname
            ):
                count += 1
    return count


def _remove_table_of_contents(state: _Pass) -> None:
    for listing in dom.select(state.main, "ul, ol"):
        if not dom.is_connected(listing) or dom.closest(listing, "#footnotes") is not None:
            continue
        position = state.content_text.find(dom.trimmed_text(listing)[:60])
        if position < 0 or position > len(state.content_text) * 0.3:
            continue
        links = dom.select(listing, "a[href]")
        if len(links) < 3 or dom.has(listing, CONTENT_ELEMENT_SELECTOR):
            continue
        anchors = _count_anchor_links(links, state)
        if anchors < 3 or anchors / len(links) < 0.8:
            continue
        target = _unwrap_single_child_chain(listing, state.main)
        previous = dom.previous_element_sibling(target)
        if (
            previous is not None
            and _HEADING_TAG.match(previous.name)
            and _TOC_LABEL.search(bounded(dom.trimmed_text(previous)))
        ):
            dom.remove(previous)
        previous = dom.previous_element_sibling(target)
        following = dom.next_element_sibling(target)
        dom.remove(target)
        if previous is not None and previous.name == "hr":
            dom.remove(previous)
        if following is not None and following.name == "hr":
            dom.remove(following)
        return


def _is_header_metadata_block(element: Tag, text: str, words: int, *, has_date: bool) -> bool:
    return (
        element.name in ("div", "p")
        and words <= 10
        and (has_date or _RELATIVE_TIME.search(text) is not None)
        and not _METADATA_LABEL.search(text)
        and not _SENTENCE_PUNCTUATION.search(text)
        and not any(
            _words_of(block) > 8 for block in dom.select(element, "p, h1, h2, h3, h4, h5, h6")
        )
    )


def _is_category_badge(element: Tag, text: str, words: int) -> bool:
    if element.name != "div" or words > 5 or _SENTENCE_PUNCTUATION.search(text):
        return False
    if not dom.has(element, "img"):
        return False
    links = dom.select(element, "a[href]")
    return bool(links) and _link_text_length(links) / (len(text) or 1) >= 0.8


def _is_read_time(element: Tag, text: str, words: int, *, has_date: bool, pre: bool) -> bool:
    if not _READ_TIME.search(text):
        return False
    allowed = not dom.has(element, "p, div, section, article") if has_date else words <= 5 and pre
    return allowed and not _strip_all(text, _READ_TIME_STRIP).strip()


def _is_author_date(text: str) -> bool:
    name_words = _strip_all(text, _BYLINE_STRIP).split()
    return 1 <= len(name_words) <= 4 and all(word[0].isupper() for word in name_words)


@dataclass(slots=True)
class _MetadataScan:
    byline_found: bool = False
    author_date_found: bool = False


def _scan_metadata_candidate(
    element: Tag, state: _Pass, scan: _MetadataScan, normalized: tuple[str, str]
) -> None:
    text = dom.trimmed_text(element)
    words = count_words(text)
    if words > 15 or words == 0 or dom.closest(element, "pre, code") is not None:
        return
    text = bounded(text)
    has_date = _CONTENT_DATE.search(text) is not None
    if has_date and _is_labeled_metadata(element, state.main):
        return

    if _TIMEZONE_WIDGET.search(text) and state.content_text.find(text) <= 300:
        parent = dom.parent_element(element)
        dom.remove(parent if parent is not None and parent is not state.main else element)
        return
    if words == 1 and _PINNED_LABEL.search(text):
        dom.remove(element)
        return

    if (
        words >= 3
        and normalize_text(text) in tuple(value for value in normalized if value)
        and state.is_pre_content(element)
    ):
        dom.remove(element)
        return

    pre = state.is_pre_content(element)
    if (pre and _is_header_metadata_block(element, text, words, has_date=has_date)) or (
        pre and _is_category_badge(element, text, words)
    ):
        dom.remove(element)
    elif (
        not scan.byline_found
        and pre
        and words >= 2
        and _STARTS_WITH_BY.search(text)
        and not _SENTENCE_END.search(text)
    ):
        dom.remove(_walk_up_to_wrapper(element, text, state.main))
        scan.byline_found = True
    elif _is_read_time(element, text, words, has_date=has_date, pre=pre):
        target = element if has_date else _walk_up_to_wrapper(element, text, state.main)
        dom.remove(target)
    elif (
        not scan.author_date_found
        and 2 <= words <= 10
        and has_date
        and pre
        and not _METADATA_LABEL.search(text)
        and _is_author_date(text)
    ):
        dom.remove(_walk_up_to_wrapper(element, text, state.main))
        scan.author_date_found = True
    elif (
        has_date
        and words <= 5
        and pre
        and not _SEPARATORS.sub("", _strip_all(text, _METADATA_STRIP_BASE)).strip()
    ):
        dom.remove(_walk_up_to_wrapper(element, text, state.main))


def _remove_boundary_times(state: _Pass) -> None:
    for time in dom.by_tag(state.main, "time"):
        if not dom.is_connected(time):
            continue
        target = time
        target_text = dom.trimmed_text(target)
        while True:
            parent = dom.parent_element(target)
            if parent is None or parent is state.main:
                break
            parent_text = dom.trimmed_text(parent)
            if parent.name == "p" and parent_text == target_text:
                target = parent
                break
            if parent.name in _INLINE_WRAPPERS and parent_text == target_text:
                target = parent
                target_text = parent_text
                continue
            break
        text = dom.trimmed_text(target)
        if count_words(text) > 10:
            continue
        position = state.content_text.find(text)
        distance_from_end = len(state.content_text) - (position + len(text))
        if position <= 200 or distance_from_end <= 200:
            dom.remove(target)


def _is_metadata_list_shape(listing: Tag, items: list[Tag]) -> bool:
    previous = dom.previous_element_sibling(listing)
    if previous is not None and (
        _is_or_contains_heading(previous) or dom.trimmed_text(previous).endswith(":")
    ):
        return False
    for item in items:
        text = dom.trimmed_text(item)
        if count_words(text) > 8 or _SENTENCE_END.search(text):
            return False
    return True


def _remove_metadata_lists(state: _Pass) -> None:
    for listing in dom.select(state.main, "ul, ol, dl"):
        if not dom.is_connected(listing) or dom.closest(listing, "#footnotes") is not None:
            continue
        is_description_list = listing.name == "dl"
        item_tag = "dd" if is_description_list else "li"
        items = [child for child in dom.child_elements(listing) if child.name == item_tag]
        if len(items) < (1 if is_description_list else 2) or len(items) > 8:
            continue
        text = dom.trimmed_text(listing)
        position = state.content_text.find(text)
        distance_from_end = len(state.content_text) - (position + len(text))
        if position > 500 and distance_from_end > 500:
            continue
        if not _is_metadata_list_shape(listing, items) or count_words(text) > 30:
            continue
        dom.remove(_walk_up_to_wrapper(listing, text, state.main))


def _is_parent_link(link: Tag, state: _Pass, url_path: str) -> bool:
    parsed = parse_url(dom.attr_or_empty(link, "href"), state.url)
    if parsed is None:
        return False
    link_path = parsed.pathname
    link_dir = link_path.rsplit("/", 1)[0] + "/"
    last_segment = link_path.rsplit("/", 1)[-1]
    is_parent_index = _PARENT_INDEX.search(last_segment) is not None and url_path.startswith(
        link_dir
    )
    return (
        link_path != "/"
        and link_path != url_path
        and (url_path.startswith(link_path) or is_parent_index)
    )


def _is_prose_link(element: Tag, text: str, state: _Pass, first_heading: Tag | None) -> bool:
    parent = dom.parent_element(element)
    if (
        not dom.matches(element, "a[href]")
        or parent is None
        or parent is state.main
        or dom.trimmed_text(parent) == text
    ):
        return False
    if dom.closest(element, "p") is not None or first_heading is None:
        return True
    return not dom.is_node_before(element, first_heading)


def _remove_section_breadcrumbs(state: _Pass) -> None:
    url_path = state.page.pathname if state.page is not None else ""
    if not url_path:
        return
    first_heading = dom.select_one(state.main, "h1, h2, h3")
    for element in dom.select(state.main, "div, span, p, a[href]"):
        if not dom.is_connected(element):
            continue
        text = dom.trimmed_text(element)
        if count_words(text) > 10 or dom.has(element, "p, div, section, article"):
            continue
        if _is_prose_link(element, text, state, first_heading):
            continue
        link = element if dom.matches(element, "a[href]") else dom.select_one(element, "a[href]")
        if link is not None and _is_parent_link(link, state, url_path):
            dom.remove(element)


def _has_trailing_content(element: Tag, main_content: Tag) -> bool:
    check: Tag | None = element
    while check is not None and check is not main_content:
        sibling = dom.next_element_sibling(check)
        while sibling is not None:
            if dom.trimmed_text(sibling):
                return True
            sibling = dom.next_element_sibling(sibling)
        check = dom.parent_element(check)
    return False


def _item_is_external(item: Tag, state: _Pass, page_host: str) -> bool:
    links = dom.select(item, "a[href]")
    if not links:
        return False
    for link in links:
        parsed = parse_url(dom.attr_or_empty(link, "href"), state.url)
        if parsed is not None and host_without_www(parsed.hostname) == page_host:
            return False
    return _link_text_length(links) >= len(dom.trimmed_text(item)) * 0.6


def _remove_trailing_external_lists(state: _Pass) -> None:
    page_host = host_without_www(state.page.hostname) if state.page is not None else ""
    if not page_host:
        return
    for heading in dom.select(state.main, "h2, h3, h4, h5, h6"):
        if not dom.is_connected(heading):
            continue
        listing = dom.next_element_sibling(heading)
        if listing is None or listing.name not in ("ul", "ol"):
            continue
        items = [child for child in dom.child_elements(listing) if child.name == "li"]
        if len(items) < 2 or _has_trailing_content(listing, state.main):
            continue
        if all(_item_is_external(item, state, page_host) for item in items):
            dom.remove(listing)
            dom.remove(heading)


def _is_link_dense(paragraph: Tag) -> bool:
    text = " ".join(dom.trimmed_text(paragraph).split())
    links = dom.select(paragraph, "a[href]")
    if not links or _link_text_length(links) / (len(text) or 1) <= 0.6:
        return False
    remainder = text
    for link in links:
        remainder = remainder.replace(dom.trimmed_text(link), "")
    return _SENTENCE_PUNCTUATION.search(bounded(remainder)) is None


def _remove_trailing_related_block(main_content: Tag) -> None:
    last = dom.last_element_child(main_content)
    while last is not None and last.name in ("hr", "br"):
        last = dom.previous_element_sibling(last)
    if last is None or last.name not in ("section", "div", "aside"):
        return
    paragraphs: list[Tag] = []
    for child in dom.child_elements(last):
        if not dom.trimmed_text(child):
            continue
        if child.name == "p":
            paragraphs.append(child)
        elif child.name != "br":
            return
    if len(paragraphs) >= 2 and all(_is_link_dense(p) for p in paragraphs):
        dom.remove(last)


def _remove_trailing_thin_sections(main_content: Tag) -> None:
    total_words = _words_of(main_content)
    if total_words <= 300:
        return
    trailing: list[Tag] = []
    trailing_words = 0
    child = dom.last_element_child(main_content)
    while child is not None:
        if dom.element_id(child) == "footnotes":
            child = dom.previous_element_sibling(child)
            continue
        if child.name == "hr":
            trailing.append(child)
            break
        svg_words = sum(_words_of(svg) for svg in dom.by_tag(child, "svg"))
        words = _words_of(child) - svg_words
        if words > 25:
            break
        trailing_words += words
        trailing.append(child)
        child = dom.previous_element_sibling(child)
    if not trailing or trailing_words >= total_words * 0.15:
        return
    has_heading = any(_is_or_contains_heading(element) for element in trailing)
    has_content = any(dom.has(element, CONTENT_ELEMENT_SELECTOR) for element in trailing)
    prose = sum(1 for element in trailing if element.name == "p" and _words_of(element) > 5)
    if has_heading and not has_content and prose < 2:
        for element in trailing:
            dom.remove(element)


def _truncate_at_boilerplate(element: Tag, main_content: Tag, full_text: str) -> bool:
    target = element
    while True:
        parent = dom.parent_element(target)
        if parent is None or parent is main_content or dom.next_element_sibling(target) is not None:
            break
        target = parent
    if full_text.find(dom.text_of(target)) < 200:
        if target is not element and dom.next_element_sibling(element) is None:
            dom.remove(element)
        return False
    _remove_trailing_with_cascade(target, main_content)
    return True


def _remove_boilerplate(main_content: Tag) -> None:
    full_text = dom.text_of(main_content)
    for element in dom.select(main_content, "p, div, span, section"):
        if not dom.is_connected(element) or dom.closest(element, "pre, code") is not None:
            continue
        text = dom.trimmed_text(element)
        if not 1 <= count_words(text) <= 50:
            continue
        short_text = bounded(text)
        for pattern in _BOILERPLATE:
            if pattern.search(short_text) and _truncate_at_boilerplate(
                element, main_content, full_text
            ):
                break


def _remove_related_sections(state: _Pass) -> None:
    for heading in dom.select(state.main, "h2, h3, h4, h5, h6"):
        if not dom.is_connected(heading):
            continue
        text = dom.trimmed_text(heading)
        short_text = bounded(text)
        is_cta = _CTA_HEADING.search(short_text) is not None
        if not is_cta and not _RELATED_HEADING.search(short_text):
            continue
        if state.content_text.find(text) < 500:
            continue
        target = _walk_up_isolated(heading, state.main)
        if _has_following_prose(target):
            if target is not heading:
                dom.remove(target)
            continue
        if target is heading:
            if not is_cta:
                continue
            _remove_trailing_siblings(heading, remove_self=True)
        else:
            _remove_thin_preceding_section(target)
            _remove_trailing_with_cascade(target, state.main)
        return


def _remove_related_intros(main_content: Tag) -> None:
    for paragraph in dom.by_tag(main_content, "p"):
        if not dom.is_connected(paragraph):
            continue
        text = dom.trimmed_text(paragraph)
        if (
            _RELATED_INTRO.search(bounded(text))
            and count_words(text) <= 20
            and not dom.has(paragraph, CONTENT_ELEMENT_SELECTOR)
        ):
            dom.remove(paragraph)


def _is_card(child: Tag) -> bool:
    return dom.has(child, "img, picture") and (
        dom.has(child, "h2, h3, h4") or dom.has(child, "a[href]")
    )


def _grid_follows_content(element: Tag, children: list[Tag], state: _Pass) -> bool:
    first_text = dom.trimmed_text(children[0])[:30]
    if len(first_text) >= 5:
        return state.content_text.find(first_text) >= 500
    return (
        all(dom.has(child, "a[href]") for child in children)
        and _preceding_prose_words(element, state.main) >= 100
    )


def _remove_card_grids(state: _Pass) -> None:
    content_words = count_words(state.content_text)
    for element in dom.select(state.main, "div, ul, ol"):
        if not dom.is_connected(element):
            continue
        children = dom.child_elements(element)
        if len(children) < 2 or len(dom.select(element, "img, picture")) < 2:
            continue
        cards = sum(1 for child in children if _is_card(child))
        if cards < 2 or cards < len(children) * 0.7:
            continue
        if not _grid_follows_content(element, children, state):
            continue
        grid_words = _words_of(element)
        if content_words > 0 and grid_words / content_words > 0.3:
            continue
        target = _walk_up_isolated(element, state.main)
        if target is element or _words_of(target) > grid_words * 2 + 15:
            continue
        if _has_following_prose(target):
            continue
        _remove_thin_preceding_section(target)
        _remove_trailing_siblings(target, remove_self=True)
        return


def _remove_newsletter_sections(main_content: Tag) -> None:
    for element in dom.select(main_content, "div, section, aside"):
        if not dom.is_connected(element) or dom.closest(element, "pre, code") is not None:
            continue
        if not _is_newsletter_element(element, 60):
            continue
        element_words = _words_of(element)
        target = element
        while True:
            parent = dom.parent_element(target)
            if (
                parent is None
                or parent is main_content
                or _words_of(parent) > element_words * 2 + 15
            ):
                break
            target = parent
        dom.remove(target)
        break
    for listing in dom.by_tag(main_content, "ul"):
        if dom.is_connected(listing) and _is_newsletter_element(listing, 30):
            dom.remove(listing)
            break


def _has_contact_label(element: Tag) -> bool:
    return any(
        _AUTHOR_CONTACT_LABEL.search(bounded(dom.trimmed_text(child)))
        for child in dom.select(element, "div, span, p, dt, dd, li")
    )


def _remove_author_contact_blocks(state: _Pass) -> None:
    for element in dom.select(state.main, "div, section"):
        if not dom.is_connected(element):
            continue
        text = dom.trimmed_text(element)
        if not 2 <= count_words(text) <= 40:
            continue
        position = state.content_text.find(text[:60])
        if position < 0 or len(state.content_text) - (position + len(text)) > 300:
            continue
        if not _has_contact_label(element):
            continue
        short_text = bounded(text)
        if (
            _EMAIL.search(short_text)
            or _PHONE.search(short_text)
            or dom.has(element, 'a[href^="mailto:"]')
        ):
            dom.remove(_walk_up_isolated(element, state.main))
            return


def _remove_share_widgets(main_content: Tag) -> None:
    for element in dom.select(main_content, "p, span, div"):
        if not dom.is_connected(element):
            continue
        if not _SHARE_AUTHOR_LABEL.search(bounded(dom.trimmed_text(element))):
            continue
        container = element
        while True:
            parent = dom.parent_element(container)
            if parent is None or parent is main_content or _words_of(parent) > 15:
                break
            container = parent
        if not dom.has(container, _CONTENT_ELEMENT_NO_IMAGE):
            dom.remove(container)


def _remove_social_counters(state: _Pass) -> None:
    for element in dom.select(state.main, "a, p, div, span"):
        if not dom.is_connected(element):
            continue
        text = dom.trimmed_text(element)
        if not _SOCIAL_COUNTER.search(bounded(text)):
            continue
        if element.name == "a":
            if dom.attr(element, "href"):
                continue
        elif not state.is_near_end(text, 200):
            continue
        dom.remove(_walk_up_to_wrapper(element, text, state.main))


def _remove_trailing_tag_blocks(state: _Pass) -> None:
    for element in dom.by_tag(state.main, "div"):
        if not dom.is_connected(element):
            continue
        text = dom.trimmed_text(element)
        if not 1 <= count_words(text) <= 10:
            continue
        if _SENTENCE_PUNCTUATION.search(text) or dom.has(element, CONTENT_ELEMENT_SELECTOR):
            continue
        if state.content_text.find(text) < 0 or not state.is_near_end(text, 300):
            continue
        links = dom.select(element, "a[href]")
        if links and _link_text_length(links) / (len(text) or 1) >= 0.8:
            dom.remove(element)


def remove_by_content_pattern(main_content: Tag, url: str, title: str, description: str) -> None:
    """Remove metadata, promotions and trailing sections recognized by their text."""
    state = _Pass(
        main=main_content,
        url=url,
        title=title,
        description=description,
        content_start=find_content_start(main_content, title),
        content_text="",
        page=None,
    )
    _remove_breadcrumb_list(state)
    _remove_promotional_links(main_content)
    _remove_hero_header(main_content, state.content_start)
    _remove_audio_widgets(state)

    state.content_text = dom.text_of(main_content)
    state.page = parse_url(url) if url else None
    _remove_table_of_contents(state)

    scan = _MetadataScan()
    normalized = (normalize_text(title), normalize_text(description))
    for element in dom.select(main_content, "p, span, div, time"):
        if dom.is_connected(element):
            _scan_metadata_candidate(element, state, scan, normalized)

    later_steps: tuple[Callable[[_Pass], None], ...] = (
        _remove_boundary_times,
        _remove_metadata_lists,
        _remove_section_breadcrumbs,
        _remove_trailing_external_lists,
    )
    for step in later_steps:
        step(state)
    _remove_trailing_related_block(main_content)
    _remove_trailing_thin_sections(main_content)
    _remove_boilerplate(main_content)
    _remove_related_sections(state)
    _remove_related_intros(main_content)
    _remove_card_grids(state)
    _remove_newsletter_sections(main_content)
    _remove_author_contact_blocks(state)
    _remove_share_widgets(main_content)
    _remove_social_counters(state)
    _remove_trailing_tag_blocks(state)
