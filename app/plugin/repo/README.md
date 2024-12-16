# Repo Plugins

TODO

The plugin **must** only raise one of the following exceptions:

    app.model.err.RepoError # irrelevant for user, only log
    app.model.err.RepoConflict # data has changed in the meantime
    app.model.err.RepoForbidden # operation not permitted
    app.model.err.RepoNotFound # referenced entity not found
    app.model.err.RepoClientError # other errors with message for the user
    app.model.err.RepoSpecsError # config error in the specs
