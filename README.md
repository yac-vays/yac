# YAC Backend

**YAC** (**Y**et **A**nother **C**onfigurator) manages many similar YAML files
in a GIT repository, but adds a layer of access control (inside the YAML data
structure) and allows to hook actions into the process.

It is a RESTful cloud-native web service that is configured through a Jinja2-
extensible YAML file. It uses OpenID Connect for user authentication, and an
extended JSON-Schema syntax to specify the YAML data stuctures and user access
control.

For the documentation, see: https://yac-vays.github.io
