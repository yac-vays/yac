# Repo Plugins

The repo plugin is referenced by filename in the specs file's `repo.plugin`
field (see `docs/yac/specs/file/repo.md`). Connection config goes into
`repo.connection` and is read once at process startup.

Each plugin must implement the the `app.model.plg.IRepo` class and provide an
instance of that class as `handler` variable.

It may raise the following exceptions:

    app.model.err.RepoError # irrelevant for user, only log
    app.model.err.RepoConflict # data has changed in the meantime
    app.model.err.RepoForbidden # operation not permitted
    app.model.err.RepoNotFound # referenced entity not found
    app.model.err.RepoClientError # other errors with message for the user
    app.model.err.RepoSpecsError # config error in the specs
