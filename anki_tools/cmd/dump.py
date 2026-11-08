"""The ``dump`` subcommand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from anki_tools.cmd.options import add_export_options
from anki_tools.lib import apkg, convert
from anki_tools.lib import format as fmt


def register[P: argparse.ArgumentParser](
    subparsers: argparse._SubParsersAction[P],
) -> None:
    """Register the ``dump`` subcommand."""
    parser = subparsers.add_parser(
        "dump",
        usage="%(prog)s [options] PACKAGE",
        help="extract a deck package into a dump",
        description="Extract a deck package into a dump (directory or .zip).",
    )
    parser.add_argument("package", type=Path, help="path to an .apkg file")
    parser.add_argument(
        "-o",
        "--output",
        default="-",
        help='output directory or file ("-" for stdout)',
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=fmt.suffixes(),
        help="output format (default: inferred from the output path)",
    )
    add_export_options(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Extract the package given on the command line."""
    codec = fmt.resolve(args.format, args.output)
    with apkg.load(args.package, with_scheduling=False) as col:
        dump = convert.to_dump(
            col,
            query=args.query,
            with_deck_configs=args.with_deck_configs,
            with_media=not args.no_media,
            load_media=False,
        )
        orphans = convert.orphan_note_count(dump)
        if orphans:
            print(
                f"anki-tools: warning: {orphans} note(s) have no cards and will be "
                "skipped when building",
                file=sys.stderr,
            )
        fmt.write_dump(
            codec, dump, args.output, fmt.DirMediaSource(Path(col.media.dir()))
        )
    return 0
