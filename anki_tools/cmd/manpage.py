"""Install the generated man page into the user's manpath."""

import argparse
import os
import shutil
import sysconfig
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple, override

from anki_tools.lib.errors import AnkiToolsError

MANPAGE_NAME = "anki-tools.1"
_PREFIX_PATH = Path("share") / "man" / "man1" / MANPAGE_NAME


class InstallResult(NamedTuple):
    """Where the man page ended up and what happened to it."""

    path: Path
    status: str


def install_man_page(
    source: Path | None = None, dest_dir: Path | None = None
) -> InstallResult:
    """Copy the man page into the user's manpath.

    The page comes from this install's prefix, so it matches the installed
    version and needs no network. Re-run after an upgrade to refresh it.

    Args:
        source: The bundled man page; defaults to this install's copy.
        dest_dir: The target directory; defaults to the user's man1.

    Returns:
        The destination path and a status word.

    Raises:
        AnkiToolsError: If there is no man page to copy and none is installed.
    """
    source = source or bundled_path()
    target = (dest_dir or user_man1_dir()) / MANPAGE_NAME
    if source.resolve() == target.resolve():
        return InstallResult(target, "already on the manpath")
    if not source.is_file():
        if target.is_file():
            return InstallResult(target, "already on the manpath")
        raise AnkiToolsError(
            f"no man page at {source}; reinstall anki-tools or use a wheel "
            "that ships it"
        )
    if target.is_file():
        if target.read_bytes() == source.read_bytes():
            return InstallResult(target, "already up to date")
        status = "updated"
    else:
        status = "installed"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return InstallResult(target, status)


def bundled_path() -> Path:
    """Return this install's bundled man page path, or where it would be."""
    for scheme in ("posix_prefix", "posix_user"):
        path = Path(sysconfig.get_path("data", scheme=scheme)) / _PREFIX_PATH
        if path.is_file():
            return path
    return Path(sysconfig.get_path("data")) / _PREFIX_PATH


def user_man1_dir() -> Path:
    """Return the per-user man1 directory, honouring XDG_DATA_HOME."""
    root = os.environ.get("XDG_DATA_HOME", "")
    base = Path(root) if root != "" else Path.home() / ".local" / "share"
    return base / "man" / "man1"


class InstallManPages(argparse.Action):
    """Copy the installed man page onto the manpath and exit."""

    @override
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        result = install_man_page()
        print(f"man page {result.status}: {result.path}")
        print("run: man anki-tools")
        parser.exit()
