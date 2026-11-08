"""Command-line entry point for anki-tools."""

import argparse
import os
import shutil
import signal
import sys
import threading
from collections.abc import Sequence
from importlib.metadata import version
from types import FrameType
from typing import Any, NoReturn, override

from anki.errors import AnkiException, Interrupted

from anki_tools.cmd import build, diff, dump, inspect
from anki_tools.cmd.manpage import InstallManPages
from anki_tools.lib.errors import AnkiToolsError

EXIT_ERROR = 1
EXIT_INTERRUPTED = 130
EXIT_BROKEN_PIPE = 141
EXIT_TERMINATED = 143

# Help text wraps to the terminal width, but never past this many columns.
_MAX_HELP_WIDTH = 80


class _HelpFormatter(argparse.HelpFormatter):
    """Help formatter that caps the wrap width instead of tracking it."""

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 24,
        width: int | None = None,
    ) -> None:
        columns = min(shutil.get_terminal_size().columns, _MAX_HELP_WIDTH)
        super().__init__(
            prog,
            indent_increment=indent_increment,
            max_help_position=max_help_position,
            width=max(columns - 2, 11),
        )


class _Parser(argparse.ArgumentParser):
    """Argument parser whose help wraps to at most ``_MAX_HELP_WIDTH`` columns."""

    @override
    def _get_formatter(self) -> argparse.HelpFormatter:
        return _HelpFormatter(prog=self.prog)


class _PrintVersion(argparse.Action):
    """Print version information and exit."""

    @override
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        print(_version_text())
        parser.exit()


def build_parser(*, with_version: bool = True) -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = _Parser(
        prog="anki-tools",
        description="Inspect and convert Anki deck packages.",
    )
    if with_version:
        parser.add_argument(
            "--version",
            action=_PrintVersion,
            nargs=0,
            help="show the anki-tools and anki versions and exit",
        )
    parser.add_argument(
        "--install-man-pages",
        action=InstallManPages,
        nargs=0,
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND", title="commands"
    )
    dump.register(subparsers)
    build.register(subparsers)
    diff.register(subparsers)
    inspect.register(subparsers)
    return parser


def install_signal_handlers() -> None:
    """Exit cleanly, running cleanup, when the process is terminated."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return an exit code."""
    install_signal_handlers()
    try:
        args = build_parser().parse_args(argv)
        code = int(args.func(args))
        sys.stdout.flush()
        return code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Interrupted:
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        _redirect_stdout_to_devnull()
        return EXIT_BROKEN_PIPE
    except AnkiToolsError as exc:
        print(f"anki-tools: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (AnkiException, OSError) as exc:
        print(f"anki-tools: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _terminate(_signum: int, _frame: FrameType | None) -> NoReturn:
    """Exit cleanly on SIGTERM."""
    raise SystemExit(EXIT_TERMINATED)


def _redirect_stdout_to_devnull() -> None:
    """Point stdout at /dev/null to silence a broken pipe."""
    try:
        fileno = sys.stdout.fileno()
    except (OSError, AttributeError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fileno)
    os.close(devnull)


def _version_text() -> str:
    """Return the two-line version banner."""
    return f"anki-tools {version('anki-tools')}\nanki {version('anki')}"


if __name__ == "__main__":
    raise SystemExit(main())
