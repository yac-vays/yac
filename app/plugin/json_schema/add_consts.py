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

        This deliberately includes data whose subschema was removed by
        yac_perms.py: property-level perms are write-side only — anyone who
        reaches the edit schema holds `see` and can read the whole entity
        (including the raw YAML) anyway, as there is no read protection below
        entity level. The injected `const` is also what keeps such values
        present-but-immutable for users lacking the guarding perm.

        Read operations get the same consts: the display schema then covers
        the whole stored document, so stored keys without a subschema neither
        fail the read validation on `additionalProperties: false` nor show up
        as unknown properties in a schema-aware YAML viewer.
        """
        if props["operation"] not in ("edit", "read"):
            return json_schema, context

        if json_schema.get("type", "") != "object":
            return json_schema, context

        if "add_consts" not in context:
            context["add_consts"] = locs.get(
                props["old"]["data"], lambda d: isinstance(d, dict)
            )

        for data_loc in locs.reduce(loc, context["add_consts"], recursive=False):
            data = locs.extract(data_loc, props["old"]["data"])
            if isinstance(data, dict):
                for key in data.keys():
                    if locs.is_specified(key, json_schema):
                        logger.debug(
                            f"Not adding data {data_loc}/{key} to schema {loc}/properties/{key} "
                            "due to existing subschema"
                        )
                    else:
                        if "properties" not in json_schema:
                            json_schema["properties"] = {}
                        json_schema["properties"][key] = {
                            "const": data[key],
                            "yac_optional": "cln" in props["user"]["perms"],
                        }

        return json_schema, context


processor = AddConsts()
