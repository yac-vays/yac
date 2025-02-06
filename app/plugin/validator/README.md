# Validator Plugins

Validator plugins are all processed according to their order. They are used to
validate a request before the requested operation may happen.

Each plugin must implement the the `app.model.plg.IValidator` class and provide an
instance of that class as `tester` variable.

It may raise the following exceptions:

    app.model.err.RequestError # the request is not valid
    app.model.err.RequestConflict # the data has changed in the meantime
    app.model.err.RequestForbidden # the request is not permitted
    app.model.err.RequestNotFound # the requested entity does not exist
    app.model.err.ServerError # irrelevant for user, only log
