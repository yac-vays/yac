# Schema Format Plugins

Schema format plugins are referenced by function name, all python-files are
included. They are used in the schema specs under the `format` keyword to
run further validation on the specified data.

The only argument of the function is the data to be validated. The function
must return a boolean (if the data is valid or not).

The functions **must not** raise any exception.
