# J2 Test Plugins

J2 test plugins are referenced by function name, all python-files are included.
They can be used in any jinja2 test-statement in YAC like
`data is my_test_function`. The first argument of the function is the data
that is being tested with this function.

The functions may raise `app.model.err.RequestError` exceptions which will be
shown to the user. All other exceptions should be avoided and will be
interpreted as specs errors.