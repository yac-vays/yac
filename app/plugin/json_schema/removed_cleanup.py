import logging

from app.consts import REMOVED
from app.lib.locs import SUBSCHEMAS
from app.lib.locs import SUBSCHEMA_ARRAYS
from app.lib.locs import SUBSCHEMA_OBJECTS
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


def _marked(schema) -> bool:
    return isinstance(schema, dict) and REMOVED in schema


class RemovedCleanup(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        # Must run after add_consts.py and before yac_optional.py
        return True, 100

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Second half of the removing plugins (yac_if, yac_perms, yac_editable):
        those only *mark* a subschema as removed (see consts.REMOVED) so that
        add_consts.py -- which runs on the parent object -- can still tell
        that the schema defines the data at this location, and why it is not
        available for this request:

        - removed by `if`: the data must go (the condition no longer holds),
          add_consts leaves it alone and this plugin drops the subschema, so
          the object's `additionalProperties: false` rejects the stale key;
        - removed by `perms` / `editable`: add_consts has replaced the marker
          with an immutable `const` already, nothing is left to clean up here;
        - not defined at all: no marker, add_consts echoes the stored value as
          a `const` that only "cln" holders may drop.

        This plugin runs on the PARENT of the marked subschemas and drops
        every marked child (a subschema's own post-order plugins run before
        its parent's, so a marker cannot remove itself without being gone by
        the time the parent's add_consts looks for it). yac_optional.py takes
        care of cleaning up the required list of the parent object afterwards.
        """
        del props

        for k in SUBSCHEMAS:
            if _marked(json_schema.get(k)):
                json_schema.pop(k)

        for k in SUBSCHEMA_OBJECTS:
            if isinstance(json_schema.get(k), dict):
                for key in [x for x, s in json_schema[k].items() if _marked(s)]:
                    json_schema[k].pop(key)

        for k in SUBSCHEMA_ARRAYS:
            if isinstance(json_schema.get(k), list):
                json_schema[k] = [s for s in json_schema[k] if not _marked(s)]

        # NOTE: a marker at the current loc must be left alone here -- this is
        # its own post-order pass, the parent's pass (above) drops it later.
        # (The top level never carries a marker, see locs.removed.)
        return json_schema, context


processor = RemovedCleanup()
