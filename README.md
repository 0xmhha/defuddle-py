# defuddle-py

A Python port of the core extraction algorithm of [Defuddle](https://github.com/kepano/defuddle)
by Steph Ango. It takes an HTML page and returns the main content as sanitized HTML and
Markdown, with page metadata.

This is an independent port, not an official Defuddle project. It tracks Defuddle commit
`3e8bde9` (version 0.19.6). Original work is under the MIT License, and the notice is kept
in [LICENSE](LICENSE).

## Scope

Ported:

- Main content detection: entry point scoring, table layouts, block scoring
- Clutter removal: small images, hidden elements, selectors, low scoring blocks, text patterns
- Retries when a pass finds little content, including the schema.org text fallback
- Metadata and schema.org reading
- Sanitization of the output

Not ported:

- Footnote, math, code block and callout standardization
- Site-specific extractors
- Fetching URLs. This package makes no network calls
- Browser-only behavior: computed styles, layout measurement, shadow DOM

Markdown comes from [markdownify](https://github.com/matthewwithanm/python-markdownify) rather
than the Turndown rule set, so formatting differs from Defuddle in detail.

## Usage

```bash
uv tool install git+https://github.com/0xmhha/defuddle-py
defuddle-py parse page.html --md
curl -s https://example.com/ | defuddle-py parse - --json --url https://example.com/
```

```python
from defuddle_py import parse

result = parse(html, url="https://example.com/post")
print(result.title, result.author)
print(result.content_markdown)
```

## Security

Input is treated as hostile.

- Output is always sanitized: scripts, styles, event handlers, `srcdoc` and dangerous URL
  schemes are removed, whatever the pipeline options are.
- Markdown drops dangerous links and escapes link, image and video syntax. Code inside links,
  emphasis or table cells becomes a code span. Inline code holding `<` or `](` becomes escaped
  text, because renderers disagree about where a code span starts. It loses its code style.
- Text writes `<` as `&lt;`, not `\<`, because an autolink can take the backslash into a URL.
- The Markdown is parsed again before it is returned. If it holds raw HTML or a dangerous link,
  it is converted again with code as plain text, and dropped if that also fails. This check uses
  one parser, so it cannot rule out a renderer that reads Markdown differently.
- Code blocks are written as backtick fences, as in Defuddle, and keep their text, including `<`.
  A renderer that does not read a fence as code reads its text as Markdown, so links such as
  `javascript:` URLs and raw HTML inside the code become active. Python-Markdown does this without
  the `fenced_code` extension, and with it (or `extra`) for fences inside lists and block quotes.
  Sanitize the rendered HTML of untrusted pages, for example with `nh3`. Turning raw HTML off is
  not enough: links and extension syntax still pass.
- Tests reparse the HTML and render the Markdown, then check that nothing active survives. The
  attack tests run with the check above turned off, so they test the conversion itself.
- `title`, `description`, `image`, `favicon` and other metadata are page text. Escape and validate
  them before use: `image` and `favicon` can hold a `javascript:` URL.
- Input over 10 MiB raises `InputTooLargeError`. Nesting over 256 levels raises
  `TreeTooDeepError`. Both are configurable through `Limits`.
- JSON-LD blocks over 1 MiB or 64 levels are skipped.
- Regexes run on bounded text. Tests time them against hostile input.
- The package does not use `eval`, `pickle` or subprocesses, and it opens no sockets.

Parsing stops after 180 seconds by default (`--timeout`, `Limits.timeout_seconds`). The limit uses
a timer signal, so it needs the main thread on a POSIX system. Elsewhere it is logged and not
enforced, and it does not cap memory. Run the tool under a CPU and memory limit when it handles
untrusted pages at scale.

Bytes are decoded in this order: byte order mark, `--encoding`, the page's `<meta>` declaration, a
UTF-8 check, then windows-1252. Pass the charset from a `Content-Type` header with `--encoding`.
Only a fixed list of encodings is accepted, which excludes utf-7 and iso-2022 variants. A legacy
page with no declaration and no `--encoding` can decode incorrectly.

## Development

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run pip-audit
```

### Fixture tests

Defuddle's fixtures include copies of third-party pages, so this repository does not contain
or redistribute them. Point the tests at your own Defuddle checkout instead:

```bash
git clone https://github.com/kepano/defuddle
git -C defuddle checkout 3e8bde9
export DEFUDDLE_SOURCE_DIR="$PWD/defuddle"
uv run pytest tests/test_fixtures.py
uv run python scripts/fixture_report.py
```

Without `DEFUDDLE_SOURCE_DIR` these tests skip, as they do in CI. The checkout is only read.

The judged set is 53 fixtures chosen by name prefix. A fixture passes when `title`, `author`,
`site` and `published` match and the word-list similarity of the Markdown reaches 0.9.
The bar is 90% of the judged set. The report also lists all fixtures, for information only.

## License

MIT. See [LICENSE](LICENSE).
