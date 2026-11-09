"""Tests for the man page installer."""

import sysconfig
from pathlib import Path

import pytest

from anki_tools.cmd import manpage
from anki_tools.cmd.cli import build_parser, main
from anki_tools.cmd.manpage import MANPAGE_NAME, install_man_page
from anki_tools.lib.errors import AnkiToolsError


def _page(tmp_path: Path, text: str = ".TH ANKI-TOOLS\n") -> Path:
    """Create a man page in a fake install prefix."""
    page = tmp_path / "prefix" / "share" / "man" / "man1" / MANPAGE_NAME
    page.parent.mkdir(parents=True)
    page.write_text(text, encoding="utf-8")
    return page


def test_install_copies_into_manpath(tmp_path: Path) -> None:
    source = _page(tmp_path)
    dest = tmp_path / "dest"
    result = install_man_page(source=source, dest_dir=dest)
    assert result.status == "installed"
    assert result.path == dest / MANPAGE_NAME
    assert result.path.read_text() == source.read_text()


def test_install_is_idempotent(tmp_path: Path) -> None:
    source = _page(tmp_path)
    dest = tmp_path / "dest"
    install_man_page(source=source, dest_dir=dest)
    again = install_man_page(source=source, dest_dir=dest)
    assert again.status == "already up to date"


def test_install_updates_a_stale_page(tmp_path: Path) -> None:
    source = _page(tmp_path, "new\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / MANPAGE_NAME).write_text("old\n", encoding="utf-8")
    result = install_man_page(source=source, dest_dir=dest)
    assert result.status == "updated"
    assert (dest / MANPAGE_NAME).read_text() == "new\n"


def test_install_without_a_page_raises(tmp_path: Path) -> None:
    with pytest.raises(AnkiToolsError):
        install_man_page(source=tmp_path / "nope", dest_dir=tmp_path / "dest")


def test_install_without_a_source_but_installed(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / MANPAGE_NAME).write_text("page\n", encoding="utf-8")
    result = install_man_page(source=tmp_path / "nope", dest_dir=dest)
    assert result.status == "already on the manpath"


def test_bundled_path_is_under_the_prefix() -> None:
    page = manpage.bundled_path()
    assert page.name == MANPAGE_NAME
    assert page.is_absolute()


def test_bundled_path_finds_the_user_scheme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _page(tmp_path)
    system = tmp_path / "system"
    user = tmp_path / "prefix"

    def fake_get_path(key: str, scheme: str | None = None) -> str:
        del key
        return str(user if scheme == "posix_user" else system)

    monkeypatch.setattr(sysconfig, "get_path", fake_get_path)
    assert manpage.bundled_path() == page


def test_install_when_source_is_the_target(tmp_path: Path) -> None:
    source = _page(tmp_path)
    result = install_man_page(source=source, dest_dir=source.parent)
    assert result.status == "already on the manpath"


def test_user_man1_dir_honours_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert manpage.user_man1_dir() == tmp_path / "man" / "man1"


def test_install_flag_is_hidden() -> None:
    assert "--install-man-pages" not in build_parser().format_help()


def test_install_flag_prints_and_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_install_man_page() -> manpage.InstallResult:
        return manpage.InstallResult(tmp_path / MANPAGE_NAME, "installed")

    monkeypatch.setattr(manpage, "install_man_page", fake_install_man_page)
    with pytest.raises(SystemExit) as excinfo:
        main(["--install-man-pages"])
    assert excinfo.value.code == 0
    assert "man anki-tools" in capsys.readouterr().out
