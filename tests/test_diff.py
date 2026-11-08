"""Tests for the diff subcommand."""

from collections.abc import Callable
from pathlib import Path

import pytest
from msgspec.structs import replace

from anki_tools.cmd.cli import main
from anki_tools.cmd.diff import _BOLD, _GREEN, _RED, _colorize, _use_color
from anki_tools.lib import apkg, convert
from anki_tools.lib import format as fmt
from anki_tools.lib.model import Dump


def _dump(
    apkg_path: Path, *, query: str = "", configs: bool = True, media: bool = True
) -> Dump:
    """Build a dump for *apkg_path*."""
    with apkg.load(apkg_path) as col:
        return convert.to_dump(
            col, query=query, with_deck_configs=configs, with_media=media
        )


def _write(dump: Dump, target: Path) -> Path:
    """Write *dump* as a JSON Lines directory and return the path."""
    fmt.codec_for("jsonl").write(dump, target)
    return target


def test_identical_is_silent(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _write(_dump(apkg_path), tmp_path / "d")
    assert main(["diff", str(dump), str(dump)]) == 0
    assert capsys.readouterr().out == ""


def test_package_matches_its_dump(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _write(_dump(apkg_path), tmp_path / "d")
    assert main(["diff", str(apkg_path), str(dump), "--with-deck-configs"]) == 0
    assert capsys.readouterr().out == ""


def test_change_exits_one(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    full = _write(dump, tmp_path / "full")
    changed = _write(replace(dump, notes=dump.notes[:1]), tmp_path / "changed")
    assert main(["diff", str(full), str(changed)]) == 1
    assert "-@note" in capsys.readouterr().out


def test_markdown_is_fenced(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    full = _write(dump, tmp_path / "full")
    changed = _write(replace(dump, notes=dump.notes[:1]), tmp_path / "changed")
    assert main(["diff", str(full), str(changed), "-f", "markdown"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("```diff\n")
    assert out.endswith("```\n")


def test_exclude_media(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    with_media = _write(dump, tmp_path / "a")
    without_media = _write(replace(dump, media=()), tmp_path / "b")
    assert main(["diff", str(with_media), str(without_media)]) == 1
    capsys.readouterr()
    assert main(["diff", str(with_media), str(without_media), "-e", "media"]) == 0
    assert capsys.readouterr().out == ""


def test_no_color_has_no_escapes(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    full = _write(dump, tmp_path / "full")
    changed = _write(replace(dump, notes=dump.notes[:1]), tmp_path / "changed")
    assert main(["diff", str(full), str(changed), "--no-color"]) == 1
    assert "\033" not in capsys.readouterr().out


def test_color_not_written_to_file(
    apkg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = _dump(apkg_path)
    full = _write(dump, tmp_path / "full")
    changed = _write(replace(dump, notes=dump.notes[:1]), tmp_path / "changed")
    out = tmp_path / "changes.diff"
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert main(["diff", str(full), str(changed), "-o", str(out)]) == 1
    assert "\033" not in out.read_text()


def test_export_options_mismatch_is_rejected(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _write(_dump(apkg_path), tmp_path / "d")
    assert main(["diff", str(apkg_path), str(dump)]) == 2
    assert "--with-deck-configs" in capsys.readouterr().err


def test_media_export_option_mismatch_is_rejected(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _write(_dump(apkg_path, configs=False, media=False), tmp_path / "d")
    assert main(["diff", str(apkg_path), str(dump)]) == 2
    assert "--no-media" in capsys.readouterr().err
    assert main(["diff", str(apkg_path), str(dump), "--exclude", "media"]) == 0


def test_query_mismatch_is_rejected(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = tmp_path / "d"
    assert main(["dump", str(apkg_path), "-o", str(dump), "-q", "tag:tag"]) == 0
    assert main(["diff", str(apkg_path), str(dump)]) == 2
    err = capsys.readouterr().err
    assert "query" in err
    assert "-q tag:tag" in err


def test_exclude_deck_configs_ignores_presets(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_presets = _write(_dump(apkg_path, configs=True), tmp_path / "a")
    without_presets = _write(_dump(apkg_path, configs=False), tmp_path / "b")
    assert main(["diff", str(with_presets), str(without_presets)]) == 2
    capsys.readouterr()
    assert (
        main(["diff", str(with_presets), str(without_presets), "-e", "deck_configs"])
        == 0
    )
    assert capsys.readouterr().out == ""


def test_both_stdin_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "-", "-"]) == 2
    assert "stdin" in capsys.readouterr().err


def test_diff_reads_a_dump_from_stdin(
    apkg_path: Path,
    tmp_path: Path,
    feed_stdin: Callable[[bytes], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = tmp_path / "dump.zip"
    assert main(["dump", str(apkg_path), "-o", str(bundle), "--with-deck-configs"]) == 0
    feed_stdin(bundle.read_bytes())
    assert main(["diff", "-", str(apkg_path), "--with-deck-configs"]) == 0
    assert capsys.readouterr().out == ""


def test_missing_package_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.apkg"
    assert main(["diff", str(missing), str(missing)]) == 2
    assert "no such file" in capsys.readouterr().err


def test_output_creates_parent_directories(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    full = _write(dump, tmp_path / "full")
    changed = _write(replace(dump, notes=dump.notes[:1]), tmp_path / "changed")
    out = tmp_path / "nested" / "dir" / "changes.diff"
    assert main(["diff", str(full), str(changed), "-o", str(out)]) == 1
    assert "-@note" in out.read_text()


def test_use_color_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _use_color(None, "-") is True
    assert _use_color(True, "-") is True
    assert _use_color(False, "-") is False
    assert _use_color(None, "out.diff") is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert _use_color(None, "-") is False
    assert _use_color(True, "-") is True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert _use_color(None, "-") is False


def test_colorize_does_not_bold_content_lines() -> None:
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-+++ old\n++++ new\n"
    lines = _colorize(diff).splitlines()
    assert lines[0].startswith(_BOLD)
    assert lines[1].startswith(_BOLD)
    assert lines[3].startswith(_RED)
    assert lines[4].startswith(_GREEN)
