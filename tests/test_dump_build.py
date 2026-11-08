"""Tests for dumping and building deck packages."""

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest
from anki.collection import Collection, ExportAnkiPackageOptions
from msgspec.structs import replace

from anki_tools.cmd.cli import main
from anki_tools.lib import apkg, convert
from anki_tools.lib import format as fmt
from anki_tools.lib.errors import AnkiToolsError, InvalidDumpError, UnknownFormatError
from anki_tools.lib.model import (
    CardPlacement,
    Deck,
    Dump,
    ExportOptions,
    MediaItem,
    Note,
    Notetype,
    NotetypeField,
    NotetypeTemplate,
)
from anki_tools.lib.utils import _db


def _dump(path: Path, *, with_deck_configs: bool = True, query: str = "") -> Dump:
    with apkg.load(path) as col:
        return convert.to_dump(col, query=query, with_deck_configs=with_deck_configs)


def _rebuild(dump: Dump, path: Path) -> None:
    with convert.from_dump(dump) as col:
        apkg.export(
            col,
            path,
            with_scheduling=False,
            with_deck_configs=bool(dump.deck_configs),
            with_media=True,
        )


def _manifest_only_dump(path: Path) -> Dump:
    """A dump of *path* whose media carries size and digest but no bytes."""
    with apkg.load(path) as col:
        return convert.to_dump(col, query="", with_deck_configs=True, load_media=False)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ({"A"}, {"A"}),
        ({"A::B"}, {"A", "A::B"}),
        ({"A::B::C"}, {"A", "A::B", "A::B::C"}),
        ({"A::B", "C::D"}, {"A", "A::B", "C", "C::D"}),
        ({"AB"}, {"AB"}),
        ({"Default"}, {"Default"}),
        ({"Default::Sub"}, {"Default", "Default::Sub"}),
    ],
)
def test_ancestors(names: set[str], expected: set[str]) -> None:
    assert convert._ancestors(names) == expected


def test_roundtrip_is_lossless(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt) == dump


def test_dump_excludes_implicit_default(apkg_path: Path) -> None:
    dump = _dump(apkg_path)
    names = {deck.name for deck in dump.decks}
    assert names == {"Test Deck", "Other Deck"}


def test_filter_selects_notes_and_decks(apkg_path: Path) -> None:
    dump = _dump(apkg_path, query="tag:tag")
    assert {deck.name for deck in dump.decks} == {"Test Deck"}
    assert len(dump.notes) == 1


def test_dump_captures_media_and_preset(apkg_path: Path) -> None:
    dump = _dump(apkg_path)
    assert [m.name for m in dump.media] == ["hello.png"]
    assert [c.name for c in dump.deck_configs] == ["Fixture Preset", "Other Preset"]


def test_underscore_static_media_is_dumped(tmp_path: Path) -> None:
    src = tmp_path / "static.apkg"
    col = Collection(str(tmp_path / "static.anki2"))
    try:
        notetype = col.models.new("Static")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        col.models.add_template(notetype, template)
        notetype["css"] = '@import "_theme.css";'
        col.models.add(notetype)
        model = col.models.by_name("Static")
        assert model is not None

        deck = col.decks.id("Static Deck")
        assert deck is not None
        col.media.write_data("_theme.css", b"body{}")
        note = col.new_note(model)
        note.fields[0] = "x"
        col.add_note(note, deck)

        col.export_anki_package(
            out_path=str(src),
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

    assert [m.name for m in _dump(src).media] == ["_theme.css"]


def test_filtered_dump_scopes_deck_configs(apkg_path: Path) -> None:
    dump = _dump(apkg_path, query="tag:tag")
    assert [deck.name for deck in dump.decks] == ["Test Deck"]
    assert [c.name for c in dump.deck_configs] == ["Fixture Preset"]


def test_filtered_dump_keeps_referenced_media(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path, query="tag:tag")
    assert [m.name for m in dump.media] == ["hello.png"]
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt, query="tag:tag") == dump


def test_filtered_dump_drops_unreferenced_media(
    apkg_path: Path, tmp_path: Path
) -> None:
    dump = _dump(apkg_path, query="-tag:tag")
    assert dump.media == ()
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt, query="-tag:tag") == dump


def test_filtered_dump_keeps_parent_decks(nested_apkg_path: Path) -> None:
    dump = _dump(nested_apkg_path, query="tag:tag")
    decks = {deck.name: deck for deck in dump.decks}
    assert set(decks) == {"Parent", "Parent::Child"}
    assert decks["Parent"].options["desc"] == "parent desc"
    assert decks["Parent"].config == "Nested Preset"
    assert [c.name for c in dump.deck_configs] == ["Nested Preset"]


def test_filtered_dump_keeps_deep_ancestors(nested_apkg_path: Path) -> None:
    dump = _dump(nested_apkg_path, query="tag:deep")
    assert {deck.name for deck in dump.decks} == {
        "Parent",
        "Parent::Child",
        "Parent::Child::Grand",
    }


def test_filtered_dump_parent_options_survive_rebuild(
    nested_apkg_path: Path, tmp_path: Path
) -> None:
    dump = _dump(nested_apkg_path, query="tag:tag")
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt, query="tag:tag") == dump


def test_full_dump_keeps_all_nested_decks(nested_apkg_path: Path) -> None:
    dump = _dump(nested_apkg_path)
    assert {deck.name for deck in dump.decks} == {
        "Parent",
        "Parent::Child",
        "Parent::Child::Grand",
        "Sibling",
    }


def test_tag_hierarchy_roundtrips(nested_apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(nested_apkg_path, query="tag:parent::child")
    assert len(dump.notes) == 1
    assert "parent::child" in dump.notes[0].tags
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt, query="tag:parent::child") == dump


def test_dump_without_media_skips_reading(apkg_path: Path) -> None:
    with apkg.load(apkg_path) as col:
        dump = convert.to_dump(col, query="", with_deck_configs=True, with_media=False)
    assert dump.media == ()


@pytest.mark.parametrize("name", ["jsonl", "zip"])
def test_read_dump_can_skip_media(apkg_path: Path, tmp_path: Path, name: str) -> None:
    dump = _dump(apkg_path)
    codec = fmt.codec_for(name)
    out = tmp_path / ("dump" if name == "jsonl" else "dump.zip")
    codec.write(dump, out)
    assert fmt.read_dump(codec, str(out), with_media=False).media == ()
    assert fmt.read_dump(codec, str(out)).media == dump.media


def test_read_dump_manifest_only_needs_no_media_files(
    apkg_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "dump"
    dump = _dump(apkg_path)
    fmt.codec_for("jsonl").write(dump, out)
    (out / "media" / "hello.png").unlink()
    loaded = fmt.read_dump(fmt.codec_for("jsonl"), str(out), load_media=False)
    assert [m.ref() for m in loaded.media] == [m.ref() for m in dump.media]
    assert all(m.data is None for m in loaded.media)


def test_missing_media_file_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "media" / "hello.png").unlink()
    with pytest.raises(InvalidDumpError, match="missing media"):
        fmt.codec_for("jsonl").read(out)


@pytest.mark.parametrize("name", ["jsonl", "zip"])
def test_build_rejects_media_missing_from_a_dump(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    source = tmp_path / ("dump" if name == "jsonl" else "dump.zip")
    assert main(["dump", str(apkg_path), "-o", str(source)]) == 0
    capsys.readouterr()

    if name == "jsonl":
        (source / "media" / "hello.png").unlink()
    else:
        stripped = tmp_path / "stripped.zip"
        with zipfile.ZipFile(source) as zin, zipfile.ZipFile(stripped, "w") as zout:
            for member in zin.namelist():
                if not member.startswith("media/"):
                    zout.writestr(member, zin.read(member))
        source = stripped

    assert main(["build", str(source), "-o", str(tmp_path / "out.apkg")]) == 1
    assert "missing media file" in capsys.readouterr().err


def test_wrong_format_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    meta = json.loads((out / "meta.json").read_text())
    meta["format"] = "other"
    (out / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(InvalidDumpError, match="not an anki-tools dump"):
        fmt.codec_for("jsonl").read(out)


def test_build_from_stdin(
    apkg_path: Path, tmp_path: Path, feed_stdin: Callable[[bytes], None]
) -> None:
    bundle = tmp_path / "dump.zip"
    assert main(["dump", str(apkg_path), "-o", str(bundle), "--with-deck-configs"]) == 0
    feed_stdin(bundle.read_bytes())
    rebuilt = tmp_path / "rebuilt.apkg"
    assert main(["build", "-", "-o", str(rebuilt)]) == 0
    assert _dump(rebuilt) == _dump(apkg_path)


def test_export_options_roundtrip(apkg_path: Path, tmp_path: Path) -> None:
    with apkg.load(apkg_path) as col:
        dump = convert.to_dump(
            col, query="tag:tag", with_deck_configs=True, with_media=False
        )
    expected = ExportOptions(query="tag:tag", with_deck_configs=True, with_media=False)
    assert dump.export_options == expected
    for name, out in (("jsonl", tmp_path / "d"), ("zip", tmp_path / "d.zip")):
        codec = fmt.codec_for(name)
        codec.write(dump, out)
        assert codec.read(out).export_options == expected


def test_meta_records_export_options(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    args = ["dump", str(apkg_path), "-o", str(out), "--with-deck-configs", "--no-media"]
    assert main(args) == 0
    meta = json.loads((out / "meta.json").read_text())
    assert meta["export_options"] == {
        "query": "",
        "with_deck_configs": True,
        "with_media": False,
    }


def test_version_mismatch(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(dump, out)
    meta = json.loads((out / "meta.json").read_text())
    meta["version"] = 999
    (out / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_missing_section_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(dump, out)
    (out / "notes.jsonl").unlink()
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_malformed_section_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(dump, out)
    (out / "notetypes.json").write_text("{ not json")
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_invalid_jsonl_record_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "notes.jsonl").write_text('{"broken":\n')
    with pytest.raises(InvalidDumpError, match="invalid record"):
        fmt.codec_for("jsonl").read(out)


def test_blank_lines_are_skipped(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path)
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(dump, out)
    notes = (out / "notes.jsonl").read_text()
    (out / "notes.jsonl").write_text(f"\n{notes}\n\n")
    assert fmt.codec_for("jsonl").read(out).notes == dump.notes


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [("tags", "tag"), ("fields", ["Front"])],
)
def test_wrong_typed_note_field_is_rejected(
    apkg_path: Path, tmp_path: Path, key: str, bad_value: object
) -> None:
    dump = _dump(apkg_path)
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(dump, out)
    lines = (out / "notes.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    record[key] = bad_value
    lines[0] = json.dumps(record)
    (out / "notes.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def _one_note_dump(
    note: Note,
    fields: tuple[NotetypeField, ...],
    templates: tuple[NotetypeTemplate, ...],
) -> Dump:
    """A one-note dump for a note type with *fields* and *templates*."""
    notetype = Notetype(
        name="T",
        kind=0,
        css="",
        sort_field=fields[0].name,
        fields=fields,
        templates=templates,
        latex_pre="",
        latex_post="",
    )
    return Dump(
        notetypes=(notetype,),
        decks=(Deck(name="D", config="Default"),),
        deck_configs=(),
        notes=(note,),
        media=(),
        export_options=ExportOptions(),
    )


def _minimal_dump(note: Note) -> Dump:
    """A one-note dump with a two-field note type."""
    return _one_note_dump(
        note,
        (NotetypeField("Alpha"), NotetypeField("Beta")),
        (NotetypeTemplate(name="C1", qfmt="{{Alpha}}", afmt="{{Alpha}}"),),
    )


def test_note_fields_are_assigned_by_name() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Beta": "b", "Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"),),
    )
    with convert.from_dump(_minimal_dump(note)) as col:
        created = col.get_note(col.find_notes("")[0])
    assert dict(created.items()) == {"Alpha": "a", "Beta": "b"}


def test_unknown_note_field_is_rejected() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a", "Gamma": "x"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"),),
    )
    with pytest.raises(AnkiToolsError), convert.from_dump(_minimal_dump(note)):
        pass


def test_unknown_notetype_is_rejected() -> None:
    note = Note(
        guid="g",
        notetype="Nope",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"),),
    )
    with pytest.raises(AnkiToolsError), convert.from_dump(_minimal_dump(note)):
        pass


def test_unknown_deck_is_rejected() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="Nope"),),
    )
    with pytest.raises(AnkiToolsError), convert.from_dump(_minimal_dump(note)):
        pass


def _split_dump(note: Note) -> Dump:
    """A one-note dump whose note type has two templates."""
    return _one_note_dump(
        note,
        (NotetypeField("Alpha"),),
        (
            NotetypeTemplate(name="C1", qfmt="{{Alpha}}", afmt="{{Alpha}}"),
            NotetypeTemplate(name="C2", qfmt="{{Alpha}} rev", afmt="{{Alpha}}"),
        ),
    )


def test_unknown_card_deck_is_rejected() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"), CardPlacement(ord=1, deck="Nope")),
    )
    with pytest.raises(AnkiToolsError), convert.from_dump(_split_dump(note)):
        pass


def test_removed_card_is_not_recreated() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"),),
    )
    with convert.from_dump(_split_dump(note)) as col:
        created = col.get_note(col.find_notes("")[0])
        assert [card.ord for card in created.cards()] == [0]


def test_card_not_in_notetype_is_rejected() -> None:
    note = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"), CardPlacement(ord=5, deck="D")),
    )
    with pytest.raises(AnkiToolsError), convert.from_dump(_minimal_dump(note)):
        pass


def test_orphan_note_is_skipped() -> None:
    note = Note(guid="g", notetype="T", fields={"Alpha": "a"}, tags=())
    with convert.from_dump(_minimal_dump(note)) as col:
        assert col.find_notes("") == []


def test_build_warns_about_orphan_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "dump"
    note = Note(guid="g", notetype="T", fields={"Alpha": "a"}, tags=())
    fmt.codec_for("jsonl").write(_minimal_dump(note), out)
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 0
    assert "no cards" in capsys.readouterr().err


def _orphan_package(tmp_path: Path) -> Path:
    """Build a package with one normal note and one note whose cards were deleted."""
    src = tmp_path / "orphan.apkg"
    col = Collection(str(tmp_path / "orphan.anki2"))
    try:
        notetype = col.models.new("Orphan")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Orphan")
        assert model is not None

        deck = col.decks.id("Orphan Deck")
        assert deck is not None
        kept = col.new_note(model)
        kept.fields[0] = "kept"
        col.add_note(kept, deck)
        orphan = col.new_note(model)
        orphan.fields[0] = "orphan"
        col.add_note(orphan, deck)
        _db(col).execute("delete from cards where nid = ?", orphan.id)

        col.export_anki_package(
            out_path=str(src),
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
    return src


def test_dump_warns_about_orphan_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _orphan_package(tmp_path)
    out = tmp_path / "dump"
    assert main(["dump", str(src), "-o", str(out)]) == 0
    assert "no cards" in capsys.readouterr().err


def test_build_rejects_media_without_with_media(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    # A merge can pair media.jsonl from one side with meta.json from the other.
    meta = json.loads((out / "meta.json").read_text())
    meta["export_options"]["with_media"] = False
    (out / "meta.json").write_text(json.dumps(meta))
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 1
    assert "with_media" in capsys.readouterr().err


def test_orphan_note_count() -> None:
    with_cards = Note(
        guid="g",
        notetype="T",
        fields={"Alpha": "a"},
        tags=(),
        cards=(CardPlacement(ord=0, deck="D"),),
    )
    without = Note(guid="h", notetype="T", fields={"Alpha": "b"}, tags=())
    dump = _one_note_dump(
        with_cards,
        (NotetypeField("Alpha"),),
        (NotetypeTemplate(name="C1", qfmt="{{Alpha}}", afmt="{{Alpha}}"),),
    )
    assert convert.orphan_note_count(replace(dump, notes=(with_cards, without))) == 1


def test_unsafe_media_name_is_rejected_on_read(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "media.jsonl").write_text(
        '{"name": "../secret", "size": 1, "sha256": "00"}\n'
    )
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_unsafe_media_name_is_rejected_on_write(
    apkg_path: Path, tmp_path: Path
) -> None:
    dump = _dump(apkg_path)
    bad = replace(dump, media=(MediaItem.from_bytes("../secret", b"z"),))
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").write(bad, tmp_path / "dump")


def test_tampered_media_is_rejected_on_read(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "media" / "hello.png").write_bytes(b"tampered")
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_wrong_media_digest_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "media.jsonl").write_text(
        '{"name": "hello.png", "size": 9, "sha256": "00"}\n'
    )
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("jsonl").read(out)


def test_wrong_media_size_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    manifest = out / "media.jsonl"
    entry = json.loads(manifest.read_text().splitlines()[0])
    entry["size"] += 1
    manifest.write_text(json.dumps(entry) + "\n")
    with pytest.raises(InvalidDumpError, match="size"):
        fmt.codec_for("jsonl").read(out)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"tampered", "manifest"), (b"way too long", "size")],
)
def test_build_rejects_tampered_media(
    apkg_path: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    payload: bytes,
    expected: str,
) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out)]) == 0
    capsys.readouterr()
    (out / "media" / "hello.png").write_bytes(payload)
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 1
    assert expected in capsys.readouterr().err


def test_dump_streams_media_from_the_package(apkg_path: Path, tmp_path: Path) -> None:
    dump_dir = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(dump_dir)]) == 0
    assert (dump_dir / "media" / "hello.png").read_bytes() == b"fake-png"

    archive = tmp_path / "dump.zip"
    assert main(["dump", str(apkg_path), "-o", str(archive)]) == 0
    with zipfile.ZipFile(archive) as zf:
        assert zf.read("media/hello.png") == b"fake-png"
        assert zf.getinfo("media/hello.png").compress_type == zipfile.ZIP_STORED


def test_build_from_zip_dump_reads_media(apkg_path: Path, tmp_path: Path) -> None:
    archive = tmp_path / "dump.zip"
    assert (
        main(["dump", str(apkg_path), "-o", str(archive), "--with-deck-configs"]) == 0
    )
    rebuilt = tmp_path / "rebuilt.apkg"
    assert main(["build", str(archive), "-o", str(rebuilt)]) == 0
    assert _dump(rebuilt) == _dump(apkg_path)


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    out = tmp_path / "evil.zip"
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("../escape.txt", "x")
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("zip").read(out)


def test_garbage_zip_is_rejected(tmp_path: Path) -> None:
    out = tmp_path / "bad.zip"
    out.write_bytes(b"not a zip")
    with pytest.raises(InvalidDumpError):
        fmt.codec_for("zip").read(out)


def test_build_of_corrupt_dump_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = tmp_path / "dump"
    dump.mkdir()
    (dump / "meta.json").write_text(
        json.dumps(
            {
                "format": "anki-tools-dump",
                "version": 1,
                "export_options": {
                    "query": "",
                    "with_deck_configs": False,
                    "with_media": True,
                },
            }
        )
    )
    (dump / "notetypes.json").write_text("not json")
    (dump / "decks.json").write_text("[]")
    (dump / "deck_configs.json").write_text("[]")
    (dump / "notes.jsonl").write_text("")
    (dump / "media.jsonl").write_text("")
    assert main(["build", str(dump), "-o", str(tmp_path / "out.apkg")]) == 1
    assert "anki-tools:" in capsys.readouterr().err


def test_cli_dump_and_build(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out), "--with-deck-configs"]) == 0
    assert (out / "notes.jsonl").exists()

    rebuilt = tmp_path / "rebuilt.apkg"
    assert main(["build", str(out), "-o", str(rebuilt)]) == 0
    assert _dump(rebuilt) == _dump(apkg_path)


def test_dump_no_media_flag(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out), "--no-media"]) == 0
    assert (out / "media.jsonl").read_text() == ""
    assert not (out / "media").exists()


def test_redump_clears_stale_media(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out)]) == 0
    assert (out / "media").is_dir()
    assert main(["dump", str(apkg_path), "-o", str(out), "--no-media"]) == 0
    assert not (out / "media").exists()


def test_dump_to_stdout(
    apkg_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert main(["dump", str(apkg_path), "--no-media"]) == 0
    assert capsysbinary.readouterr().out[:2] == b"PK"


def test_build_to_stdout(
    apkg_path: Path, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out), "--no-media"]) == 0
    assert main(["build", str(out), "-o", "-"]) == 0
    assert capsysbinary.readouterr().out[:2] == b"PK"


def test_dump_explicit_zip_format(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "d.zip"
    assert (
        main(["dump", str(apkg_path), "-f", "zip", "-o", str(out), "--no-media"]) == 0
    )
    assert out.read_bytes()[:2] == b"PK"


def test_dump_directory_to_stdout_is_rejected(
    apkg_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["dump", str(apkg_path), "-f", "jsonl"]) == 1
    assert "writes a directory" in capsys.readouterr().err


def test_build_directory_from_stdin_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["build", "-", "-f", "jsonl"]) == 1
    assert "reads a directory" in capsys.readouterr().err


def test_format_name_is_latest(apkg_path: Path) -> None:
    assert apkg.format_name(apkg_path) == "latest"


def test_format_name_legacy1(tmp_path: Path) -> None:
    archive = tmp_path / "legacy1.apkg"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("collection.anki2", b"")
    assert apkg.format_name(archive) == "legacy1"


def test_unknown_format_is_not_a_package(tmp_path: Path) -> None:
    bad = tmp_path / "bad.apkg"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("unrelated.txt", "x")
    assert apkg.format_name(bad) == "unknown"
    with pytest.raises(AnkiToolsError), apkg.load(bad):
        pass


def test_multideck_note_roundtrips(split_apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(split_apkg_path)
    (note,) = dump.notes
    assert {card.deck for card in note.cards} == {"Deck A", "Deck B"}
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt) == dump


def test_dump_without_presets_roundtrips(apkg_path: Path, tmp_path: Path) -> None:
    dump = _dump(apkg_path, with_deck_configs=False)
    assert dump.deck_configs == ()
    assert all(deck.config == "Default" for deck in dump.decks)
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt, with_deck_configs=False) == dump


@pytest.mark.parametrize(
    "fixture", ["apkg_path", "split_apkg_path", "nested_apkg_path"]
)
def test_roundtrip_through_every_format(
    request: pytest.FixtureRequest, tmp_path: Path, fixture: str
) -> None:
    source = request.getfixturevalue(fixture)
    original = _dump(source)

    directory = tmp_path / "dump"
    fmt.codec_for("jsonl").write(original, directory)
    assert fmt.codec_for("jsonl").read(directory) == original

    archive = tmp_path / "dump.zip"
    fmt.codec_for("zip").write(original, archive)
    in_archive = fmt.codec_for("zip").read(archive)
    assert in_archive == original

    manifest_only = fmt.codec_for("zip").read(archive, load_media=False)
    assert [m.ref() for m in manifest_only.media] == [m.ref() for m in original.media]

    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(in_archive, rebuilt)
    assert _dump(rebuilt) == original


def test_media_is_hashed_without_retaining_bytes(apkg_path: Path) -> None:
    with apkg.load(apkg_path) as col:
        loaded = convert.to_dump(col, query="", with_deck_configs=False)
        streamed = convert.to_dump(
            col, query="", with_deck_configs=False, load_media=False
        )
    assert [m.name for m in loaded.media] == [m.name for m in streamed.media]
    assert all(m.data is not None for m in loaded.media)
    assert all(m.data is None for m in streamed.media)
    assert [(m.size, m.sha256) for m in loaded.media] == [
        (m.size, m.sha256) for m in streamed.media
    ]


def test_directory_and_zip_diff_as_equal(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    directory = tmp_path / "d"
    fmt.codec_for("jsonl").write(dump, directory)
    archive = tmp_path / "d.zip"
    fmt.codec_for("zip").write(dump, archive)
    assert main(["diff", str(directory), str(archive), "--with-deck-configs"]) == 0
    assert main(["diff", str(apkg_path), str(archive), "--with-deck-configs"]) == 0
    assert capsys.readouterr().out == ""


def test_option_keys_are_covered(apkg_path: Path) -> None:
    with apkg.load(apkg_path) as col:
        notetype = col.models.by_name("Fixture")
        assert notetype is not None
        field = notetype["flds"][0]
        template = notetype["tmpls"][0]
        deck = next(d for d in col.decks.all() if d["name"] == "Test Deck")

    assert set(
        convert._options(
            notetype,
            "name",
            "type",
            "css",
            "sortf",
            "flds",
            "tmpls",
            "latexPre",
            "latexPost",
        )
    ) == {"latexsvg", "originalStockKind"}
    assert set(convert._options(field, "name")) == {
        "description",
        "excludeFromSearch",
        "font",
        "plainText",
        "preventDeletion",
        "rtl",
        "size",
        "sticky",
        "tag",
    }
    assert set(convert._options(template, "name", "qfmt", "afmt")) == {
        "bafmt",
        "bfont",
        "bqfmt",
        "bsize",
    }
    assert set(convert._options(deck, "name", "conf")) == {"desc"}


def test_custom_options_survive_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "custom.apkg"
    col = Collection(str(tmp_path / "src.anki2"))
    try:
        notetype = col.models.new("Custom")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("C1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        template["bqfmt"] = "{{Front}} browser"
        col.models.add_template(notetype, template)
        col.models.add(notetype)

        model = col.models.by_name("Custom")
        assert model is not None
        model["flds"][0]["rtl"] = True
        model["flds"][0]["sticky"] = True
        model["flds"][0]["font"] = "Courier"
        model["flds"][0]["size"] = 48
        model["latexsvg"] = True
        col.models.update(model)

        deck = col.decks.id("Desc Deck")
        assert deck is not None
        stored = col.decks.get(deck)
        assert stored is not None
        stored["desc"] = "hello desc"
        col.decks.update(stored)

        note = col.new_note(model)
        note.fields[0] = "x"
        col.add_note(note, deck)

        col.export_anki_package(
            out_path=str(src),
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

    dump = _dump(src)
    custom = next(n for n in dump.notetypes if n.name == "Custom")
    assert custom.options["latexsvg"] is True
    assert custom.fields[0].options["rtl"] is True
    assert custom.fields[0].options["font"] == "Courier"
    assert custom.templates[0].options["bqfmt"] == "{{Front}} browser"
    assert next(d for d in dump.decks if d.name == "Desc Deck").options["desc"] == (
        "hello desc"
    )

    with convert.from_dump(dump) as built:
        rebuilt = built.models.by_name("Custom")
        assert rebuilt is not None
        assert rebuilt["latexsvg"] is True
        assert rebuilt["flds"][0]["rtl"] is True
        assert rebuilt["flds"][0]["font"] == "Courier"
        assert rebuilt["tmpls"][0]["bqfmt"] == "{{Front}} browser"
        rebuilt_deck_id = built.decks.id("Desc Deck")
        assert rebuilt_deck_id is not None
        rebuilt_deck = built.decks.get(rebuilt_deck_id)
        assert rebuilt_deck is not None
        assert rebuilt_deck["desc"] == "hello desc"

    assert _dump(src) == dump


def _duplicate_preset_package(tmp_path: Path) -> Path:
    """Build a package with two presets that share the name "P"."""
    src = tmp_path / "duplicate.apkg"
    col = Collection(str(tmp_path / "duplicate.anki2"))
    try:
        notetype = col.models.new("Dup")
        col.models.add_field(notetype, col.models.new_field("Front"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Front}}"
        col.models.add_template(notetype, template)
        col.models.add(notetype)
        model = col.models.by_name("Dup")
        assert model is not None

        for index, per_day in enumerate((5, 9)):
            preset = col.decks.add_config("P")
            preset["new"]["perDay"] = per_day
            col.decks.update_config(preset)
            deck = col.decks.id(f"Deck {index}")
            assert deck is not None
            stored = col.decks.get(deck)
            assert stored is not None
            stored["conf"] = preset["id"]
            col.decks.update(stored)
            note = col.new_note(model)
            note.fields[0] = f"note {index}"
            col.add_note(note, deck)

        col.export_anki_package(
            out_path=str(src),
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
    return src


def test_duplicate_preset_names_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _duplicate_preset_package(tmp_path)
    args = ["dump", str(src), "-o", str(tmp_path / "dump"), "--with-deck-configs"]
    assert main(args) == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "'P'" in err


def test_duplicate_preset_names_are_ignored_without_presets(tmp_path: Path) -> None:
    src = _duplicate_preset_package(tmp_path)
    assert main(["dump", str(src), "-o", str(tmp_path / "dump")]) == 0


@pytest.mark.parametrize(
    ("filename", "pointer", "key"),
    [
        ("decks.json", (0, "options"), "id"),
        ("notetypes.json", (0, "fields", 0, "options"), "ord"),
        ("deck_configs.json", (0, "settings"), "usn"),
    ],
)
def test_reserved_option_keys_are_rejected(
    apkg_path: Path,
    tmp_path: Path,
    filename: str,
    pointer: tuple[int | str, ...],
    key: str,
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    data = json.loads((out / filename).read_text())
    target = data
    for part in pointer:
        target = target[part]
    target[key] = 0
    (out / filename).write_text(json.dumps(data))
    with pytest.raises(InvalidDumpError) as excinfo:
        fmt.codec_for("jsonl").read(out)
    assert f"'{key}'" in str(excinfo.value)
    assert "not allowed in a dump" in str(excinfo.value)


def _notetype_variants_package(tmp_path: Path) -> Path:
    """Build a package with a cloze note type and one sorted by a later field."""
    src = tmp_path / "variants.apkg"
    col = Collection(str(tmp_path / "variants.anki2"))
    try:
        cloze = col.models.new("Cloze Variant")
        col.models.add_field(cloze, col.models.new_field("Text"))
        col.models.add_field(cloze, col.models.new_field("Extra"))
        template = col.models.new_template("Cloze")
        template["qfmt"] = "{{cloze:Text}}"
        template["afmt"] = "{{cloze:Text}}<br>{{Extra}}"
        col.models.add_template(cloze, template)
        cloze["type"] = 1
        col.models.add(cloze)

        sorted_nt = col.models.new("Sorted Variant")
        col.models.add_field(sorted_nt, col.models.new_field("Alpha"))
        col.models.add_field(sorted_nt, col.models.new_field("Beta"))
        template = col.models.new_template("Card 1")
        template["qfmt"] = "{{Alpha}}"
        template["afmt"] = "{{Beta}}"
        col.models.add_template(sorted_nt, template)
        col.models.add(sorted_nt)
        stored = col.models.by_name("Sorted Variant")
        assert stored is not None
        stored["sortf"] = 1
        col.models.update(stored)

        deck = col.decks.id("Variants")
        assert deck is not None
        cloze_model = col.models.by_name("Cloze Variant")
        sorted_model = col.models.by_name("Sorted Variant")
        assert cloze_model is not None
        assert sorted_model is not None
        cloze_note = col.new_note(cloze_model)
        cloze_note.fields[0] = "{{c1::answer}}"
        col.add_note(cloze_note, deck)
        sorted_note = col.new_note(sorted_model)
        sorted_note.fields[0] = "alpha"
        sorted_note.fields[1] = "beta"
        col.add_note(sorted_note, deck)

        col.export_anki_package(
            out_path=str(src),
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
    return src


def test_cloze_and_sort_field_and_multiple_notetypes_roundtrip(
    tmp_path: Path,
) -> None:
    dump = _dump(_notetype_variants_package(tmp_path))
    variants = {notetype.name: notetype for notetype in dump.notetypes}
    assert set(variants) == {"Cloze Variant", "Sorted Variant"}
    assert variants["Cloze Variant"].kind == 1
    assert variants["Sorted Variant"].kind == 0
    assert variants["Sorted Variant"].sort_field == "Beta"

    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt) == dump


def test_preset_settings_keys_are_sorted(apkg_path: Path) -> None:
    dump = _dump(apkg_path)
    assert dump.deck_configs
    for config in dump.deck_configs:
        assert list(config.settings) == sorted(config.settings)


@pytest.mark.parametrize("name", ["jsonl", "zip"])
def test_failed_dump_leaves_existing_dump_intact(
    apkg_path: Path, tmp_path: Path, name: str
) -> None:
    codec = fmt.codec_for(name)
    out = tmp_path / ("dump" if name == "jsonl" else "dump.zip")
    codec.write(_dump(apkg_path), out)
    before = out.read_bytes() if name == "zip" else (out / "notes.jsonl").read_bytes()

    if name == "jsonl":
        missing: fmt.MediaSource = fmt.DirMediaSource(tmp_path / "nowhere")
    else:
        missing = fmt.ZipMediaSource(tmp_path / "nowhere.zip")
    with pytest.raises(InvalidDumpError, match="missing media"):
        codec.write(_manifest_only_dump(apkg_path), out, missing)

    after = out.read_bytes() if name == "zip" else (out / "notes.jsonl").read_bytes()
    assert after == before
    assert not list(tmp_path.glob(f".{out.name}.*"))


def test_failed_dump_creates_nothing(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    with pytest.raises(InvalidDumpError, match="missing media"):
        fmt.codec_for("jsonl").write(
            _manifest_only_dump(apkg_path),
            out,
            fmt.DirMediaSource(tmp_path / "nowhere"),
        )
    assert not out.exists()
    assert not list(tmp_path.glob(".dump.*"))


def test_interrupted_dump_keeps_the_old_dump(
    apkg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    before = (out / "notes.jsonl").read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(fmt, "_copy_media", boom)
    with pytest.raises(KeyboardInterrupt):
        fmt.codec_for("jsonl").write(_dump(apkg_path), out)

    assert (out / "notes.jsonl").read_bytes() == before
    assert not list(tmp_path.glob(".dump.*"))


def test_failed_build_keeps_the_previous_package(
    apkg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out), "--with-deck-configs"]) == 0
    target = tmp_path / "release.apkg"
    target.write_bytes(b"previous release")

    def boom(_col: object, path: Path, **_kwargs: object) -> None:
        path.write_bytes(b"partial garbage")
        raise OSError("disk full")

    monkeypatch.setattr(apkg, "export", boom)
    assert main(["build", str(out), "-o", str(target)]) == 1

    assert target.read_bytes() == b"previous release"
    assert not list(tmp_path.glob(f".{target.name}.*"))


def test_build_creates_missing_output_directory(
    apkg_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "dump"
    assert main(["dump", str(apkg_path), "-o", str(out)]) == 0
    target = tmp_path / "missing" / "nested" / "out.apkg"
    assert main(["build", str(out), "-o", str(target)]) == 0
    assert target.exists()


def test_jsonl_write_rejects_a_file_target(apkg_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "dump"
    target.write_bytes(b"not a directory")
    with pytest.raises(InvalidDumpError, match="is a file"):
        fmt.codec_for("jsonl").write(_dump(apkg_path), target)


def test_zip_write_rejects_a_directory_target(apkg_path: Path, tmp_path: Path) -> None:
    target = tmp_path / "dump.zip"
    target.mkdir()
    with pytest.raises(InvalidDumpError, match="is a directory"):
        fmt.codec_for("zip").write(_dump(apkg_path), target)


@pytest.mark.parametrize(
    "section", ["decks.json", "notetypes.json", "deck_configs.json"]
)
def test_duplicate_names_in_a_dump_are_rejected(
    apkg_path: Path, tmp_path: Path, section: str
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    data = json.loads((out / section).read_text())
    data.append(data[0])
    (out / section).write_text(json.dumps(data))
    with pytest.raises(InvalidDumpError, match="ambiguous"):
        fmt.codec_for("jsonl").read(out)


def test_duplicate_media_names_in_a_dump_are_rejected(
    apkg_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    manifest = out / "media.jsonl"
    manifest.write_text(manifest.read_text() * 2)
    with pytest.raises(InvalidDumpError, match="ambiguous"):
        fmt.codec_for("jsonl").read(out)


def test_build_rejects_a_dump_with_duplicate_names(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    data = json.loads((out / "decks.json").read_text())
    data.append(data[0])
    (out / "decks.json").write_text(json.dumps(data))
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 1
    assert "ambiguous" in capsys.readouterr().err


def test_build_rejects_a_note_type_anki_would_rename(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = _dump(apkg_path)
    renamed = replace(
        dump,
        notetypes=(replace(dump.notetypes[0], name="Basic"),),
        notes=tuple(replace(note, notetype="Basic") for note in dump.notes),
    )
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(renamed, out)
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 1
    err = capsys.readouterr().err
    assert "'Basic'" in err
    assert "renamed" in err


def test_zip_redump_replaces_the_archive(apkg_path: Path, tmp_path: Path) -> None:
    archive = tmp_path / "dump.zip"
    codec = fmt.codec_for("zip")
    codec.write(_dump(apkg_path), archive)
    codec.write(_dump(apkg_path), archive)
    with zipfile.ZipFile(archive) as zf:
        assert "media/hello.png" in zf.namelist()


def test_redump_keeps_unrelated_files(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    (out / "README.md").write_text("mine")
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    assert (out / "README.md").read_text() == "mine"
    assert (out / "media" / "hello.png").read_bytes() == b"fake-png"


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(UnknownFormatError):
        fmt.codec_for("nope")


def test_write_rejects_media_without_data_or_source(
    apkg_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidDumpError, match="has no data"):
        fmt.codec_for("jsonl").write(_manifest_only_dump(apkg_path), tmp_path / "dump")


def test_populate_rejects_media_without_data_or_source(apkg_path: Path) -> None:
    dump = _manifest_only_dump(apkg_path)
    with pytest.raises(AnkiToolsError, match="has no data"), convert.from_dump(dump):
        pass


def _stock_notetype_package(tmp_path: Path) -> Path:
    """Build a package that uses Anki's stock Basic and Cloze note types."""
    src = tmp_path / "stock.apkg"
    col = Collection(str(tmp_path / "stock.anki2"))
    try:
        deck = col.decks.id("Stock")
        assert deck is not None
        for name, field in (("Basic", "Front"), ("Cloze", "Text")):
            notetype = col.models.by_name(name)
            assert notetype is not None
            note = col.new_note(notetype)
            note[field] = "x"
            col.add_note(note, deck)
        col.export_anki_package(
            out_path=str(src),
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
    return src


def test_stock_notetypes_are_renamed_by_anki_and_roundtrip(tmp_path: Path) -> None:
    # A dump keeps the name Anki's importer gave a colliding note type (`Basic`
    # becomes `Basic+`). Do not undo the rename: build refuses a dump that names
    # a stock note type (see the test below).
    dump = _dump(_stock_notetype_package(tmp_path))
    assert sorted(notetype.name for notetype in dump.notetypes) == ["Basic+", "Cloze+"]
    rebuilt = tmp_path / "rebuilt.apkg"
    _rebuild(dump, rebuilt)
    assert _dump(rebuilt) == dump


def test_dump_with_presets_but_no_preset_flag_is_rejected(
    apkg_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    assert json.loads((out / "deck_configs.json").read_text())
    meta = json.loads((out / "meta.json").read_text())
    meta["export_options"]["with_deck_configs"] = False
    (out / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(InvalidDumpError, match="with_deck_configs"):
        fmt.codec_for("jsonl").read(out)


def test_deck_with_unknown_preset_is_rejected(
    apkg_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    decks = json.loads((out / "decks.json").read_text())
    decks[0]["config"] = "Ghost"
    (out / "decks.json").write_text(json.dumps(decks))
    with pytest.raises(InvalidDumpError, match="Ghost"):
        fmt.codec_for("jsonl").read(out)
    assert main(["build", str(out), "-o", str(tmp_path / "out.apkg")]) == 1
    assert "Ghost" in capsys.readouterr().err


def test_unknown_sort_field_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    notetypes = json.loads((out / "notetypes.json").read_text())
    notetypes[0]["sort_field"] = "Nope"
    (out / "notetypes.json").write_text(json.dumps(notetypes))
    with pytest.raises(InvalidDumpError, match="Nope"):
        fmt.codec_for("jsonl").read(out)


def test_duplicate_card_order_is_rejected(apkg_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    lines = (out / "notes.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    record["cards"] = [record["cards"][0], record["cards"][0]]
    lines[0] = json.dumps(record)
    (out / "notes.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(InvalidDumpError, match="twice"):
        fmt.codec_for("jsonl").read(out)


@pytest.mark.parametrize("field", ["notetype", "deck"])
def test_note_with_unknown_reference_is_rejected(
    apkg_path: Path, tmp_path: Path, field: str
) -> None:
    out = tmp_path / "dump"
    fmt.codec_for("jsonl").write(_dump(apkg_path), out)
    lines = (out / "notes.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    if field == "notetype":
        record["notetype"] = "Nope"
    else:
        record["cards"][0]["deck"] = "Nope"
    lines[0] = json.dumps(record)
    (out / "notes.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(InvalidDumpError, match="Nope"):
        fmt.codec_for("jsonl").read(out)


def test_zip_media_source_closes_the_archive(
    apkg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "dump.zip"
    fmt.codec_for("zip").write(_dump(apkg_path), archive)
    closed: list[str | None] = []
    real = fmt.ZipFile

    class Recorder(real):
        @override
        def close(self) -> None:
            closed.append(self.filename)
            super().close()

    monkeypatch.setattr(fmt, "ZipFile", Recorder)
    with fmt.codec_for("zip").media_source(archive).open("hello.png") as handle:
        assert handle.read() == b"fake-png"
    assert closed
