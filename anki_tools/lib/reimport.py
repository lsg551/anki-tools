"""Changes a re-import cannot fully apply.

Anki's package import can only add presets, merges note types by inserting
missing fields and templates, and leaves existing cards in their decks.
`docs/anki.md` records the behaviour; each message links the tracking issue.

The caller prints the messages to stderr, separately from the diff, and leaves
the exit code alone: a "warning" needs the right import options, an "error"
cannot be applied at all.
"""

from anki_tools.lib import format as fmt
from anki_tools.lib.model import Dump, Note, NotetypeField, NotetypeTemplate

REMOVED = "https://github.com/lsg551/anki-tools/issues/9"
REORDERED = "https://github.com/lsg551/anki-tools/issues/10"
MOVED_CARD = "https://github.com/lsg551/anki-tools/issues/7"
PRESET = "https://github.com/lsg551/anki-tools/issues/8"
NOTETYPE_ID = "https://github.com/lsg551/anki-tools/issues/5"


def messages(
    old: Dump, new: Dump, *, exclude: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """Return a ``(level, text)`` pair per change a re-import cannot apply.

    *exclude* names the sections the caller dropped, so a message never mentions
    a section that is not being compared.
    """
    out: list[tuple[str, str]] = []
    if fmt.NOTETYPES not in exclude:
        out += _notetypes(old, new)
    if fmt.NOTES not in exclude:
        out += _cards(old, new)
    if fmt.DECK_CONFIGS not in exclude:
        out += _presets(old, new)
    return out


def _notetypes(old: Dump, new: Dump) -> list[tuple[str, str]]:
    """Compare fields and templates of note types present on both sides."""
    before = {nt.name: nt for nt in old.notetypes}
    out: list[tuple[str, str]] = []
    for nt in new.notetypes:
        prev = before.get(nt.name)
        if prev is None:
            continue
        if prev.id != nt.id:
            out.append(
                (
                    "error",
                    f"note type '{nt.name}' has a different id; a re-import "
                    f"adds a duplicate note type instead of updating it "
                    f"({NOTETYPE_ID})",
                )
            )
        out += _members(nt.name, "field", prev.fields, nt.fields)
        out += _members(nt.name, "template", prev.templates, nt.templates)
    return out


def _members(
    notetype: str,
    kind: str,
    old: tuple[NotetypeField, ...] | tuple[NotetypeTemplate, ...],
    new: tuple[NotetypeField, ...] | tuple[NotetypeTemplate, ...],
) -> list[tuple[str, str]]:
    """Compare one member list by id, then by name for the leftovers.

    A rename keeps its id, so it is not a removal. A note type built by another
    tool or release can carry different ids for the same names, so the members
    without a shared id are paired by name instead of read as removed and added.
    """
    shared = {member.id for member in old} & {member.id for member in new}
    old_left = [member for member in old if member.id not in shared]
    new_left = [member for member in new if member.id not in shared]
    old_names = {member.name for member in old_left}
    new_names = {member.name for member in new_left}
    out: list[tuple[str, str]] = []
    removed = [member.name for member in old_left if member.name not in new_names]
    added = [member.name for member in new_left if member.name not in old_names]
    if removed:
        out.append(
            (
                "error",
                f"note type '{notetype}' lost {kind} {_list(removed)}; "
                f"a re-import cannot remove a {kind} ({REMOVED})",
            )
        )
    if added:
        out.append(
            (
                "warning",
                f"note type '{notetype}' gained {kind} {_list(added)}; "
                'import with "merge note types" and "always update" to apply it',
            )
        )
    old_order = [_order_key(member, shared) for member in old]
    new_order = [_order_key(member, shared) for member in new]
    if not removed and not added and old_order != new_order:
        out.append(
            (
                "error",
                f"note type '{notetype}' reordered its {kind}s; "
                f"a re-import cannot reorder {kind}s ({REORDERED})",
            )
        )
    return out


def _order_key(member: NotetypeField | NotetypeTemplate, shared: set[int]) -> int | str:
    """Return the id a member shares with the other side, else its name."""
    return member.id if member.id in shared else member.name


def _cards(old: Dump, new: Dump) -> list[tuple[str, str]]:
    """Report a card whose deck changed between the two dumps."""
    before = {note.guid: note for note in old.notes}
    out: list[tuple[str, str]] = []
    for note in new.notes:
        prev = before.get(note.guid)
        if prev is None:
            continue
        decks = {card.ord: card.deck for card in prev.cards}
        for card in note.cards:
            if card.ord in decks and decks[card.ord] != card.deck:
                out.append(
                    (
                        "error",
                        f"card '{_label(note)}' moved from '{decks[card.ord]}' "
                        f"to '{card.deck}'; a re-import cannot move a card "
                        f"({MOVED_CARD})",
                    )
                )
    return out


def _presets(old: Dump, new: Dump) -> list[tuple[str, str]]:
    """Report a preset whose settings changed between the two dumps."""
    before = {conf.name: conf.settings for conf in old.deck_configs}
    out: list[tuple[str, str]] = []
    for conf in new.deck_configs:
        prev = before.get(conf.name)
        if prev is not None and prev != conf.settings:
            out.append(
                (
                    "error",
                    f"deck preset '{conf.name}' settings changed; a re-import "
                    f"can only add a preset, not update it ({PRESET})",
                )
            )
    return out


def _list(names: list[str]) -> str:
    """Render names as ``'A', 'B'``."""
    return ", ".join(f"'{name}'" for name in names)


def _label(note: Note) -> str:
    """Return a short label for *note*, or its guid."""
    for value in note.fields.values():
        text = value.strip()
        if text:
            return text[:40]
    return note.guid
