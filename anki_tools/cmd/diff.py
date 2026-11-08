"""The ``diff`` subcommand."""

from __future__ import annotations

import argparse
import difflib
import os
import shlex
import sys
from pathlib import Path
from typing import NamedTuple

from anki_tools.cmd.options import add_export_options
from anki_tools.lib import apkg, convert
from anki_tools.lib import format as fmt
from anki_tools.lib.errors import AnkiToolsError
from anki_tools.lib.model import Dump, ExportOptions

_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"


def register[P: argparse.ArgumentParser](
    subparsers: argparse._SubParsersAction[P],
) -> None:
    """Register the ``diff`` subcommand."""
    parser = subparsers.add_parser(
        "diff",
        usage="%(prog)s [options] OLD NEW",
        help="compare two packages or dumps",
        description=(
            "Compare two packages or dumps and print a unified diff. Exit "
            "codes follow the diff utility: 0 when they match, 1 when they "
            "differ, 2 on an error. A dump records the export options it was "
            "made with; the two inputs must agree on them, or the diff would "
            "report scope and preset differences as content changes. The "
            "export options only affect how a package is read; -e/--exclude "
            "drops a section from the comparison itself and works for any "
            "input."
        ),
    )
    parser.add_argument("old", help='baseline package, dump or "-"')
    parser.add_argument("new", help='changed package, dump or "-"')
    parser.add_argument(
        "-f",
        "--format",
        choices=("text", "markdown"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "-o", "--output", default="-", help='output file ("-" for stdout)'
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        choices=fmt.SECTIONS,
        default=[],
        metavar="SECTION",
        help=f"section to drop from the diff (repeatable: {', '.join(fmt.SECTIONS)})",
    )
    add_export_options(parser)
    parser.add_argument(
        "--color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="colourise text output (default: auto)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Compare the two inputs and report the difference."""
    if args.old == "-" and args.new == "-":
        print(
            "anki-tools: only one of OLD and NEW can be read from stdin",
            file=sys.stderr,
        )
        return 2
    options = ExportOptions(
        query=args.query,
        with_deck_configs=args.with_deck_configs,
        with_media=not args.no_media,
    )
    exclude = frozenset(args.exclude)
    try:
        old = _load(args.old, options, exclude)
        new = _load(args.new, options, exclude)
    except (AnkiToolsError, OSError) as exc:
        print(f"anki-tools: {exc}", file=sys.stderr)
        return 2
    mismatch = _mismatch(old.dump.export_options, new.dump.export_options, exclude)
    if mismatch:
        return _report_mismatch(args, old, new, mismatch)
    diff = "".join(
        difflib.unified_diff(
            fmt.canonical_text(old.dump, exclude=exclude).splitlines(keepends=True),
            fmt.canonical_text(new.dump, exclude=exclude).splitlines(keepends=True),
            fromfile=args.old,
            tofile=args.new,
        )
    )
    if not diff:
        return 0
    if args.format == "markdown":
        text = f"```diff\n{diff}```\n"
    elif _use_color(args.color, args.output):
        text = _colorize(diff)
    else:
        text = diff
    _write(text, args.output)
    return 1


class _Loaded(NamedTuple):
    """A loaded input and whether it came from a binary package."""

    dump: Dump
    package: bool


def _load(source: str, options: ExportOptions, exclude: frozenset[str]) -> _Loaded:
    """Load a package or dump on either side of the comparison."""
    codec = fmt.dump_codec(source)
    if codec is not None:
        return _Loaded(
            fmt.read_dump(
                codec,
                source,
                with_media=fmt.MEDIA not in exclude,
                load_media=False,
            ),
            package=False,
        )
    with apkg.load(Path(source), with_scheduling=False) as col:
        return _Loaded(
            convert.to_dump(
                col,
                query=options.query,
                with_deck_configs=options.with_deck_configs,
                with_media=options.with_media and fmt.MEDIA not in exclude,
                load_media=False,
            ),
            package=True,
        )


def _mismatch(
    old: ExportOptions, new: ExportOptions, exclude: frozenset[str]
) -> list[str]:
    """Return the export options that differ and are still being compared."""
    names = []
    if old.query != new.query:
        names.append("query")
    if (
        old.with_deck_configs != new.with_deck_configs
        and fmt.DECK_CONFIGS not in exclude
    ):
        names.append("deck configs")
    if old.with_media != new.with_media and fmt.MEDIA not in exclude:
        names.append("media")
    return names


def _report_mismatch(
    args: argparse.Namespace, old: _Loaded, new: _Loaded, mismatch: list[str]
) -> int:
    """Report export options that differ between the two inputs."""
    print("anki-tools: export options differ between inputs", file=sys.stderr)
    print(f"  {args.old}: {_describe(old.dump.export_options)}", file=sys.stderr)
    print(f"  {args.new}: {_describe(new.dump.export_options)}", file=sys.stderr)
    print(f"  differing: {', '.join(mismatch)}", file=sys.stderr)
    if old.package == new.package:
        print("re-dump one side with matching export options.", file=sys.stderr)
    else:
        options = new.dump.export_options if old.package else old.dump.export_options
        command = " ".join(
            shlex.quote(part)
            for part in (
                "anki-tools",
                "diff",
                args.old,
                args.new,
                *_export_flags(options),
            )
        )
        print(f"re-run with: {command}", file=sys.stderr)
    return 2


def _export_flags(options: ExportOptions) -> list[str]:
    """Return the CLI flags that reproduce *options*."""
    flags: list[str] = []
    if options.with_deck_configs:
        flags.append("--with-deck-configs")
    if not options.with_media:
        flags.append("--no-media")
    if options.query:
        flags += ["-q", options.query]
    return flags


def _describe(options: ExportOptions) -> str:
    """Render *options* as a short flag string."""
    flags = _export_flags(options)
    return " ".join(shlex.quote(flag) for flag in flags) if flags else "(defaults)"


def _write(text: str, output: str) -> None:
    """Write *text* to *output* (a path or "-" for stdout)."""
    if output == "-":
        sys.stdout.write(text)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _use_color(choice: bool | None, output: str) -> bool:
    """Resolve the --color flag against the target, NO_COLOR, and the terminal."""
    if choice is not None:
        return choice
    if output != "-":
        return False
    if os.environ.get("NO_COLOR", "") != "":
        return False
    return bool(sys.stdout.isatty())


def _colorize(diff: str) -> str:
    """Wrap unified-diff lines in ANSI colours."""
    lines = []
    for index, line in enumerate(diff.splitlines()):
        color = _color(line, header=index < 2)
        lines.append(f"{color}{line}{_RESET}" if color else line)
    return "\n".join(lines) + "\n"


def _color(line: str, *, header: bool = False) -> str:
    """Return the ANSI colour for a unified-diff line, if any."""
    if header and line.startswith(("+++", "---")):
        return _BOLD
    if line.startswith("@@"):
        return _CYAN
    if line.startswith("+"):
        return _GREEN
    if line.startswith("-"):
        return _RED
    return ""
