# Repo Plugins

The repo plugin is referenced by filename in the specs file's `repo.plugin`
field (see https://yac-vays.github.io/yac/specs/file/repo.html). Connection config goes into
`repo.connection` and is read once at process startup.

Each plugin must implement the `app.model.plg.IRepo` class (with its
`IRepoSession` / `IRepoUntyped` scopes and `state()`) and provide an
instance of that class as `handler` variable.

`IRepoSession` has defaults for `get_resolved`, `get_all` and
`is_link_target` that are built on the per-entity reads. They are correct
for any backend, but `get_all` in particular (the whole type at once, used by
the entity list and the limits) is worth overriding if the backend can read
everything in one go.

It may raise the following exceptions:

    app.model.err.RepoError # irrelevant for user, only log
    app.model.err.RepoTimeoutError # a git operation timed out
    app.model.err.RepoUnavailable # repo not reachable (503, stale reads)
    app.model.err.RepoMaintenance # repo in planned maintenance (503)
    app.model.err.RepoConflict # data has changed in the meantime
    app.model.err.RepoNotFound # referenced entity not found
    app.model.err.RepoClientError # other errors with message for the user
    app.model.err.RepoSpecsError # config error in the specs
