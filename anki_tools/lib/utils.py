"""Shared helpers for the anki-tools library."""

from anki.collection import Collection
from anki.dbproxy import DBProxy

from anki_tools.lib.errors import AnkiToolsError


def _db(col: Collection) -> DBProxy:
    """Return the collection's database handle, or fail if it is closed."""
    if col.db is None:
        raise AnkiToolsError("collection is not open")
    return col.db


def _required[T](value: T | None, message: str) -> T:
    """Return *value*, or fail with *message* when it is missing.

    Anki's API types lookups as optional even where the caller has already
    established that the value exists. This states that invariant once, in a
    way that fails with a readable message instead of a stray attribute error.
    """
    if value is None:
        raise AnkiToolsError(message)
    return value
