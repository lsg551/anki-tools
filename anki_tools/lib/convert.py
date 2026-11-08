"""Convert between a live collection and the canonical dump model."""

from collections import Counter
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

# NOTE: The bare import must come first: anki.cards/decks/models/notes raise a
# circular import error if one of them is the first anki module a process
# imports.
import anki.collection  # noqa: F401
from anki.cards import CardId
from anki.collection import Collection
from anki.decks import DeckConfigDict, DeckConfigId, DeckDict, DeckId
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from msgspec.structs import replace

from anki_tools.lib.errors import AnkiToolsError, InvalidDumpError
from anki_tools.lib.format import MediaSource
from anki_tools.lib.model import (
    CONFIG_IDENTITY_KEYS,
    DEFAULT_CONFIG,
    DEFAULT_DECK,
    IGNORED_KEYS,
    CardPlacement,
    Deck,
    DeckConfig,
    Dump,
    ExportOptions,
    MediaItem,
    Note,
    Notetype,
    NotetypeField,
    NotetypeTemplate,
)
from anki_tools.lib.utils import _db, _required


def _options(obj: Mapping[str, object], *named: str) -> dict[str, object]:
    """Return *obj*'s Anki options besides its *named* and ignored keys."""
    return {
        key: obj[key]
        for key in sorted(obj)
        if key not in named and key not in IGNORED_KEYS
    }


def to_dump(
    col: Collection,
    *,
    query: str,
    with_deck_configs: bool,
    with_media: bool = True,
    load_media: bool = True,
) -> Dump:
    """Extract a dump from an imported collection."""
    models = col.models.all()
    deck_dicts = col.decks.all()
    config_dicts = col.decks.all_config()
    notes = [_note(col, nid) for nid in col.find_notes(query)]
    decks = _decks(deck_dicts, config_dicts, notes, query=query)
    configs = _deck_configs(config_dicts, decks) if with_deck_configs else ()
    if not with_deck_configs:
        decks = [replace(deck, config=DEFAULT_CONFIG) for deck in decks]
    media = (
        media_items(col, _referenced_media(col, models, notes), load_data=load_media)
        if with_media
        else []
    )
    return Dump(
        notetypes=tuple(sorted(_notetypes(models, notes), key=lambda n: n.name)),
        decks=tuple(sorted(decks, key=lambda d: d.name)),
        deck_configs=tuple(sorted(configs, key=lambda c: c.name)),
        notes=tuple(sorted(notes, key=lambda n: n.guid)),
        media=tuple(sorted(media, key=lambda m: m.name)),
        export_options=ExportOptions(
            query=query, with_deck_configs=with_deck_configs, with_media=with_media
        ),
    )


@contextmanager
def from_dump(
    dump: Dump, media_source: MediaSource | None = None
) -> Generator[Collection]:
    """Yield a temporary collection populated from *dump*.

    Media bytes come from *dump* itself, or from *media_source* when the dump
    was read without them.
    """
    with TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "collection.anki2"))
        try:
            _populate(col, dump, media_source)
            yield col
        finally:
            col.close()


def _note(col: Collection, nid: NoteId) -> Note:
    """Convert a collection note to the dump model."""
    note = col.get_note(nid)
    notetype = _required(note.note_type(), f"note {note.guid} has no note type")
    cards = tuple(
        CardPlacement(ord=card.ord, deck=col.decks.name(card.did))
        for card in sorted(note.cards(), key=lambda card: card.ord)
    )
    return Note(
        guid=note.guid,
        notetype=notetype["name"],
        fields=dict(note.items()),
        tags=tuple(sorted(note.tags)),
        cards=cards,
    )


def _notetypes(models: list[NotetypeDict], notes: list[Note]) -> list[Notetype]:
    """Return the note types used by *notes*."""
    used = {note.notetype for note in notes}
    return [_notetype(nt) for nt in models if nt["name"] in used]


def _notetype(nt: NotetypeDict) -> Notetype:
    """Convert an upstream note type to the dump model."""
    fields = sorted(nt["flds"], key=lambda field: field["ord"])
    templates = sorted(nt["tmpls"], key=lambda template: template["ord"])
    names = [str(field["name"]) for field in fields]
    sortf = int(nt["sortf"])
    return Notetype(
        name=nt["name"],
        kind=int(nt["type"]),
        css=nt["css"],
        sort_field=names[sortf]
        if 0 <= sortf < len(names)
        else (names[0] if names else ""),
        fields=tuple(
            NotetypeField(
                name=str(field["name"]),
                options=_options(field, "name"),
            )
            for field in fields
        ),
        templates=tuple(
            NotetypeTemplate(
                name=template["name"],
                qfmt=template["qfmt"],
                afmt=template["afmt"],
                options=_options(template, "name", "qfmt", "afmt"),
            )
            for template in templates
        ),
        latex_pre=str(nt.get("latexPre", "")),
        latex_post=str(nt.get("latexPost", "")),
        options=_options(
            nt,
            "name",
            "type",
            "css",
            "sortf",
            "flds",
            "tmpls",
            "latexPre",
            "latexPost",
        ),
    )


def _decks(
    deck_dicts: list[DeckDict],
    config_dicts: list[DeckConfigDict],
    notes: list[Note],
    *,
    query: str,
) -> list[Deck]:
    """Return the decks to dump for *notes*."""
    referenced = _referenced_decks(notes)
    by_name = {deck["name"]: deck for deck in deck_dicts}
    config_names = {conf["id"]: str(conf["name"]) for conf in config_dicts}
    if query:
        included = _ancestors(referenced)
    else:
        included = {name for name in by_name if name != DEFAULT_DECK}
        included |= referenced & {DEFAULT_DECK}
    decks = []
    for name in included:
        deck = by_name.get(name)
        if deck is None:
            decks.append(Deck(name=name, config=DEFAULT_CONFIG))
            continue
        config = config_names.get(deck["conf"], DEFAULT_CONFIG)
        decks.append(
            Deck(name=name, config=config, options=_options(deck, "name", "conf"))
        )
    return decks


def _referenced_decks(notes: list[Note]) -> set[str]:
    """Return the deck names referenced by *notes*."""
    return {card.deck for note in notes for card in note.cards}


def _ancestors(names: Iterable[str]) -> set[str]:
    """Return all ancestor deck names of *names*."""
    out: set[str] = set()
    for name in names:
        parts = name.split("::")
        for i in range(1, len(parts) + 1):
            out.add("::".join(parts[:i]))
    return out


def _deck_configs(
    config_dicts: list[DeckConfigDict], decks: list[Deck]
) -> list[DeckConfig]:
    """Return the presets referenced by *decks*."""
    used = {deck.config for deck in decks}
    by_name = _unique_configs(config_dicts, used)
    used.discard(DEFAULT_CONFIG)
    configs = []
    for name in used:
        conf = by_name.get(name)
        if conf is not None:
            settings = {
                key: conf[key]
                for key in sorted(conf)
                if key not in CONFIG_IDENTITY_KEYS
            }
            configs.append(DeckConfig(name=name, settings=settings))
    return configs


def _unique_configs(
    config_dicts: list[DeckConfigDict], used: set[str]
) -> dict[str, DeckConfigDict]:
    """Map preset names to presets, rejecting a name used by more than one."""
    counts = Counter(str(conf["name"]) for conf in config_dicts)
    clash = sorted(name for name, count in counts.items() if count > 1 and name in used)
    if clash:
        raise AnkiToolsError(
            "preset names are ambiguous ("
            + ", ".join(f"'{name}' is used by {counts[name]} presets" for name in clash)
            + "); anki-tools identifies presets by name, so a dump would silently "
            "lose the difference. Rename one of them in Anki, then dump again."
        )
    return {str(conf["name"]): conf for conf in config_dicts}


def media_names(col: Collection) -> list[str]:
    """Return the collection's media file names, sorted."""
    return [path.name for path in _media_paths(col)]


def media_items(
    col: Collection, names: set[str] | None = None, *, load_data: bool = True
) -> list[MediaItem]:
    """Return the collection's media files, restricted to *names* if given."""
    return [
        _media_item(path, load_data=load_data)
        for path in _media_paths(col)
        if names is None or path.name in names
    ]


def _media_paths(col: Collection) -> list[Path]:
    """Return the collection's media file paths, sorted."""
    root = Path(col.media.dir())
    return sorted(p for p in root.iterdir() if p.is_file())


def _media_item(path: Path, *, load_data: bool) -> MediaItem:
    """Build a media item, hashing *path* without retaining it if asked."""
    if load_data:
        return MediaItem.from_bytes(path.name, path.read_bytes())
    digest = sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return MediaItem(name=path.name, size=size, sha256=digest.hexdigest())


def orphan_note_count(dump: Dump) -> int:
    """Return how many notes in *dump* have no cards."""
    return sum(1 for note in dump.notes if not note.cards)


def _referenced_media(
    col: Collection, models: list[NotetypeDict], notes: list[Note]
) -> set[str]:
    """Return the media names *notes* and their note types reference."""
    by_name = {nt["name"]: nt for nt in models}
    names: set[str] = set()
    for notetype in {note.notetype for note in notes}:
        model = by_name.get(notetype)
        if model is not None:
            names |= set(col.media.extract_static_media_files(model["id"]))
    for note in notes:
        model = by_name.get(note.notetype)
        if model is None:
            continue
        for value in note.fields.values():
            names |= set(col.media.files_in_str(model["id"], value))
    return names


def _populate(col: Collection, dump: Dump, media_source: MediaSource | None) -> None:
    """Populate *col* from *dump*."""
    notetype_ids = _add_notetypes(col, dump.notetypes)
    config_ids = _add_deck_configs(col, dump.deck_configs)
    deck_ids = _add_decks(col, dump.decks, config_ids)
    _add_notes(col, dump.notes, notetype_ids, deck_ids)
    for item in dump.media:
        col.media.write_data(item.name, _media_bytes(item, media_source))


def _media_bytes(item: MediaItem, media_source: MediaSource | None) -> bytes:
    """Return *item*'s bytes, read from *media_source* and checked if needed."""
    if item.data is not None:
        return item.data
    if media_source is None:
        raise AnkiToolsError(f"media file {item.name} has no data to write")
    with media_source.open(item.name) as source:
        data = source.read()
    if len(data) != item.size:
        raise InvalidDumpError(
            f"media file {item.name} has size {len(data)}, expected {item.size}"
        )
    if sha256(data).hexdigest() != item.sha256:
        raise InvalidDumpError(f"media file {item.name} does not match its manifest")
    return data


def _add_notetypes(
    col: Collection, notetypes: tuple[Notetype, ...]
) -> dict[str, NotetypeId]:
    """Add the dump's note types and return their ids by name."""
    ids: dict[str, NotetypeId] = {}
    for nt in notetypes:
        model = col.models.new(nt.name)
        model["type"] = nt.kind
        model["css"] = nt.css
        model["latexPre"] = nt.latex_pre
        model["latexPost"] = nt.latex_post
        model.update(nt.options)
        for field in nt.fields:
            created = col.models.new_field(field.name)
            created.update(field.options)
            col.models.add_field(model, created)
        for template in nt.templates:
            tmpl = col.models.new_template(template.name)
            tmpl["qfmt"] = template.qfmt
            tmpl["afmt"] = template.afmt
            tmpl.update(template.options)
            col.models.add_template(model, tmpl)
        names = [field.name for field in nt.fields]
        model["sortf"] = names.index(nt.sort_field) if nt.sort_field in names else 0
        added = col.models.add(model)
        if model["name"] != nt.name:
            raise AnkiToolsError(
                f"note type '{nt.name}' collides with an existing note type and "
                f"would be renamed to '{model['name']}'; the dump is not "
                "reproducible. Remove the duplicate name and dump again."
            )
        ids[nt.name] = NotetypeId(added.id)
    return ids


def _add_deck_configs(
    col: Collection, configs: tuple[DeckConfig, ...]
) -> dict[str, DeckConfigId]:
    """Add the dump's presets and return their ids by name."""
    ids: dict[str, DeckConfigId] = {DEFAULT_CONFIG: DeckConfigId(1)}
    for conf in configs:
        clone: DeckConfigDict = {
            "id": 0,
            "name": conf.name,
            "mod": 0,
            "usn": 0,
            **conf.settings,
        }
        ids[conf.name] = col.decks.add_config(conf.name, clone_from=clone)["id"]
    return ids


def _add_decks(
    col: Collection, decks: tuple[Deck, ...], config_ids: dict[str, DeckConfigId]
) -> dict[str, DeckId]:
    """Add the dump's decks and return their ids by name."""
    ids: dict[str, DeckId] = {DEFAULT_DECK: DeckId(1)}
    for deck in decks:
        did = _required(col.decks.id(deck.name), f"could not create deck '{deck.name}'")
        ids[deck.name] = did
        stored = _required(
            col.decks.get(did), f"deck '{deck.name}' not found after creation"
        )
        stored.update(deck.options)
        config_id = config_ids.get(deck.config)
        if config_id is not None and config_id != DeckConfigId(1):
            stored["conf"] = config_id
        col.decks.update(stored)
    return ids


def _add_notes(
    col: Collection,
    notes: tuple[Note, ...],
    notetype_ids: dict[str, NotetypeId],
    deck_ids: dict[str, DeckId],
) -> None:
    """Add the dump's notes to *col*."""
    for note in notes:
        if not note.cards:
            continue
        model = _required(
            col.models.get(_require(notetype_ids, note.notetype, "note type")),
            f"note type '{note.notetype}' not found after creation",
        )
        created = col.new_note(model)
        created.guid = note.guid
        for name, value in note.fields.items():
            try:
                created[name] = value
            except KeyError as exc:
                raise AnkiToolsError(
                    f"note type '{note.notetype}' has no field '{name}'"
                ) from exc
        created.tags = list(note.tags)
        primary_id = _require(deck_ids, note.cards[0].deck, "deck")
        col.add_note(created, primary_id)
        _place_cards(col, created.id, note.cards, deck_ids, primary_id)


def _require[T](mapping: dict[str, T], name: str, kind: str) -> T:
    """Return the id for *name*, or fail as a malformed dump."""
    try:
        return mapping[name]
    except KeyError as exc:
        raise AnkiToolsError(f"dump references unknown {kind} '{name}'") from exc


def _place_cards(
    col: Collection,
    nid: NoteId,
    cards: tuple[CardPlacement, ...],
    deck_ids: dict[str, DeckId],
    primary_id: DeckId,
) -> None:
    """Keep only the cards recorded in *cards*, and place them."""
    db = _db(col)
    placement = {card.ord: card.deck for card in cards}
    created = {
        int(ord_): CardId(card_id)
        for card_id, ord_ in db.all("select id, ord from cards where nid = ?", nid)
    }
    missing = sorted(set(placement) - set(created))
    if missing:
        raise AnkiToolsError(
            f"dump lists cards {missing} that the note type does not create"
        )
    removed = [card_id for ord_, card_id in created.items() if ord_ not in placement]
    if removed:
        col.remove_cards_and_orphaned_notes(removed)
    for ord_, deck in placement.items():
        target_id = _require(deck_ids, deck, "deck")
        if target_id != primary_id:
            card = col.get_card(created[ord_])
            card.did = target_id
            col.update_card(card)
