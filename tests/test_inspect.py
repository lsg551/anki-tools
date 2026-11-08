"""Tests for the inspect subcommand and its summary."""

import json
from pathlib import Path

import pytest

from anki_tools.cmd.cli import main
from anki_tools.lib import apkg, summary


def test_summarize(apkg_path: Path) -> None:
    with apkg.load(apkg_path) as col:
        data = summary.summarize(col, apkg.format_name(apkg_path))

    assert data.notes == 2
    assert data.cards == 2
    assert data.scheduled_cards == 0
    assert data.revlog == 0
    assert data.has_personal_state is False
    assert data.package_format == "latest"
    assert "Test Deck" in data.decks
    assert "Fixture" in data.notetypes
    assert data.media == ("hello.png",)
    assert data.anki_version


def test_inspect_json(apkg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", str(apkg_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["notes"] == 2
    assert payload["has_personal_state"] is False
    assert payload["media"] == ["hello.png"]


def test_inspect_reports_personal_state(
    scheduled_apkg_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(scheduled_apkg_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scheduled_cards"] == 1
    assert payload["revlog"] == 1
    assert payload["has_personal_state"] is True


def test_inspect_text(apkg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", str(apkg_path)]) == 0
    out = capsys.readouterr().out
    assert "notes:" in out
    assert "personal state:  no" in out


def test_inspect_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(tmp_path / "nope.apkg")]) == 1
    assert "no such file" in capsys.readouterr().err
