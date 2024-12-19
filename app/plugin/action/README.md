# Action Plugins

Action plugins are referenced by filename in the specs.

Each plugin must implement the the `app.model.plg.IAction` class and provide an
instance of that class as `action` variable.

It may raise the following exceptions:

    app.model.err.ActionClientError # with message for the user
    app.model.err.ActionError       # irrelevant for user, only log
    app.model.err.ActionSpecsError  # config error in the specs
