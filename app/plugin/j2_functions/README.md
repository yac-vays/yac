# J2 Function Plugins

J2 function plugins are referenced by function name, all python-files are
included. They can be used in any jinja2-statement in YAC like
`my_function(arg1, arg2)`.

The functions **should not** raise an exception. Any exception is considered a
config error in the specs.
