"""Tests for signal and broken-pipe handling."""

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from anki_tools.cmd import cli

_CHILD_LOAD = """
import sys, time
from pathlib import Path
from anki_tools.cmd import cli
from anki_tools.lib import apkg

cli.install_signal_handlers()
with apkg.load(Path(sys.argv[1])):
    print("ready", flush=True)
    time.sleep(30)
"""


def _main_child(exception: str) -> str:
    return (
        "import sys\n"
        "from anki_tools.cmd import cli, inspect\n"
        "def boom(args):\n"
        f"    raise {exception}\n"
        "inspect.run = boom\n"
        'sys.exit(cli.main(["inspect", "deck.apkg"]))\n'
    )


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )


def test_keyboard_interrupt_returns_130() -> None:
    assert _run(_main_child("KeyboardInterrupt")).returncode == cli.EXIT_INTERRUPTED


def test_broken_pipe_returns_141() -> None:
    assert _run(_main_child("BrokenPipeError")).returncode == cli.EXIT_BROKEN_PIPE


@pytest.mark.parametrize(
    ("sig", "expected"),
    [(signal.SIGINT, None), (signal.SIGTERM, cli.EXIT_TERMINATED)],
)
def test_signal_cleans_up_temp_collection(
    apkg_path: Path, sig: signal.Signals, expected: int | None
) -> None:
    tmp = tempfile.mkdtemp(prefix="anki-tools-sig-")
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_LOAD, str(apkg_path)],
        env=os.environ | {"TMPDIR": tmp},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    proc.send_signal(sig)
    _, stderr = proc.communicate(timeout=15)

    leftovers = [name for _, _, files in os.walk(tmp) for name in files]
    assert leftovers == [], f"leftover temp files: {leftovers}"
    if expected is not None:
        assert proc.returncode == expected, stderr
