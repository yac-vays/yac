# Schema Format Plugins

Schema format plugins are referenced by function name, all python-files are
included. They can be used in the schema specs under the `format` keyword to
run further validation on the specified string.

The only argument of the function is the data to be validated. The function
must return a boolean (if the data is valid or not).

It may not raise any exceptions.