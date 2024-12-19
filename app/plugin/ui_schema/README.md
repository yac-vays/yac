# UI Schema Plugins

UI schema plugins are all processed according to their order. They are used to
assemble the ui_schema while iterating over the json_schema. In order to remove
custom schema extensions, they can also modify the json_schema. YAC iterates
over the json_schema and runs each plugin on every level of subschemas!

Each plugin must implement the the `app.model.plg.IUiSchema` class and provide an
instance of that class as `processor` variable.

The UI schema plugins all run after all JSON schema plugins (of the same
category).

It may raise the following exceptions:

    app.model.err.SchemaSpecsError # config error in the specs
