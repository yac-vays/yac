import logging

from app.model.err import SchemaSpecsError
from app.model.plg import IJsonSchema
from app.lib import locs

logger = logging.getLogger(__name__)


class YacPerms(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        return False, 0

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Removes subschemas if the user has not at least one perm defined in yac_perms.
        Permissions are applied recursively, so if not overwritten on the current
        level of the schema, the perms defined in the next higher level will be used.
        Top-level, the default ist [add, edt].

        If inside object properties, yac_optional.py takes care of cleaning up the
        required list.
        """
        if loc == "#":
            context["yac_perms"] = {"#": ["add", "edt"]}

        if "yac_perms" in json_schema:
            try:
                assert bool(json_schema["yac_perms"])
                assert isinstance(json_schema["yac_perms"], list)
                assert all(isinstance(s, str) for s in json_schema["yac_perms"])
            except AssertionError as error:
                raise SchemaSpecsError(
                    f"{loc}/yac_perms is not an array of strings"
                ) from error

            context["yac_perms"].update({loc: json_schema["yac_perms"]})
            json_schema.pop("yac_perms")

        perms_loc = locs.get_most_specific(loc, list(context["yac_perms"].keys()))

        if perms_loc is None:
            logger.warning(f"removed {loc} from schema due to undefined perms")
            return None, context

        if (
            len(
                set(props["old"]["perms"]).intersection(
                    set(context["yac_perms"][perms_loc])
                )
            )
            <= 0
        ):
            logger.info(
                f"removed {loc} from schema due to missing perms (requires one of: "
                f'{context["yac_perms"][perms_loc]})'
            )
            return None, context

        return json_schema, context


processor = YacPerms()
