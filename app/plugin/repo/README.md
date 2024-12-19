# Repo Plugins

The repo plugin is referenced by filename in the env variable YAC_REPO_PLUGIN.

Each plugin must implement the the `app.model.plg.IRepo` class and provide an
instance of that class as `handler` variable.

It may raise the following exceptions:

    app.model.err.RepoError # irrelevant for user, only log
    app.model.err.RepoConflict # data has changed in the meantime
    app.model.err.RepoForbidden # operation not permitted
    app.model.err.RepoNotFound # referenced entity not found
    app.model.err.RepoClientError # other errors with message for the user
    app.model.err.RepoSpecsError # config error in the specs
