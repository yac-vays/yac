# Log Plugins

Log plugins are referenced by filename.

Each plugin must implement the following function:

    async get(log_name: str, problem: bool, progress: bool, *, details: dict, props: dict) -> list[app.model.out.Log]

With:

    log_name # name of the log type
    problem # if the log type indicates problems
    progress # if the log type indicates progress
    details # plugin-specific configuration passed through from the specs
    props # context vars according to ../../../docs/specs/general.md

The plugin **must** only raise one of the following exceptions:

    app.model.err.LogError      # skip log type for this entity
    app.model.err.LogSpecsError # config error in the specs
