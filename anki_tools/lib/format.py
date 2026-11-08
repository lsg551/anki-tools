"""Serialize dumps to and from text formats."""

import shutil
import sys
from collections import Counter
from collections.abc import Generator, Iterable, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, mkdtemp
from typing import IO, Any, Protocol
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

import msgspec

from anki_tools.lib.errors import InvalidDumpError, UnknownFormatError
from anki_tools.lib.model import (
    CONFIG_IDENTITY_KEYS,
    DEFAULT_CONFIG,
    DEFAULT_DECK,
    DUMP_FORMAT,
    DUMP_VERSION,
    IGNORED_KEYS,
    Deck,
    DeckConfig,
    Dump,
    ExportOptions,
    MediaItem,
    MediaRef,
    Note,
    Notetype,
)


class MediaSource(Protocol):
    """Read-side access to a dump's media files, one file at a time."""

    def open(self, name: str) -> AbstractContextManager[IO[bytes]]:
        """Open *name* for reading, releasing whatever it holds on exit."""
        ...


@dataclass(frozen=True, slots=True)
class DirMediaSource:
    """Media files stored as files under a dump's ``media/`` directory."""

    root: Path

    def open(self, name: str) -> AbstractContextManager[IO[bytes]]:
        """Open *name* under the media directory."""
        try:
            return (self.root / _media_name(name)).open("rb")
        except OSError as exc:
            raise InvalidDumpError(f"missing media file {name}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ZipMediaSource:
    """Media files stored as ``media/`` members of a dump zip archive."""

    path: Path

    @contextmanager
    def open(self, name: str) -> Generator[IO[bytes]]:
        """Open the ``media/<name>`` member, closing the archive on exit."""
        member = f"media/{_media_name(name)}"
        with ExitStack() as stack:
            try:
                archive = stack.enter_context(ZipFile(self.path))
                source = stack.enter_context(archive.open(member))
            except (OSError, BadZipFile, KeyError) as exc:
                raise InvalidDumpError(f"missing media file {name}: {exc}") from exc
            yield source


class Codec(Protocol):
    """Read and write a dump in one named format."""

    suffix: str
    directory: bool

    def write(
        self, value: Dump, path: Path, media_source: MediaSource | None = None
    ) -> None:
        """Write *value* to *path*, reading media from *media_source* if needed."""
        ...

    def read(
        self, path: Path, *, with_media: bool = True, load_media: bool = True
    ) -> Dump:
        """Read a dump from *path*."""
        ...

    def media_source(self, path: Path) -> MediaSource:
        """Return the media source of the dump stored at *path*."""
        ...


class _Meta(msgspec.Struct, frozen=True):
    """The format, version, and export options header of a dump."""

    format: str
    version: int
    export_options: ExportOptions


_CODECS: dict[str, Codec] = {}

# Dump sections a diff can exclude, in canonical order.
NOTETYPES = "notetypes"
DECKS = "decks"
DECK_CONFIGS = "deck_configs"
NOTES = "notes"
MEDIA = "media"
SECTIONS = (NOTETYPES, DECKS, DECK_CONFIGS, NOTES, MEDIA)


def codec_for(suffix: str) -> Codec:
    """Return the codec registered for *suffix*."""
    try:
        return _CODECS[suffix.lower()]
    except KeyError:
        known = ", ".join(sorted(_CODECS))
        raise UnknownFormatError(
            f"unknown format '{suffix}' (known: {known})"
        ) from None


def suffixes() -> list[str]:
    """Return the registered format suffixes."""
    return sorted(_CODECS)


def dump_codec(path: str) -> Codec | None:
    """Return the codec for a dump at *path*, or None if *path* is not a dump."""
    if path != "-" and Path(path).is_dir():
        return codec_for("jsonl")
    if path == "-" or path.lower().endswith(".zip"):
        return codec_for("zip")
    return None


def resolve(explicit: str | None, path: str) -> Codec:
    """Pick a codec from an explicit format or the path."""
    if explicit is not None:
        return codec_for(explicit)
    return dump_codec(path) or codec_for("jsonl")


def write_dump(
    codec: Codec,
    value: Dump,
    output: str,
    media_source: MediaSource | None = None,
) -> None:
    """Write a dump to *output* (a path, or ``-`` for stdout)."""
    if output != "-":
        codec.write(value, Path(output), media_source)
        return
    if codec.directory:
        raise UnknownFormatError(f"'{codec.suffix}' writes a directory; use -o DIR")
    with stream_to_stdout() as path:
        codec.write(value, path, media_source)


@contextmanager
def stream_to_stdout() -> Generator[Path]:
    """Yield a temporary path whose contents are written to stdout on exit."""
    with NamedTemporaryFile(delete=False) as tmp:
        path = Path(tmp.name)
    try:
        yield path
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, sys.stdout.buffer)
        sys.stdout.buffer.flush()
    finally:
        path.unlink(missing_ok=True)


def read_dump(
    codec: Codec, source: str, *, with_media: bool = True, load_media: bool = True
) -> Dump:
    """Read a dump from *source* (a path, or ``-`` for stdin)."""
    if source != "-":
        return codec.read(Path(source), with_media=with_media, load_media=load_media)
    if codec.directory:
        raise UnknownFormatError(f"'{codec.suffix}' reads a directory; pass the path")
    with NamedTemporaryFile(delete=False) as tmp:
        shutil.copyfileobj(sys.stdin.buffer, tmp)
        tmp_path = Path(tmp.name)
    try:
        return codec.read(tmp_path, with_media=with_media, load_media=load_media)
    finally:
        tmp_path.unlink(missing_ok=True)


class JsonlCodec:
    """A directory of JSON Lines files."""

    suffix = "jsonl"
    directory = True

    def write(
        self, value: Dump, path: Path, media_source: MediaSource | None = None
    ) -> None:
        """Write the dump as a directory of files."""
        if path.exists() and not path.is_dir():
            raise InvalidDumpError(f"{path} is a file, not a dump directory")
        _validate(value)
        with staged(path) as payload:
            payload.mkdir(parents=True)
            for name, data in _dump_files(value).items():
                (payload / name).write_bytes(data)
            if value.media:
                media_dir = payload / "media"
                media_dir.mkdir()
                for item in value.media:
                    target = media_dir / _media_name(item.name)
                    with target.open("wb") as dest:
                        _copy_media(item, dest, media_source)

    def read(
        self, path: Path, *, with_media: bool = True, load_media: bool = True
    ) -> Dump:
        """Read a dump from a directory of files."""
        meta = _read(path / "meta.json", _Meta)
        _check_meta(meta)
        dump = Dump(
            notetypes=_read(path / "notetypes.json", tuple[Notetype, ...]),
            decks=_read(path / "decks.json", tuple[Deck, ...]),
            deck_configs=_read(path / "deck_configs.json", tuple[DeckConfig, ...]),
            notes=_read_lines(path / "notes.jsonl", Note),
            media=_read_media_dir(path, load_data=load_media) if with_media else (),
            export_options=meta.export_options,
        )
        _validate(dump)
        return dump

    def media_source(self, path: Path) -> MediaSource:
        """Return the media directory of the dump stored at *path*."""
        return DirMediaSource(path / "media")


class ZipCodec:
    """A zip archive containing the JSON Lines layout."""

    suffix = "zip"
    directory = False

    def write(
        self, value: Dump, path: Path, media_source: MediaSource | None = None
    ) -> None:
        """Write the dump as a zip archive."""
        if path.is_dir():
            raise InvalidDumpError(f"{path} is a directory; a dump archive is a file")
        _validate(value)
        with staged(path) as payload, ZipFile(payload, "w", ZIP_DEFLATED) as archive:
            for name, data in _dump_files(value).items():
                archive.writestr(name, data)
            for item in value.media:
                info = ZipInfo(f"media/{_media_name(item.name)}")
                info.compress_type = ZIP_STORED
                info.file_size = item.size
                with archive.open(info, "w") as dest:
                    _copy_media(item, dest, media_source)

    def read(
        self, path: Path, *, with_media: bool = True, load_media: bool = True
    ) -> Dump:
        """Read a dump from a zip archive."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "dump"
            try:
                with ZipFile(path) as archive:
                    members = archive.namelist()
                    for name in members:
                        _safe_member(name)
                    if not (with_media and load_media):
                        members = [
                            name for name in members if not name.startswith("media/")
                        ]
                    archive.extractall(root, members=members)
            except (OSError, BadZipFile) as exc:
                raise InvalidDumpError(f"cannot read {path}: {exc}") from exc
            return JsonlCodec().read(root, with_media=with_media, load_media=load_media)

    def media_source(self, path: Path) -> MediaSource:
        """Return the media source inside the archive stored at *path*."""
        return ZipMediaSource(path)


_CODECS[JsonlCodec.suffix] = JsonlCodec()
_CODECS[ZipCodec.suffix] = ZipCodec()


def _dump_files(value: Dump) -> dict[str, bytes]:
    """Return the dump's non-media members as bytes by file name."""
    return {
        "meta.json": _pretty(_Meta(DUMP_FORMAT, DUMP_VERSION, value.export_options)),
        "notetypes.json": _pretty(value.notetypes),
        "decks.json": _pretty(value.decks),
        "deck_configs.json": _pretty(value.deck_configs),
        "notes.jsonl": _lines(value.notes),
        "media.jsonl": _lines(item.ref() for item in value.media),
    }


def _pretty(obj: object) -> bytes:
    """Serialize *obj* as pretty JSON with a trailing newline."""
    return msgspec.json.format(msgspec.json.encode(obj), indent=2) + b"\n"


def _lines(objs: Iterable[Any]) -> bytes:
    """Serialize *objs* as one JSON object per line."""
    return b"".join(msgspec.json.encode(o) + b"\n" for o in objs)


def _read_bytes(path: Path) -> bytes:
    """Read *path*, reporting an I/O failure as an invalid dump."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InvalidDumpError(f"cannot read {path}: {exc}") from exc


def _read[T](path: Path, as_type: type[T]) -> T:
    """Decode a JSON file as *as_type*, reporting failures as invalid dumps."""
    try:
        return msgspec.json.decode(_read_bytes(path), type=as_type)
    except msgspec.DecodeError as exc:
        raise InvalidDumpError(f"invalid JSON in {path}: {exc}") from exc


def _read_lines[T](path: Path, as_type: type[T]) -> tuple[T, ...]:
    """Decode a JSON Lines file as *as_type*, skipping blank lines."""
    records: list[T] = []
    for lineno, line in enumerate(_read_bytes(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(msgspec.json.decode(line, type=as_type))
        except msgspec.DecodeError as exc:
            raise InvalidDumpError(f"invalid record in {path}:{lineno}: {exc}") from exc
    return tuple(records)


def _media_name(name: str) -> str:
    """Reject a media name that could escape the media directory."""
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise InvalidDumpError(f"invalid media name: {name!r}")
    return name


def _copy_media(
    item: MediaItem, dest: IO[bytes], media_source: MediaSource | None
) -> None:
    """Copy one media file into *dest*, from the item's bytes or its source."""
    if item.data is not None:
        dest.write(item.data)
        return
    if media_source is None:
        raise InvalidDumpError(f"media file {item.name} has no data to write")
    with media_source.open(item.name) as source:
        shutil.copyfileobj(source, dest)


@contextmanager
def staged(path: Path) -> Generator[Path]:
    """Yield a staging path that is published to *path* once the block succeeds.

    A failed or interrupted write leaves an existing *path* untouched and
    removes the staging directory, so a truncated file is never published.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    try:
        yield staging / path.name
        _publish(staging / path.name, path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _publish(payload: Path, target: Path) -> None:
    """Move *payload* into place at *target*, replacing what is already there."""
    if payload.is_dir():
        _publish_directory(payload, target)
    else:
        payload.replace(target)


def _publish_directory(payload: Path, target: Path) -> None:
    """Replace the dump's own files in *target*, leaving anything else alone."""
    target.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in sorted(payload.iterdir()) if entry.name != "media"]
    shutil.rmtree(target / "media", ignore_errors=True)
    for entry in entries:
        entry.replace(target / entry.name)
    media = payload / "media"
    if media.is_dir():
        media.rename(target / "media")


def _media(name: str, data: bytes, expected_sha256: str) -> MediaItem:
    """Build a media item and check it against its manifest."""
    item = MediaItem.from_bytes(name, data)
    if item.sha256 != expected_sha256:
        raise InvalidDumpError(f"media file {name} does not match its manifest")
    return item


def _read_media_dir(root: Path, *, load_data: bool) -> tuple[MediaItem, ...]:
    """Read the media manifest, and the files too when *load_data* is set."""
    items = []
    for entry in _read_lines(root / "media.jsonl", MediaRef):
        name = _media_name(entry.name)
        if not load_data:
            items.append(MediaItem(name=name, size=entry.size, sha256=entry.sha256))
            continue
        try:
            data = (root / "media" / name).read_bytes()
        except OSError as exc:
            raise InvalidDumpError(f"missing media file {name}: {exc}") from exc
        if len(data) != entry.size:
            raise InvalidDumpError(
                f"media file {name} has size {len(data)}, expected {entry.size}"
            )
        items.append(_media(name, data, entry.sha256))
    return tuple(items)


def _safe_member(name: str) -> None:
    """Reject an archive member that could escape the extraction root."""
    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
        raise InvalidDumpError(f"unsafe archive member: {name!r}")


def _check_meta(meta: _Meta) -> None:
    """Validate a dump's format and version header."""
    if meta.format != DUMP_FORMAT:
        raise InvalidDumpError(f"not an anki-tools dump (format: {meta.format!r})")
    if meta.version != DUMP_VERSION:
        raise InvalidDumpError(
            f"unsupported dump version {meta.version!r} (expected {DUMP_VERSION})"
        )


def _validate(dump: Dump) -> None:
    """Reject a dump the tool could not reproduce without collapsing content."""
    _check_options_are_dumpable(dump)
    _check_export_options(dump)
    _check_unique_names([notetype.name for notetype in dump.notetypes], "note type")
    _check_unique_names([deck.name for deck in dump.decks], "deck")
    _check_unique_names([config.name for config in dump.deck_configs], "preset")
    _check_unique_names([item.name for item in dump.media], "media file")
    _check_references(dump)


def _check_export_options(dump: Dump) -> None:
    """Reject a dump whose recorded options contradict what it stores."""
    if dump.media and not dump.export_options.with_media:
        raise InvalidDumpError("the dump carries media but records with_media=False")
    if dump.deck_configs and not dump.export_options.with_deck_configs:
        raise InvalidDumpError(
            "the dump carries presets but records with_deck_configs=False"
        )


def _check_references(dump: Dump) -> None:
    """Reject a dump whose entities point at something it does not contain."""
    presets = {config.name for config in dump.deck_configs}
    notetypes = {notetype.name for notetype in dump.notetypes}
    decks = {deck.name for deck in dump.decks} | {DEFAULT_DECK}
    for deck in dump.decks:
        if deck.config != DEFAULT_CONFIG and deck.config not in presets:
            raise InvalidDumpError(
                f"deck '{deck.name}' uses preset '{deck.config}', which the dump "
                "does not contain"
            )
    for notetype in dump.notetypes:
        names = [field.name for field in notetype.fields]
        if notetype.sort_field not in names:
            raise InvalidDumpError(
                f"note type '{notetype.name}' sorts by '{notetype.sort_field}', "
                "which is not one of its fields"
            )
    for note in dump.notes:
        if note.notetype not in notetypes:
            raise InvalidDumpError(
                f"note '{note.guid}' uses note type '{note.notetype}', which the "
                "dump does not contain"
            )
        ords = [card.ord for card in note.cards]
        if len(set(ords)) != len(ords):
            raise InvalidDumpError(
                f"note '{note.guid}' lists card order {sorted(ords)} twice"
            )
        for card in note.cards:
            if card.deck not in decks:
                raise InvalidDumpError(
                    f"note '{note.guid}' has a card in deck '{card.deck}', which "
                    "the dump does not contain"
                )


def _check_unique_names(names: list[str], kind: str) -> None:
    """Reject a *kind* name used more than once; a dump identifies those by name."""
    counts = Counter(names)
    clashes = sorted(name for name, count in counts.items() if count > 1)
    if clashes:
        raise InvalidDumpError(
            f"{kind} names are ambiguous ("
            + ", ".join(f"'{name}' appears {counts[name]} times" for name in clashes)
            + "); a dump identifies them by name, so the difference would be lost. "
            "Remove the duplicate and dump again."
        )


def _check_options_are_dumpable(dump: Dump) -> None:
    """Reject a dump whose options carry keys the dump format never stores."""
    for notetype in dump.notetypes:
        where = f"notetype '{notetype.name}'"
        _check_options(where, notetype.options)
        for field in notetype.fields:
            _check_options(f"field '{field.name}' of {where}", field.options)
        for template in notetype.templates:
            _check_options(f"template '{template.name}' of {where}", template.options)
    for deck in dump.decks:
        _check_options(f"deck '{deck.name}'", deck.options)
    for config in dump.deck_configs:
        _check_options(f"preset '{config.name}'", config.settings, CONFIG_IDENTITY_KEYS)


def _check_options(
    where: str, options: Mapping[str, object], keys: frozenset[str] = IGNORED_KEYS
) -> None:
    """Fail if *options* stores a key that a dump never keeps."""
    reserved = sorted(set(options) & keys)
    if reserved:
        raise InvalidDumpError(
            f"{where}: option '{reserved[0]}' is not allowed in a dump "
            "(Anki generates it on import)"
        )


def canonical_text(dump: Dump, *, exclude: frozenset[str] = frozenset()) -> str:
    """Render *dump* as one tagged line per entity for text diffing."""
    with_config = DECK_CONFIGS not in exclude
    lines: list[str] = []
    if NOTETYPES not in exclude:
        lines += [_line("@notetype", n) for n in dump.notetypes]
    if DECKS not in exclude:
        lines += [_line("@deck", _deck_obj(d, with_config)) for d in dump.decks]
    if with_config:
        lines += [_line("@deck_config", c) for c in dump.deck_configs]
    if NOTES not in exclude:
        lines += [_line("@note", n) for n in dump.notes]
    if MEDIA not in exclude:
        lines += [_line("@media", m.ref()) for m in dump.media]
    return "".join(f"{line}\n" for line in lines)


def _deck_obj(deck: Deck, with_config: bool) -> object:
    """Render a deck, omitting its preset when presets are excluded."""
    if with_config:
        return deck
    return {"name": deck.name, "options": deck.options}


def _line(tag: str, obj: object) -> str:
    """Render one canonical dump line."""
    return f"{tag} {msgspec.json.encode(obj).decode('utf-8')}"
