"""A wall-clock limit for one parse.

The work is pure Python and never waits on anything, so a timer signal can interrupt it.
A worker process would stop C code too, but it would pull in process management that
this package otherwise avoids. Every regex runs on bounded text, so no single C call
holds the signal back for long.
"""

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from defuddle_py.errors import ParseTimeoutError

logger = logging.getLogger(__name__)


def _can_use_timer() -> bool:
    return hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()


@contextmanager
def deadline(seconds: float | None) -> Iterator[None]:
    """Raise ``ParseTimeoutError`` in the body after ``seconds``.

    Signals only reach the main thread and do not exist on Windows. Elsewhere the limit
    cannot be enforced, so a warning is logged and the body runs without one. ``None``
    or a non-positive value disables the limit.
    """
    if seconds is None or seconds <= 0:
        yield
        return
    if not _can_use_timer():
        logger.warning("timeout of %s seconds is not enforced in this context", seconds)
        yield
        return

    def _expire(_signum: int, _frame: FrameType | None) -> None:
        raise ParseTimeoutError(f"parsing exceeded {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, _expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
