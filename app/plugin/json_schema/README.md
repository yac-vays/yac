# JSON Schema Plugins

JSON schema plugins are all processed according to their order. They are used to
modify the schema according to custom schema extensions or standards. YAC
iterates over the schema and runs each plugin on every level of subschemas!

Each plugin must implement the the `app.model.plg.IJsonSchema` class and provide an
instance of that class as `processor` variable.

It may raise the following exceptions:

    app.model.err.SchemaSpecsError # config error in the specs
