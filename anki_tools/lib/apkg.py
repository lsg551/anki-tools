"""Load Anki packages into a disposable collection."""

# NOTE: Anki's importer owns format detection and note type renaming. This is
# especially fiddly and relies on knowledge about Anki's internals. See
# docs/anki.md for details, it records some of the upstream behaviour we rely on
# and docs/decisions.md carries non-trivial implementation / design decisions.

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from anki.collection import (
    Collection,
    ExportAnkiPackageOptions,
    ImportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)
from anki.errors import AnkiException

from anki_tools.lib.errors import AnkiToolsError

# Archive members of each package format, newest first.
_FORMATS = {
    "collection.anki21b": "latest",
    "collection.anki21": "legacy2",
    "collection.anki2": "legacy1",
}


def format_name(path: Path) -> str:
    """Return the package format name by inspecting the archive members."""
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, BadZipFile) as exc:
        raise AnkiToolsError(f"not a valid package: {path}: {exc}") from exc
    for member, name in _FORMATS.items():
        if member in names:
            return name
    return "unknown"


@contextmanager
def load(path: Path, *, with_scheduling: bool = True) -> Generator[Collection]:
    """Yield a temporary collection containing the package at *path*."""
    if path.is_dir():
        raise AnkiToolsError(f"is a directory, not a package: {path}")
    if not path.is_file():
        raise AnkiToolsError(f"no such file: {path}")
    with TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "collection.anki2"))
        try:
            try:
                # Keep the name Anki's importer gave a colliding note type
                # (`Basic` becomes `Basic+`).
                col.import_anki_package(
                    ImportAnkiPackageRequest(
                        package_path=str(path),
                        options=ImportAnkiPackageOptions(
                            merge_notetypes=True,
                            with_scheduling=with_scheduling,
                            with_deck_configs=True,
                        ),
                    )
                )
            except AnkiException as exc:
                raise AnkiToolsError(
                    f"could not read {path} (format: {format_name(path)}): {exc}"
                ) from exc
            yield col
        finally:
            col.close()


def export(
    col: Collection,
    path: Path,
    *,
    with_scheduling: bool,
    with_deck_configs: bool,
    with_media: bool,
) -> None:
    """Write *col* to an ``.apkg`` at *path*."""
    col.export_anki_package(
        out_path=str(path),
        options=ExportAnkiPackageOptions(
            with_scheduling=with_scheduling,
            with_deck_configs=with_deck_configs,
            with_media=with_media,
        ),
        limit=None,
    )
