# Schema Format Plugins

Schema format plugins are referenced by function name, all python-files are
included. They can be used in the schema specs under the `format` keyword to
run further validation on the specified string.

The only argument of the function is the data to be validated. The function
must return a boolean (if the data is valid or not).

The function may also be `async` (e.g. for a lookup in an external service).
All values of async formats are checked concurrently before the validation, each
distinct value once per validation. Cache the results if the check is expensive,
as YAC validates on every change in VAYS.

It may not raise any exceptions.
