"""Collect metadata and stats of an imported deck package for summary reports."""

from dataclasses import dataclass
from importlib.metadata import version as package_version

from anki.collection import Collection

from anki_tools.lib.convert import media_names
from anki_tools.lib.utils import _db


@dataclass(frozen=True, slots=True)
class Summary:
    """A read-only overview of a deck package."""

    anki_version: str
    package_format: str
    notes: int
    cards: int
    scheduled_cards: int
    revlog: int
    decks: tuple[str, ...]
    notetypes: tuple[str, ...]
    deck_configs: tuple[str, ...]
    media: tuple[str, ...]

    @property
    def has_personal_state(self) -> bool:
        """Whether the package carries learning progress."""
        return self.scheduled_cards > 0 or self.revlog > 0


def summarize(col: Collection, package_format: str) -> Summary:
    """Summarize an imported collection."""
    return Summary(
        anki_version=package_version("anki"),
        package_format=package_format,
        notes=_count(col, "notes"),
        cards=_count(col, "cards"),
        scheduled_cards=_count(
            col,
            "cards",
            where="type != 0 or queue != 0 or ivl != 0 or reps != 0",
        ),
        revlog=_count(col, "revlog"),
        decks=tuple(sorted(deck["name"] for deck in col.decks.all())),
        notetypes=_used_notetypes(col),
        deck_configs=tuple(sorted(conf["name"] for conf in col.decks.all_config())),
        media=tuple(media_names(col)),
    )


def _count(col: Collection, table: str, where: str = "") -> int:
    """Count rows in *table*, optionally filtered by *where*."""
    db = _db(col)
    clause = f" where {where}" if where else ""
    value = db.scalar(f"select count(*) from {table}{clause}")
    return int(value) if value is not None else 0


def _used_notetypes(col: Collection) -> tuple[str, ...]:
    """Return the note type names used by notes."""
    db = _db(col)
    used = set(db.list("select distinct mid from notes"))
    names = {str(nt["name"]) for nt in col.models.all() if nt["id"] in used}
    return tuple(sorted(names))
