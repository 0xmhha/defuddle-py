"""Compare defuddle-py with the expected output of a local Defuddle checkout.

Fixtures are read from the directory in DEFUDDLE_SOURCE_DIR and never copied here.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Final

from defuddle_py import DefuddleError, parse

SOURCE_ENV: Final = "DEFUDDLE_SOURCE_DIR"
BASELINE_COMMIT: Final = "3e8bde9"
SIMILARITY_THRESHOLD: Final = 0.9
PASS_RATE_THRESHOLD: Final = 0.9
JUDGED_PREFIXES: Final = (
    "content-patterns",
    "scoring",
    "entry-point",
    "hidden",
    "selectors",
    "small-images",
    "eyebrow",
    "metadata",
    "comments",
    "security",
    "related",
    "listing",
    "gated-content",
    "custom-elements",
    "table-layout",
    "headings",
)
SECURITY_FIXTURES: Final = ("security--markdown-image-escaping", "security--math-literal-text")
METADATA_KEYS: Final = ("title", "author", "site", "published")
_PREAMBLE: Final = re.compile(r"^```json\n(.*?)\n```\n?", re.DOTALL)
_FRONTMATTER: Final = re.compile(r"<!--\s*(\{\"url\":.*?\})\s*-->")
_IMAGE_OR_LINK: Final = re.compile(
    r"!?\[((?:[^\[\]\\]|\\.)*)\]\((?:<[^>\n]*>|(?:[^()\\\s]|\\.)*)(?:\s+\"[^\"\n]*\")?\)"
)
_HTML_TAG: Final = re.compile(r"</?[a-zA-Z][^>\n]*>")
_WORD: Final = re.compile(r"\w+")
_SHORT_COMMIT_CHARS: Final = 7


@dataclass(frozen=True, slots=True)
class FixtureScore:
    """The comparison result of one fixture."""

    name: str
    metadata_match: bool
    similarity: float
    mismatched_keys: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Whether the fixture meets both criteria."""
        return self.metadata_match and self.similarity >= SIMILARITY_THRESHOLD


def source_dir() -> Path | None:
    """Return the configured checkout, or None when it is not set."""
    value = os.environ.get(SOURCE_ENV)
    return Path(value) if value else None


def checkout_commit(root: Path) -> str:
    """Return the short commit of the checkout by reading its git metadata."""
    git_dir = root / ".git"
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head[:_SHORT_COMMIT_CHARS]
    ref = head.removeprefix("ref: ")
    ref_path = git_dir / ref
    if ref_path.exists():
        return ref_path.read_text(encoding="utf-8").strip()[:_SHORT_COMMIT_CHARS]
    packed = git_dir / "packed-refs"
    for line in packed.read_text(encoding="utf-8").splitlines():
        if line.endswith(f" {ref}"):
            return line[:_SHORT_COMMIT_CHARS]
    return ""


def word_list(markdown: str) -> list[str]:
    """Strip Markdown formatting and return the lowercased words."""
    text = markdown
    for _ in range(3):
        text = _IMAGE_OR_LINK.sub(r" \1 ", text)
    text = _HTML_TAG.sub(" ", text)
    return [word.lower() for word in _WORD.findall(text)]


def similarity(actual: str, expected: str) -> float:
    """Return the SequenceMatcher ratio of the two word lists."""
    return SequenceMatcher(None, word_list(actual), word_list(expected), autojunk=False).ratio()


def split_expected(text: str) -> tuple[dict[str, str], str]:
    """Split an expected file into its JSON preamble and Markdown body."""
    match = _PREAMBLE.match(text)
    if match is None:
        return {}, text
    metadata: dict[str, str] = json.loads(match.group(1))
    return metadata, text[match.end() :]


def fixture_url(name: str, html: str) -> str:
    """Return the page address the same way the upstream test does."""
    match = _FRONTMATTER.search(html)
    if match:
        url = json.loads(match.group(1)).get("url")
        if isinstance(url, str):
            return url
    return "https://" + re.sub(r"^[a-z]+--", "", name)


def score_fixture(root: Path, name: str) -> FixtureScore | None:
    """Run one fixture, returning None when it has no expected Markdown."""
    expected_path = root / "tests" / "expected" / f"{name}.md"
    if not expected_path.exists():
        return None
    html = (root / "tests" / "fixtures" / f"{name}.html").read_text(encoding="utf-8")
    metadata, body = split_expected(expected_path.read_text(encoding="utf-8"))
    try:
        result = parse(html, url=fixture_url(name, html))
    except DefuddleError as err:
        print(f"{name} failed: {err}", file=sys.stderr)
        return FixtureScore(name, False, 0.0, METADATA_KEYS)
    mismatched = tuple(
        key for key in METADATA_KEYS if key in metadata and getattr(result, key) != metadata[key]
    )
    return FixtureScore(
        name=name,
        metadata_match=not mismatched,
        similarity=similarity(result.content_markdown, body),
        mismatched_keys=mismatched,
    )


def fixture_names(root: Path) -> list[str]:
    """List every fixture name."""
    return sorted(path.stem for path in (root / "tests" / "fixtures").glob("*.html"))


def is_judged(name: str) -> bool:
    """Return whether the fixture counts toward completion."""
    return name.split("--", 1)[0] in JUDGED_PREFIXES


def render(scores: list[FixtureScore]) -> str:
    """Render scores as a Markdown table."""
    lines = ["| fixture | judged | metadata | similarity | result |", "|---|---|---|---|---|"]
    for score in scores:
        metadata = "ok" if score.metadata_match else ",".join(score.mismatched_keys)
        result = "pass" if score.passed else "fail"
        judged = "yes" if is_judged(score.name) else "no"
        lines.append(
            f"| {score.name} | {judged} | {metadata} | {score.similarity:.3f} | {result} |"
        )
    return "\n".join(lines)


def main() -> int:
    """Print the report and return 0 when the judged set meets the bar."""
    root = source_dir()
    if root is None:
        print(f"{SOURCE_ENV} is not set", file=sys.stderr)
        return 2
    commit = checkout_commit(root)
    if commit != BASELINE_COMMIT:
        print(f"warning: checkout is at {commit}, baseline is {BASELINE_COMMIT}", file=sys.stderr)
    scores = [score for name in fixture_names(root) if (score := score_fixture(root, name))]
    print(render(scores))
    judged = [score for score in scores if is_judged(score.name)]
    passed = sum(1 for score in judged if score.passed)
    rate = passed / len(judged) if judged else 0.0
    overall = sum(1 for score in scores if score.passed)
    print(f"\njudged: {passed}/{len(judged)} = {rate:.1%}")
    print(f"all: {overall}/{len(scores)} = {overall / len(scores):.1%}")
    return 0 if rate >= PASS_RATE_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
