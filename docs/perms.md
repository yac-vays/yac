# Permissions

Permissions are applied to a user at at run-time based on
[roles](./specs/roles.md) and [sets](./specs/sets.md) according to the
definitions in the specs.

The following *permissions* are reserved and have a statically implemented
meaning, but you are free to define and use additional ones in the specs
(define in `roles`, use in `types[].actions` and `schema`):

| Perm  | Includes | Description |
|:-----:|:--------:|:------------|
| `see` |          | Allow to read the whole YAML and the logs for the entity. |
| `add` |          | Allow to create the entity (may be restricted in the schema). |
| `rnm` | `see`    | Allow to rename the entity without a revalidation of the YAML, requires the `add` permission for the new entity name. |
| `cpy` | `see`    | Allow to copy the entity without a revalidation of the YAML, requires the `add` permission for the new entity name. |
| `lnk` | `see`    | Allow to link to the entity without a revalidation of the YAML, requires the `add` permission for the new entity name. |
| `edt` | `see`    | Allow to edit the entity data (may be restricted in the schema). |
| `cln` | `see`    | Allow to delete any[^1] data from the YAML of the entity and to modify the non-data structure[^2] of the YAML. |
| `del` |          | Allow to delete the entity. |
| `act` |          | Allow to execute[^3] actions (may be overwritten in de specs per action). |
| `all` | `see` + `add` + `rnm` + `cpy` + `lnk` + `edt` + `cln` + `del` + `act` | `all` itself cannot be used in the schema directly! |
| `adm` | `all`    | Allow to freely edit the entity data without validation **this is not implemented yet and will only be on real demand** |

[^1]: All data that is not covered from the schema definition. But if subschemas
      are removed due to missing perms or if-conditions, this also applies!
[^2]: Comments, syntax (like whether a value is split over multiple lines or
      not), spacing and the order of object keys, but not the order of list
      elements (because this is part of the data and covered via schema if it
      is allowed to change it or not).
[^3]: Only applies if the action execution is non-forced or arbitrary. (Actions
      that are hooked to operations and have the `force` flag will always run,
      regardless of the permissions.)

## Inheritance

Permissions, if not defined explicitly, are inherited from the parent schema to
the children. (so if defined explicitly, no permissions will be inherited from
the parent, including `edt` from top-level).
