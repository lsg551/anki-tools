"""Errors raised by anki-tools."""


class AnkiToolsError(Exception):
    """Base class for user-facing errors."""


class UnknownFormatError(AnkiToolsError):
    """Raised when a format name has no registered codec."""


class InvalidDumpError(AnkiToolsError):
    """Raised when a dump cannot be read or written."""
