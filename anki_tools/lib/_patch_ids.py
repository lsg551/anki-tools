"""Workarounds for import limitations regarding Anki's IDs.

For details, refer to https://github.com/lsg551/anki_tools/issues/5.
"""

from anki.collection import Collection
from anki.models import NotetypeId

from anki_tools.lib.errors import AnkiToolsError
from anki_tools.lib.utils import _db

# HACK: Anki refuses a preset note type id on create. Its `add_notetype`
# asserts the incoming id is 0 and stamps the current time in milliseconds, and
# the public `models` API does not expose the create-with-existing-id path that
# import and sync use. So `build` creates each note type normally and then moves
# its id (and its fields' and templates' ids) to what the dump recorded. Field
# and template ids can be set before the note type is added; only the note type
# id needs the move. Delete this module when Anki exposes the option, see
# issue #5.


def reassign_notetype(col: Collection, current: NotetypeId, target: int) -> None:
    """Move note type *current* to *target*, or fail on a collision.

    Raises:
        AnkiToolsError: if conversion failed
    """
    if current == target:
        return
    db = _db(col)
    if db.scalar("select 1 from notetypes where id = ?", target) is not None:
        raise AnkiToolsError(
            f"note type id {target} already exists in the collection; "
            "the dump is not reproducible"
        )
    db.execute("update notetypes set id = ? where id = ?", target, current)
    db.execute("update fields set ntid = ? where ntid = ?", target, current)
    db.execute("update templates set ntid = ? where ntid = ?", target, current)
    # The backend can hand the freed id to the next note type it creates, so
    # drop the cached entry; otherwise a later read returns the old note type.
    col.models._remove_from_cache(current)
