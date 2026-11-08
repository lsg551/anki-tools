"""Tests for the shared library helpers."""

from pathlib import Path

import pytest
from anki.collection import Collection

from anki_tools.lib.errors import AnkiToolsError
from anki_tools.lib.utils import _db, _required


def test_db_rejects_a_closed_collection(tmp_path: Path) -> None:
    col = Collection(str(tmp_path / "collection.anki2"))
    col.close()
    with pytest.raises(AnkiToolsError, match="not open"):
        _db(col)


def test_required_rejects_a_missing_value() -> None:
    value: str | None = None
    with pytest.raises(AnkiToolsError, match="boom"):
        _required(value, "boom")
