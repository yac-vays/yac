# UI Schema Plugins

UI schema plugins are all processed according to their order. They are used to
assemble the ui_schema while iterating over the json_schema. In order to remove
custom schema extensions, they can also modify the json_schema. YAC iterates
over the json_schema and runs each plugin on every level of subschemas!

The UI schema plugins all run after all JSON schema plugins (of the same
category, see `late` below).

Each plugin must implement the following functions:

    def order() -> tuple[bool, int] # late, order
    def process(loc: str, json_schema: dict, ui_schema: dict, props: dict) -> tuple[dict|bool|None, dict] # json_schema, ui_schema

With:

    late # if the plugin should run post order (when walking back up the schema tree)
    order # number to be sorted after, lower numbers run earlier
    loc # the current location in the whole json_schema (like '#/properties/blub')
    json_schema # the current subschema we are working on
    ui_schema # the complete ui_schema that has been assembled so far
    props # context vars according to ../../../docs/specs/general.md

If the returned `json_schema` is `None`, the whole subschema will be removed
from the parent schema.

The plugin **must** only raise the following exception:

    app.model.err.SchemaSpecsError # config error in the specs
