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

        Whether the injected `const` may be dropped depends on WHY the schema
        has no live subschema for the key (see consts.REMOVED):

        - not defined by the schema at all: this is exactly what the "cln"
          perm is for, so the const is optional for "cln" holders and
          required for everybody else;
        - defined, but removed by yac_perms / yac_editable: the const is
          always required. Property-level perms are write-side only — anyone
          who reaches the edit schema holds `see` and can read the whole
          entity (including the raw YAML) anyway, as there is no read
          protection below entity level — and the required `const` is what
          keeps such values present-but-immutable. "cln" does not override
          that: the key IS covered by the schema;
        - defined, but removed by yac_if: nothing is injected. The condition
          no longer holds, so the data has to go; removing it is an ordinary
          data change (needs no "cln"), enforced by the parent object's
          `additionalProperties: false` once removed_cleanup.py dropped the
          marker.

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
                    state = locs.specification(key, json_schema)
                    if state in (locs.DEFINED, "if"):
                        logger.debug(
                            f"Not adding data {data_loc}/{key} to schema"
                            f" {loc}/properties/{key} due to existing subschema"
                            f" ({state})"
                        )
                        continue

                    if "properties" not in json_schema:
                        json_schema["properties"] = {}
                    if state is None:
                        json_schema["properties"][key] = {
                            "const": data[key],
                            "yac_optional": "cln" in props["user"]["perms"],
                        }
                    else:
                        # removed by perms / editable: immutable, never optional
                        json_schema["properties"][key] = {"const": data[key]}
                    # for lib.schema to phrase the validation error of a removal
                    context.setdefault("add_consts_state", {})[
                        f"{loc}/properties/{key}"
                    ] = state or "unknown"

        return json_schema, context


processor = AddConsts()
