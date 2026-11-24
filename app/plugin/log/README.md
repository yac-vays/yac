# Log Plugins

Log plugins are referenced by filename in the specs.

Each plugin must implement the `app.model.plg.ILog` class and provide an
instance of that class as `log` variable:

  - `get(facility, *, details, props)` returns the entries of the log
    (`app.model.out.Log`). It is polled by the UI, so keep it cheap. Set
    `problem` / `progress` of an entry if the details define them (any
    renderer may show them), and `has_content` if `content` can return a
    document for the log.
  - `content(facility, *, details, props)` returns the content document of the
    log (`app.model.out.LogContent`), fetched only when it is opened in the UI.
    Optional: the default raises `LogContentNotFound`.

They may raise the following exceptions:

    app.model.err.LogError            # skip log type for this entity (get) /
                                      # content could not be fetched (content)
    app.model.err.LogContentNotFound  # there is no content (content)
    app.model.err.LogSpecsError       # config error in the specs (get: the
                                      # log is skipped as well)

Which UI renderer shows a log (`renderer` in the specs) is independent of the
plugin; see the VAYS docs for the renderers.
