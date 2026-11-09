"""WHATWG-style URL helpers built on ``urllib.parse``."""

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    """The URL parts the pipeline compares."""

    href: str
    hostname: str
    pathname: str
    origin: str


def parse_url(url: str, base: str | None = None) -> ParsedUrl | None:
    """Parse ``url`` against ``base``, returning None when it is not absolute."""
    try:
        joined = urljoin(base, url) if base else url
        parts = urlsplit(joined)
        hostname = parts.hostname or ""
    except ValueError:
        return None
    if not parts.scheme:
        return None
    path = parts.path
    if parts.netloc and not path:
        path = "/"
    href = urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    origin = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
    return ParsedUrl(href=href, hostname=hostname, pathname=path, origin=origin)


def host_without_www(hostname: str) -> str:
    """Drop a leading ``www.``."""
    return hostname.removeprefix("www.")
