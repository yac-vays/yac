# J2 Test Plugins

J2 test plugins are referenced by function name, all python-files are included.
They can be used in any jinja2 test-statement in YAC like
`data is my_test_function`. The first argument of the function is the data
that is being tested with this function.

The functions **should not** raise an exception. Any exception is considered a
config error in the specs.
