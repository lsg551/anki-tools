"""Shared command-line options."""

import argparse


def add_export_options(parser: argparse.ArgumentParser) -> None:
    """Add the dump-shaping export options to *parser*."""
    export = parser.add_argument_group("export options")
    export.add_argument(
        "-q",
        "--query",
        default="",
        help="Anki search selecting which notes to include",
    )
    export.add_argument(
        "--with-deck-configs", action="store_true", help="include deck presets"
    )
    export.add_argument("--no-media", action="store_true", help="exclude media files")
