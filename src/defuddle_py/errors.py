"""Exceptions raised by defuddle-py."""


class DefuddleError(Exception):
    """Base class for every error this package raises on purpose."""


class InputTooLargeError(DefuddleError):
    """The input exceeds the configured byte limit."""


class TreeTooDeepError(DefuddleError):
    """The input nests elements deeper than the configured limit."""


class ParseTimeoutError(DefuddleError):
    """Parsing took longer than the configured time limit."""


class UnsupportedEncodingError(DefuddleError):
    """The caller named an encoding this package does not decode."""
