import logging

from app.lib import locs
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


class AddConsts(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        # Must run before yac_optional.py to to have it added to the required list
        return True, 90

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        Adds existing data to the schema as consts if not defined in the schema.

        This will only add data on the object property level, so lists are either
        considered as defined or they are added as a single constant.
        """
        if props["operation"] != "edit":
            return json_schema, context

        if json_schema.get("type", "") != "object":
            return json_schema, context

        if "add_consts" not in context:
            context["add_consts"] = locs.get(
                props["old"]["data"], lambda d: isinstance(d, dict)
            )

        # Data paths whose subschema was removed by yac_perms.py due to
        # missing permissions must not be re-injected as consts — that would
        # leak values the user has no permission to read. Instead, a permissive
        # stub is injected so the mere *presence* of the stored key keeps
        # validating under `additionalProperties: false`. Immutability of the
        # stored value is enforced outside the schema (see lib/schema.py).
        removed = context.get("yac_perms_removed", [])

        for data_loc in locs.reduce(loc, context["add_consts"], recursive=False):
            data = locs.extract(data_loc, props["old"]["data"])
            if isinstance(data, dict):
                for key in data.keys():
                    if locs.is_specified(key, json_schema):
                        logger.debug(
                            f"Not adding data {data_loc}/{key} to schema {loc}/properties/{key} "
                            "due to existing subschema"
                        )
                    elif any(r.match(f"{data_loc}/{key}") for r in removed):
                        logger.debug(
                            f"Adding permissive stub (instead of data) for "
                            f"{data_loc}/{key} at {loc}/properties/{key} due to "
                            "missing permission"
                        )
                        if "properties" not in json_schema:
                            json_schema["properties"] = {}
                        json_schema["properties"][key] = {
                            "description": "Hidden (insufficient permissions)",
                            "yac_optional": True,
                        }
                    else:
                        if "properties" not in json_schema:
                            json_schema["properties"] = {}
                        json_schema["properties"][key] = {
                            "const": data[key],
                            "yac_optional": "cln" in props["user"]["perms"],
                        }

        return json_schema, context


processor = AddConsts()
