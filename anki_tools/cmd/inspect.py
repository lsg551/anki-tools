"""The ``inspect`` subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anki_tools.lib import apkg, summary


def register[P: argparse.ArgumentParser](
    subparsers: argparse._SubParsersAction[P],
) -> None:
    """Register the ``inspect`` subcommand."""
    parser = subparsers.add_parser(
        "inspect",
        usage="%(prog)s [options] PACKAGE",
        help="summarize a deck package",
        description="Summarize a deck package.",
    )
    parser.add_argument("package", type=Path, help="path to an .apkg file")
    parser.add_argument(
        "-f",
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Summarize the package given on the command line."""
    with apkg.load(args.package) as col:
        data = summary.summarize(col, apkg.format_name(args.package))

    if args.format == "json":
        print(json.dumps(_as_json(data), indent=2, sort_keys=True))
    else:
        print(_render_text(data))
    return 0


def _as_json(data: summary.Summary) -> dict[str, object]:
    """Return a JSON-serialisable view of *data*."""
    return {
        "anki_version": data.anki_version,
        "package_format": data.package_format,
        "notes": data.notes,
        "cards": data.cards,
        "scheduled_cards": data.scheduled_cards,
        "revlog": data.revlog,
        "decks": list(data.decks),
        "notetypes": list(data.notetypes),
        "deck_configs": list(data.deck_configs),
        "media": list(data.media),
        "has_personal_state": data.has_personal_state,
    }


def _render_text(data: summary.Summary) -> str:
    """Render *data* as human-readable text."""
    personal = "yes" if data.has_personal_state else "no"
    configs = f"{len(data.deck_configs)} ({', '.join(data.deck_configs)})"
    return "\n".join(
        [
            f"anki:            {data.anki_version}",
            f"format:          {data.package_format}",
            f"notes:           {data.notes}",
            f"cards:           {data.cards} (scheduled: {data.scheduled_cards})",
            f"revlog:          {data.revlog}",
            f"decks:           {len(data.decks)} ({', '.join(data.decks)})",
            f"notetypes:       {len(data.notetypes)} ({', '.join(data.notetypes)})",
            f"deck configs:    {configs}",
            f"media:           {len(data.media)}",
            f"personal state:  {personal}",
        ]
    )
