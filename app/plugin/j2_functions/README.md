# J2 Function Plugins

J2 function plugins are referenced by function name, all python-files are
included. They can be used in any jinja2-statement in YAC like
`my_function(arg1, arg2)`.

The functions may raise `app.model.err.RequestError` exceptions which will be
shown to the user. All other exceptions should be avoided and will be
interpreted as specs errors.