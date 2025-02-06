# Format Plugins

The format plugin is referenced by filename in the env variable YAC_FORMAT_PLUGIN. It is used to format the YAC stdout log messages.

Each plugin must implement the the `logging.Formatter` class and provide an
instance of that class as `formatter` variable.

It may raise no exceptions!
