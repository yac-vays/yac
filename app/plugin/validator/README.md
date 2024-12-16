# Validator Plugins

Validator plugins are all processed according to their order. They are used to
validate a request before the operation may happen.

Each plugin must implement the following functions:

    def order() -> tuple[bool, int] # late, order
    def test(op: app.model.inp.OperationRequest, spec: app.model.spc.Specs, old: app.model.rpo.Entity, new: app.model.rpo.Entity) -> None

With:

    late # if the plugin should run for all operations (false) or for all except
           listing entities (true)
    order # number to be sorted after, lower numbers run earlier
    op # the operation requested to be executed
    spec # the parsed specs
    old # the old entity to operate on (if any)
    new # the new entity to operate on (if any)

The plugin **must** only raise the following exception:

    app.model.err.RequestError # the request is not valid
    app.model.err.RequestConflict # the data has changed in the meantime
    app.model.err.RequestForbidden # the request is not permitted
    app.model.err.RequestNotFound # the requested entity does not exist
    app.model.err.ServerError # irrelevant for user, only log
