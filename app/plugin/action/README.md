# Action Plugins

Action plugins are referenced by filename.

Each plugin must implement the following function:

    async def run(*, details: dict, props: dict) -> None

With:

    details # plugin-specific configuration passed through from the specs
    props # context vars according to ../../../docs/specs/general.md

The plugin **must** only raise one of the following exceptions:

    app.model.err.ActionClientError # with message for the user
    app.model.err.ActionError       # irrelevant for user, only log
    app.model.err.ActionSpecsError  # config error in the specs
