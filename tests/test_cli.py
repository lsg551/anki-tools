"""Tests for the top-level CLI."""

from pathlib import Path

import pytest

from anki_tools.cmd.cli import build_parser, main


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "anki-tools" in out
    assert "anki " in out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_unknown_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["nope"])
    assert excinfo.value.code == 2


def test_subcommand_metavar() -> None:
    help_text = build_parser().format_help()
    assert "COMMAND" in help_text
    assert "{dump" not in help_text


def test_invalid_package_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.apkg"
    bad.write_text("not a package")
    assert main(["inspect", str(bad)]) == 1
    assert "anki-tools:" in capsys.readouterr().err


def test_directory_is_not_a_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(tmp_path)]) == 1
    assert "directory" in capsys.readouterr().err


def test_build_on_missing_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope"
    out = tmp_path / "out.apkg"
    assert main(["build", str(missing), "-o", str(out)]) == 1
    assert "no such file" in capsys.readouterr().err
