import logging

from app import consts
from app.lib import locs
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


def _stored_values(loc: str, data_locs: list[str], old_data: dict) -> dict[str, list]:
    """Key -> stored values (one per object at loc holding it), in data order."""
    values: dict[str, list] = {}
    for data_loc in locs.reduce(loc, data_locs, recursive=False):
        data = locs.extract(data_loc, old_data)
        if isinstance(data, dict):
            for key, value in data.items():
                values.setdefault(key, []).append(value)
    return values


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

        A *shared* subschema (see locs.shared, e.g. the items of an array)
        covers many objects with possibly different values and no identity
        (items can be added, removed and reordered). There the key gets an
        optional `enum` of all stored values instead of a const, and
        lib.schema checks that no value is used more often than before (see
        `add_consts_shared` in the context): the values can be kept, moved
        between the objects or removed (with or without their object), but
        neither invented nor duplicated.

        Like the injected consts elsewhere (which have no `vays_category`),
        these are not part of the form: marked with consts.INJECTED, the
        ui_schema plugins leave them out of the array's rows.
        """
        if props["operation"] not in ("edit", "read"):
            return json_schema, context

        if json_schema.get("type", "") != "object":
            return json_schema, context

        if "add_consts" not in context:
            context["add_consts"] = locs.get(
                props["old"]["data"], lambda d: isinstance(d, dict)
            )

        shared = locs.shared(loc)
        values = _stored_values(loc, context["add_consts"], props["old"]["data"])
        for key, stored in values.items():
            state = locs.specification(key, json_schema)
            if state in (locs.DEFINED, "if"):
                logger.debug(
                    f"Not adding data at {loc}/properties/{key} to schema due to"
                    f" existing subschema ({state})"
                )
                continue

            json_schema.setdefault("properties", {})
            if shared:
                unique = {locs.value_key(v): v for v in stored}
                sub: dict = {
                    "enum": list(unique.values()),
                    "yac_optional": True,
                    consts.INJECTED: True,
                }
                context.setdefault("add_consts_shared", {})[
                    f"{loc}/properties/{key}"
                ] = stored
            elif state is None:
                sub = {"const": stored[0], "yac_optional": "cln" in props["user"]["perms"]}
            else:
                # removed by perms / editable: immutable, never optional
                sub = {"const": stored[0]}
            json_schema["properties"][key] = sub
            # for lib.schema to phrase the validation error of a removal
            context.setdefault("add_consts_state", {})[
                f"{loc}/properties/{key}"
            ] = state or "unknown"

        return json_schema, context


processor = AddConsts()
