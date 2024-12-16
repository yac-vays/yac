# JSON Schema Plugins

JSON schema plugins are all processed according to their order. They are used to
modify the schema according to custom schema extensions or standards. YAC
iterates over the schema and runs each plugin on every level of subschemas!

Each plugin must implement the following functions:

    def order() -> tuple[bool, int] # late, order
    def process(loc: str, schema: dict, context: dict, props: dict) -> type[dict|bool|None, dict] # schema, context

With:

    late # if the plugin should run post order (when walking back up the schema tree)
    order # number to be sorted after, lower numbers run earlier
    loc # the current location in the whole json_schema (like '#/properties/blub')
    schema # the current subschema we are working on
    context # a freely usable context variable passed among all plugins
    props # context vars according to ../../../docs/specs/general.md

If the returned `schema` is `None`, the whole subschema will be removed from
the parent schema.

The plugin **must** only raise the following exception:

    app.model.err.SchemaSpecsError # config error in the specs
