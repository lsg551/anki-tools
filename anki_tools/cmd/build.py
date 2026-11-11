"""The ``build`` subcommand reads a dump and writes an ``.apkg``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from anki_tools.lib import apkg, convert
from anki_tools.lib import format as fmt
from anki_tools.lib.errors import AnkiToolsError
from anki_tools.lib.model import Dump


def register[P: argparse.ArgumentParser](
    subparsers: argparse._SubParsersAction[P],
) -> None:
    """Register the ``build`` subcommand."""
    parser = subparsers.add_parser(
        "build",
        usage="%(prog)s [options] SOURCE",
        help="build an .apkg from a dump",
        description="Build a deck package from a dump (directory or .zip).",
    )
    parser.add_argument("source", help='dump directory or file ("-" for stdin)')
    parser.add_argument(
        "-o", "--output", default="-", help='output .apkg path ("-" for stdout)'
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=fmt.suffixes(),
        help="input format (default: inferred from the source path)",
    )
    parser.add_argument(
        "--update-deck-presets",
        choices=convert.PRESET_MODES,
        default="never",
        help=(
            "how the package picks deck preset ids: never keeps the dump's id "
            "(default), always mints a new one, on-change follows the content"
        ),
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Build a package from the dump given on the command line."""
    if args.source != "-" and not Path(args.source).exists():
        raise AnkiToolsError(f"no such file: {args.source}")
    codec = fmt.resolve(args.format, args.source)
    # A dump from stdin has no media source to stream from, so it is read whole.
    from_stdin = args.source == "-"
    dump = fmt.read_dump(codec, args.source, load_media=from_stdin)
    orphans = convert.orphan_note_count(dump)
    if orphans:
        print(
            f"anki-tools: warning: skipping {orphans} note(s) with no cards",
            file=sys.stderr,
        )
    media = None if from_stdin else codec.media_source(Path(args.source))
    if args.output == "-":
        with fmt.stream_to_stdout() as path:
            _build(dump, path, media, args.update_deck_presets)
        return 0
    with fmt.staged(Path(args.output)) as path:
        _build(dump, path, media, args.update_deck_presets)
    return 0


def _build(
    dump: Dump,
    path: Path,
    media: fmt.MediaSource | None = None,
    update_deck_presets: str = "never",
) -> None:
    """Build *dump* into an .apkg at *path*, reading media from *media*."""
    # The media tuple is the source of truth; with_media is only metadata, and
    # format._check_export_options has already refused a dump that disagrees.
    with convert.from_dump(dump, media, update_deck_presets=update_deck_presets) as col:
        apkg.export(
            col,
            path,
            with_scheduling=False,
            with_deck_configs=dump.export_options.with_deck_configs,
            with_media=True,
        )
