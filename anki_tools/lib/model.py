"""Canonical, backend-independent deck dump model.

We keep a reduced view of Anki's internal models (or more specifically, of the
Python ``anki`` library) so a dump does not change when Anki does.

Anki has a lot of fields and moving internal parts so many things are not
trivial at all. docs/decisions.md goes into more detail and explains what counts
as content.
"""

from hashlib import sha256
from typing import Any, Self

import msgspec

DUMP_FORMAT = "anki-tools-dump"
DUMP_VERSION = 1

# NOTE: Here we have to hardcode subsets of keys used by Anki that we treat
# specially, namely dropping / excluding them.

# Keys Anki rebuilds on import or changes on every export. Dropping them keeps
# a dump canonical across ``dump -> build -> dump``.
IDENTITY_KEYS = frozenset(
    {
        "id",
        "mod",
        "usn",
        "ord",
        "did",
        "originalId",
        "req",
    }
)

# Deck scheduling and UI state: personal learning state, not content.
# "Scheduling information" can generally be exported too, but the feature is
# not supported by anki-tools and would otherwise carry substantial limitations.
DECK_STATE_KEYS = frozenset(
    {
        "dyn",
        "collapsed",
        "browserCollapsed",
        "desiredRetention",
        "newToday",
        "revToday",
        "lrnToday",
        "timeToday",
        "newLimit",
        "reviewLimit",
        "newLimitToday",
        "reviewLimitToday",
        "extendNew",
        "extendRev",
    }
)

# Preset keys that are bookkeeping, not settings. A preset keeps its scheduling
# keys, because those are content.
CONFIG_IDENTITY_KEYS = frozenset({"id", "name", "usn", "mod"})

# Everything else is kept, so a new Anki option is never lost.
IGNORED_KEYS = IDENTITY_KEYS | DECK_STATE_KEYS

# Anki's built-in deck and preset, which every fresh collection has.
DEFAULT_DECK = "Default"
DEFAULT_CONFIG = "Default"


class NotetypeField(msgspec.Struct, frozen=True):
    """A note field and the options Anki stores for it."""

    id: int
    name: str
    options: dict[str, Any] = msgspec.field(default_factory=dict)


class NotetypeTemplate(msgspec.Struct, frozen=True):
    """A single card template of a note type."""

    id: int
    name: str
    qfmt: str
    afmt: str
    options: dict[str, Any] = msgspec.field(default_factory=dict)


class Notetype(msgspec.Struct, frozen=True):
    """A note type with its fields and templates."""

    id: int
    name: str
    kind: int
    css: str
    sort_field: str
    fields: tuple[NotetypeField, ...]
    templates: tuple[NotetypeTemplate, ...]
    latex_pre: str
    latex_post: str
    options: dict[str, Any] = msgspec.field(default_factory=dict)


class DeckConfig(msgspec.Struct, frozen=True):
    """A deck preset (scheduling options)."""

    id: int
    name: str
    settings: dict[str, Any]


class Deck(msgspec.Struct, frozen=True):
    """A deck, the preset it uses, and the options Anki stores for it."""

    name: str
    config: str
    options: dict[str, Any] = msgspec.field(default_factory=dict)


class CardPlacement(msgspec.Struct, frozen=True):
    """The deck a card of a given template lives in."""

    ord: int
    deck: str


class Note(msgspec.Struct, frozen=True):
    """A note, its field values, and the cards it has."""

    guid: str
    notetype: str
    fields: dict[str, str]
    tags: tuple[str, ...]
    cards: tuple[CardPlacement, ...] = ()


class MediaRef(msgspec.Struct, frozen=True):
    """A media file's name, size and digest, without its bytes."""

    name: str
    size: int
    sha256: str


class MediaItem(MediaRef, frozen=True):
    """A media reference and, optionally, its bytes."""

    data: bytes | None = None

    @classmethod
    def from_bytes(cls, name: str, data: bytes) -> Self:
        """Build an item, computing its size and digest from *data*."""
        return cls(
            name=name, size=len(data), sha256=sha256(data).hexdigest(), data=data
        )

    def ref(self) -> MediaRef:
        """Return this item without its bytes."""
        return MediaRef(name=self.name, size=self.size, sha256=self.sha256)


class ExportOptions(msgspec.Struct, frozen=True):
    """The dump-shaping options a dump was produced with."""

    query: str = ""
    with_deck_configs: bool = False
    with_media: bool = True


class Dump(msgspec.Struct, frozen=True):
    """A complete deck dump."""

    notetypes: tuple[Notetype, ...]
    decks: tuple[Deck, ...]
    deck_configs: tuple[DeckConfig, ...]
    notes: tuple[Note, ...]
    media: tuple[MediaItem, ...]
    export_options: ExportOptions = ExportOptions()
