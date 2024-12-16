# Section: `sets`

The sets that are referenced in your role definitions need to be defined in
this section. (That's also the only purpose of this section.) They are
evaluated at runtime for each entity as well.

## Defaults

    *: {} # the key is a defined type from above
      {name}: '' # each list element is a dict where the key is a list of perms
                   # (separated by +) and the value is a jinja2 bool

## Examples

    sets:
      animal:
        zooanimals: "name in ['zebra', 'elephant', 'lion']"
        wildanimals: "name not in ['zebra', 'elephant', 'lion']"
