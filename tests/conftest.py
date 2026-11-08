"""Shared pytest fixtures."""

import io
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from anki.collection import Collection, ExportAnkiPackageOptions


@pytest.fixture
def feed_stdin(monkeypatch: pytest.MonkeyPatch) -> Callable[[bytes], None]:
    """Return a helper that feeds *data* to the CLI as ``sys.stdin``."""

    def _feed(data: bytes) -> None:
        monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(data)))

    return _feed


@pytest.fixture(scope="session")
def apkg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a small deck package once per test session."""
    tmp = tmp_path_factory.mktemp("deck")
    col = Collection(str(tmp / "collection.anki2"))
    try:
        notetype = col.models.new("Fixture")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{FrontSide}}<hr id=answer>"
        col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Fixture")
        assert model is not None

        preset = col.decks.add_config("Fixture Preset")
        other_preset = col.decks.add_config("Other Preset")
        deck = col.decks.id("Test Deck")
        other = col.decks.id("Other Deck")
        assert deck is not None
        assert other is not None
        stored = col.decks.get(deck)
        assert stored is not None
        stored["conf"] = preset["id"]
        col.decks.update(stored)
        stored_other = col.decks.get(other)
        assert stored_other is not None
        stored_other["conf"] = other_preset["id"]
        col.decks.update(stored_other)

        col.media.write_data("hello.png", b"fake-png")

        with_media = col.new_note(model)
        with_media.fields[0] = 'front <img src="hello.png">'
        with_media.tags = ["tag"]
        col.add_note(with_media, deck)

        plain = col.new_note(model)
        plain.fields[0] = "other"
        col.add_note(plain, other)

        out = tmp / "fixture.apkg"
        col.export_anki_package(
            out_path=str(out),
            options=ExportAnkiPackageOptions(
                with_scheduling=False,
                with_deck_configs=True,
                with_media=True,
                legacy=False,
            ),
            limit=None,
        )
    finally:
        col.close()
    return out


@pytest.fixture(scope="session")
def split_apkg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a package with one note whose two cards live in different decks."""
    tmp = tmp_path_factory.mktemp("split")
    col = Collection(str(tmp / "collection.anki2"))
    try:
        notetype = col.models.new("Split")
        col.models.add_field(notetype, col.models.new_field("Front"))
        for name, qfmt in (("Card 1", "{{Front}}"), ("Card 2", "{{Front}} rev")):
            template = col.models.new_template(name)
            template["qfmt"] = qfmt
            template["afmt"] = "{{Front}}"
            col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Split")
        assert model is not None

        deck_a = col.decks.id("Deck A")
        deck_b = col.decks.id("Deck B")
        assert deck_a is not None
        assert deck_b is not None

        note = col.new_note(model)
        note.fields[0] = "x"
        col.add_note(note, deck_a)
        second = note.cards()[1]
        second.did = deck_b
        col.update_card(second)

        out = tmp / "split.apkg"
        col.export_anki_package(
            out_path=str(out),
            options=ExportAnkiPackageOptions(
                with_scheduling=False,
                with_deck_configs=False,
                with_media=True,
                legacy=False,
            ),
            limit=None,
        )
    finally:
        col.close()
    return out


@pytest.fixture(scope="session")
def nested_apkg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a package with nested decks, a parent description, and a preset."""
    tmp = tmp_path_factory.mktemp("nested")
    col = Collection(str(tmp / "collection.anki2"))
    try:
        notetype = col.models.new("Nested")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Nested")
        assert model is not None

        preset = col.decks.add_config("Nested Preset")
        parent = col.decks.id("Parent")
        child = col.decks.id("Parent::Child")
        grand = col.decks.id("Parent::Child::Grand")
        sibling = col.decks.id("Sibling")
        assert parent is not None
        assert child is not None
        assert grand is not None
        assert sibling is not None

        stored = col.decks.get(parent)
        assert stored is not None
        stored["desc"] = "parent desc"
        stored["conf"] = preset["id"]
        col.decks.update(stored)

        child_note = col.new_note(model)
        child_note.fields[0] = "child"
        child_note.tags = ["tag", "parent::child"]
        col.add_note(child_note, child)

        grand_note = col.new_note(model)
        grand_note.fields[0] = "grand"
        grand_note.tags = ["deep"]
        col.add_note(grand_note, grand)

        sibling_note = col.new_note(model)
        sibling_note.fields[0] = "sibling"
        sibling_note.tags = ["sibling"]
        col.add_note(sibling_note, sibling)

        out = tmp / "nested.apkg"
        col.export_anki_package(
            out_path=str(out),
            options=ExportAnkiPackageOptions(
                with_scheduling=False,
                with_deck_configs=True,
                with_media=True,
                legacy=False,
            ),
            limit=None,
        )
    finally:
        col.close()
    return out


@pytest.fixture(scope="session")
def scheduled_apkg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a package whose single card has been reviewed."""
    tmp = tmp_path_factory.mktemp("scheduled")
    col = Collection(str(tmp / "collection.anki2"))
    try:
        notetype = col.models.new("Scheduled")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Scheduled")
        assert model is not None

        deck = col.decks.id("Scheduled Deck")
        assert deck is not None
        note = col.new_note(model)
        note.fields[0] = "x"
        col.add_note(note, deck)

        card = col.get_card(col.find_cards("")[0])
        card.start_timer()
        col.sched.answerCard(card, 3)

        out = tmp / "scheduled.apkg"
        col.export_anki_package(
            out_path=str(out),
            options=ExportAnkiPackageOptions(
                with_scheduling=True,
                with_deck_configs=True,
                with_media=True,
                legacy=False,
            ),
            limit=None,
        )
    finally:
        col.close()
    return out
