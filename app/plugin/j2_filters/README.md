# J2 Filter Plugins

J2 filter plugins are referenced by function name, all python-files are
included. They can be used in any jinja2-statement in YAC like
`data | my_filter_function`. The first argument of the function is the data
that is being piped to the filter.

The functions may raise `app.model.err.RequestError` exceptions which will be
shown to the user. All other exceptions should be avoided and will be
interpreted as specs errors.
