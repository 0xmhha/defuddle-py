"""Resource limits applied to untrusted input."""

from dataclasses import dataclass
from typing import Final

DEFAULT_MAX_INPUT_BYTES: Final = 10 * 1024 * 1024
# The deepest page in the Defuddle fixture set nests 54 levels. Processing time grows
# with depth, so the limit stays well below what parsers tolerate.
DEFAULT_MAX_TREE_DEPTH: Final = 256
DEFAULT_MAX_JSON_LD_BYTES: Final = 1024 * 1024
DEFAULT_MAX_JSON_LD_DEPTH: Final = 64
# Regexes only ever run on text up to this length. Metadata strings and short
# labels are far shorter, and the bound keeps backtracking from growing with input.
MAX_PATTERN_TEXT_CHARS: Final = 4096
# A synthetic 10 MiB page took about 80 seconds on a laptop and the slowest Defuddle
# fixture about 1 second, so this allows slower machines the largest accepted input.
DEFAULT_TIMEOUT_SECONDS: Final = 180.0


@dataclass(frozen=True, slots=True)
class Limits:
    """Upper bounds checked while parsing a document."""

    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    max_tree_depth: int = DEFAULT_MAX_TREE_DEPTH
    max_json_ld_bytes: int = DEFAULT_MAX_JSON_LD_BYTES
    max_json_ld_depth: int = DEFAULT_MAX_JSON_LD_DEPTH
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS
