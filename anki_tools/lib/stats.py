"""A table summarising the changes between two dumps.

`diff --stat` prints it after the diff and `diff --summary` prints it alone.
The counts are by identity, not by diff line, so a modified entity counts once
instead of as an add plus a remove.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from anki_tools.lib import format as fmt
from anki_tools.lib.model import Dump

_COLUMN = len("changed")


@dataclass(frozen=True, slots=True)
class Summary:
    """Added, removed and changed counts per section, plus notice counts."""

    rows: tuple[tuple[str, int, int, int], ...]
    warnings: int
    errors: int


def summary(
    old: Dump,
    new: Dump,
    messages: Sequence[tuple[str, str]],
    *,
    exclude: frozenset[str] = frozenset(),
) -> Summary:
    """Return the change counts for the sections that are compared."""
    sections: tuple[
        tuple[str, str, Mapping[str, object], Mapping[str, object]], ...
    ] = (
        (
            "notetypes",
            fmt.NOTETYPES,
            {nt.name: nt for nt in old.notetypes},
            {nt.name: nt for nt in new.notetypes},
        ),
        (
            "decks",
            fmt.DECKS,
            {deck.name: deck for deck in old.decks},
            {deck.name: deck for deck in new.decks},
        ),
        (
            "presets",
            fmt.DECK_CONFIGS,
            {conf.name: conf for conf in old.deck_configs},
            {conf.name: conf for conf in new.deck_configs},
        ),
        (
            "notes",
            fmt.NOTES,
            {note.guid: note for note in old.notes},
            {note.guid: note for note in new.notes},
        ),
        (
            "media",
            fmt.MEDIA,
            {item.name: item for item in old.media},
            {item.name: item for item in new.media},
        ),
    )
    rows: list[tuple[str, int, int, int]] = []
    for name, section, before, after in sections:
        if section in exclude:
            continue
        rows.append((name, *_count(before, after)))
    warnings = sum(1 for level, _ in messages if level == "warning")
    errors = sum(1 for level, _ in messages if level == "error")
    return Summary(tuple(rows), warnings, errors)


def render(summary: Summary, *, markdown: bool) -> str:
    """Return *summary* as a borderless table or a markdown table."""
    return _markdown(summary) if markdown else _text(summary)


def _count(
    before: Mapping[str, object], after: Mapping[str, object]
) -> tuple[int, int, int]:
    added = len(after.keys() - before.keys())
    removed = len(before.keys() - after.keys())
    changed = sum(
        1 for key in before.keys() & after.keys() if before[key] != after[key]
    )
    return added, removed, changed


def _text(summary: Summary) -> str:
    width = max((len(name) for name, *_ in summary.rows), default=len("section"))
    lines = [
        f"{'':<{width}}  {'added':>{_COLUMN}}  {'removed':>{_COLUMN}}  "
        f"{'changed':>{_COLUMN}}"
    ]
    lines += [
        f"{name:<{width}}  {added:>{_COLUMN}}  {removed:>{_COLUMN}}  "
        f"{changed:>{_COLUMN}}"
        for name, added, removed, changed in summary.rows
    ]
    notices = (("warnings", summary.warnings), ("errors", summary.errors))
    notice_width = max(len(name) for name, _ in notices)
    lines += [""]
    lines += [f"{name:<{notice_width}}  {count}" for name, count in notices]
    return "\n".join(lines) + "\n"


def _markdown(summary: Summary) -> str:
    lines = [
        "| section | added | removed | changed |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines += [
        f"| {name} | {added} | {removed} | {changed} |"
        for name, added, removed, changed in summary.rows
    ]
    lines += [
        "",
        "| notices | count |",
        "| --- | ---: |",
        f"| warnings | {summary.warnings} |",
        f"| errors | {summary.errors} |",
    ]
    return "\n".join(lines) + "\n"
