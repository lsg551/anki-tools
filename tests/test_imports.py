"""Import-order regression tests."""

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run *code* in a fresh interpreter and return the result."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )


def test_convert_imports_in_a_fresh_interpreter() -> None:
    # anki.cards/decks/models/notes raise a circular import if one of them is the
    # first anki module a process imports, so convert.py imports anki.collection first.
    result = _run("from anki_tools.lib import convert")
    assert result.returncode == 0, result.stderr


def test_cli_imports_in_a_fresh_interpreter() -> None:
    result = _run("from anki_tools.cmd.cli import main")
    assert result.returncode == 0, result.stderr
