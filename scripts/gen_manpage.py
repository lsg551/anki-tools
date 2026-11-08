"""Regenerate man/anki-tools.1 from the CLI's argparse tree.

The CLI reference is generated, so `--help` and the man page cannot drift.
Hand-written prose lives in man/anki-tools.include. The .TH line is excluded
from --check: it carries only generation metadata (the date), not content.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from anki_tools.cmd.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
OUTPUT = ROOT / "man" / "anki-tools.1"
INCLUDE = ROOT / "man" / "anki-tools.include"

# Fields argparse-manpage takes on the command line. The version is left out on
# purpose: it would land on the .TH line and be stale or a meaningless dev
# string between releases. The page ships inside the wheel, so it already
# documents the installed version, and `anki-tools --version` prints the live
# one.
_METADATA = (
    "--project-name",
    "anki-tools",
    "--description",
    "version control for Anki deck packages",
    "--author",
    "Luis Schulte",
    "--url",
    "https://github.com/lsg551/anki-tools",
    "--manual-title",
    "anki-tools",
)


def build_manpage_parser() -> argparse.ArgumentParser:
    """Return the parser rendered into the man page.

    It differs from the CLI parser in that it has no --version option and no
    inline description; the man page supplies that from the include file, where
    it can carry roff markup.
    """
    parser = build_parser(with_version=False)
    parser.description = None
    return parser


def generate() -> str:
    """Return the man page generated from the parser."""
    env = os.environ | {"COLUMNS": "80"}
    completed = subprocess.run(
        [
            "argparse-manpage",
            "--pyfile",
            str(SELF),
            "--function",
            "build_manpage_parser",
            *_METADATA,
            "--format",
            "single-commands-section",
            "--include",
            str(INCLUDE),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout


def _body(text: str) -> str:
    """Return *text* without its leading .TH line, which carries the date."""
    _, _, rest = text.partition("\n")
    return rest


def main() -> int:
    """Write the man page, or with --check fail when it is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed man page is stale instead of writing it",
    )
    args = parser.parse_args()

    generated = generate()
    if not args.check:
        OUTPUT.write_text(generated, encoding="utf-8")
        return 0
    committed = OUTPUT.read_text(encoding="utf-8")
    if _body(committed) == _body(generated):
        return 0
    print(
        f"{OUTPUT} is stale; run: uv run python scripts/gen_manpage.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
