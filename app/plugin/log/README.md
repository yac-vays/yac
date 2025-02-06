# Log Plugins

Log plugins are referenced by filename in the specs.

Each plugin must implement the the `app.model.plg.ILog` class and provide an
instance of that class as `log` variable.

It may raise the following exceptions:

    app.model.err.LogError      # skip log type for this entity
    app.model.err.LogSpecsError # config error in the specs
