"""Command line interface. It reads local input only and never touches the network."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO, Final

from defuddle_py import DefuddleError, InputTooLargeError, Limits, parse
from defuddle_py.limits import DEFAULT_MAX_INPUT_BYTES, DEFAULT_TIMEOUT_SECONDS

_STDIN_MARKER: Final = "-"
_EXIT_OK: Final = 0
_EXIT_FAILURE: Final = 1


def _read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise InputTooLargeError(f"input exceeds {max_bytes} bytes")
    return data


def _read_input(source: str, max_bytes: int) -> bytes:
    if source == _STDIN_MARKER:
        return _read_limited(sys.stdin.buffer, max_bytes)
    with Path(source).open("rb") as stream:
        return _read_limited(stream, max_bytes)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="defuddle-py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    parse_command = commands.add_parser("parse", help="extract the main content of an HTML file")
    parse_command.add_argument("source", help=f"HTML file, or {_STDIN_MARKER} for stdin")
    parse_command.add_argument("--md", action="store_true", help="print Markdown")
    parse_command.add_argument("--json", action="store_true", help="print JSON with metadata")
    parse_command.add_argument("--url", default="", help="page address for resolving links")
    parse_command.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_INPUT_BYTES,
        help="largest accepted input, in bytes",
    )
    parse_command.add_argument(
        "--encoding",
        help="encoding of the input, for example from a Content-Type header",
    )
    parse_command.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds before parsing stops; 0 disables the limit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface and return the exit code."""
    arguments = _build_parser().parse_args(argv)
    try:
        data = _read_input(arguments.source, arguments.max_bytes)
        limits = Limits(max_input_bytes=arguments.max_bytes, timeout_seconds=arguments.timeout)
        result = parse(data, url=arguments.url, encoding=arguments.encoding, limits=limits)
    except (DefuddleError, OSError) as err:
        print(f"defuddle-py: {err}", file=sys.stderr)
        return _EXIT_FAILURE
    if arguments.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    elif arguments.md:
        print(result.content_markdown)
    else:
        print(result.content)
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
