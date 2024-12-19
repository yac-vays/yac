# Section `schema`

YAC uses [JSON-Schema](https://json-schema.org) Draft-7 with custom extensions
to describe and validate data. From this schema, a `json_schema` and a
`ui_schema` are generated. VAYS then uses them to generate and validate the
forms.

The top-level schema has to be of type `object`, everything else is not
supported by YAC. And every object defaults to `additionalProperties: false`
(instead of `true`). Addititonally, it is recommended that you use
`yac_optional` on object properties instead of the `required` array on object
level.

All the keywords listed below can be used on any schema level if not stated
differently in the description.

## Extension Keywords

| Keyword                         | Schema Type    | Keyword Type    | Since |
|:--------------------------------|:---------------|:----------------|------:|
| `yac_changable`                 | `any`          | `boolean`       |    v0 |
| `yac_if`                        | `any`          | `string`        |    v0 |
| `yac_optional`                  | *some*         | `boolean`       |    v0 |
| `yac_perms`                     | `any`          | `array[string]` |    v0 |
| `yac_types`                     | `any`          | `array[string]` |    v0 |
| `vays_category`                 | *some*         | `string`        |    v0 |
| `vays_group`                    | *some*         | `string`        |    v0 |
| `vays_options`                  | *some*         | `object`        |    v0 |
| `vays_options.initial`          | `vays_options` | `any`           |    v0 |
| `vays_options.initial_editable` | `vays_options` | `boolean`       |    v0 |
| `vays_options.renderer`         | `vays_options` | `string`        |    v0 |
| `vays_options.renderer_options` | `vays_options` | `object`        |    v0 |
Added
### Keyword `yac_changable`

Only allow changing entity data specified by this subschema if `true`.

### Keyword `yac_if`

Only allow creating and changing entity data specified by this subschema if
`true`.

**But** this is not a boolean but a j2-string that has to render into a boolean.

### Keyword `yac_optional`

Can only be defined for schemas in object properties defined under the
properties schema-keyword! If not defined or `true`, this property will be
added to the required list of the object. Otherwise, it will be removed (if
there).

### Keyword `yac_perms`

*Permissions*, one of which is required to allow creating and changing entity
data specified by this subschema. This keyword is recursive and the top-level
default is `[add, edt]` (see [Permissions](../perms.md) for details)!

### Keyword `yac_types`

Only add this subschema for entities of the types defined in `yac_types`.

### Keyword `vays_category`

A category name for the VAYS form.

**Important**: This field is required to have the subschema represented in the
form. If it's not present, the subschema will not be represented in the form at
all.

It can only be defined on subschemas where all parents are objects (or in other
words: not inside of arrays, ifs, oneOf/allOf/anyOf, etc.). See the examples
(TODO) to have a list of all possible subschemas with a meaningful form renderer
in VAYS. (TODO test if this is true or ifs/oneOf/etc. would even work!)

### Keyword `vays_group`

An optional group name for grouping multiple input fields inside a VAYS form
category. This can only be used on subschemas with `vays_category` defined (so
the same restrictions apply automatically).

### Keyword `vays_options`

Additional options for the VAYS forms. This can only be used on subschemas with
`vays_category` defined **or** on object properties within an array that has
`vays_category` defined (TODO). (*Note* that this is not stackable, so it will
only work for an array of objects, **not** for an array of objects with an array
of objects and so on.)

### Keyword `vays_options.initial`

This value will be used as an initial value in the VAYS input field but will
not land in the data (and thus the YAML file in the end) unless the user
interacts with it.

### Keyword `vays_options.initial_editable`

The `vays_options.initial` value will be used as data in the VAYS input field
instead of a placeholder. (So if it is `false` for a text input, as soon as
the user starts typing, the initial value will disappear. Otherwise (`true`),
the user would edit the `initial` value when typing instead of replacing it.)

### Keyword `vays_options.renderer`

Use a custom form renderer for this input field.

TODO link to custom renderer docs in VAYS repo

### Keyword `vays_options.renderer_options`

Specific options for the `vays_options.renderer`.

## Official Keywords With Modified, Limited Or Extended Behaviour

| Keyword                | Schema Type | Keyword Type | Since |   Draft |
|:-----------------------|:------------|:-------------|------:|--------:|
| `title`                | `any`       | `string`     |    v0 |       1 |
| `description`          | `any`       | `string`     |    v0 |       1 |
| `default`              | `any`       | `any`        |    v0 |       1 |
| `if`                   | `any`       | `schema`     |    v0 |       7 |
| `else`                 | `any`       | `schema`     |    v0 |       7 |
| `then`                 | `any`       | `schema`     |    v0 |       7 |
| `not`                  | `any`       | `schema`     |    v0 |       4 |
| `format`               | `string`    | `string`     |    v0 |       1 |

### Keyword `title`

Will used for VAYS forms and supports markdown formatting.

### Keyword `description`

Will used for VAYS forms and supports markdown formatting.

### Keyword `default`

The value of `default` will always be present in the data (and thus the YAML
file in the end) unless the user changes (or purposely deletes) it.

### Keywords `if`, `else`, `then`

VAYS only supports the `if`-construct in some scenarios.

The following things are known to work:

  - Changing value ranges (e.g. regex or enum values) conditionally

The following things are known to **not** work:

  - Adding/removing object properties conditionally
  - Changing default values conditionally

### Keyword `not`

YAC only supports the `not` keyword below the `if` keyword. Using `not` outside
of that context may lead to strange behaviour.

### Keyword `format`

The following official formats are supported by YAC:

| Format                  | Since |   Draft |
|:------------------------|------:|--------:|
| `date-time`             |    v0 |       1 |
| `email`                 |    v0 |       1 |
| `hostname`              |    v0 |       1 |
| `ipv4`                  |    v0 |       1 |
| `ipv6`                  |    v0 |       1 |
| `uri`                   |    v0 |       1 |
| `date`                  |    v0 |       7 |
| `regex`                 |    v0 |       7 |
| `time`                  |    v0 |       7 |

The following official formats are **not** supported:

| Format                  |   Draft |
|:------------------------|--------:|
| `json-pointer`          |       6 |
| `uri-reference`         |       6 |
| `uri-template`          |       6 |
| `idn-email`             |       7 |
| `idn-hostname`          |       7 |
| `iri-reference`         |       7 |
| `iri`                   |       7 |
| `relative-json-pointer` |       7 |
| `duration`              | 2019-09 |
| `uuid`                  | 2019-09 |

But you are free to add custom formats by adding a function with the name
of the format to `plugin/json_schema_format/*.py`.

*Note* that formats are only validated on the server side, so the user
might not get immediate feedback when using VAYS.

## Official Keywords (Supported)

Use the following link for a more detailed description/reference of the keywords below:
https://www.learnjsonschema.com/2020-12/

| Keyword                | Schema Type | Keyword Type             |   Draft | Details |
|:-----------------------|:------------|:-------------------------|--------:|:--------|
| `type`                 | `any`       | `string\|array[string]`  |       1 | Options: `null` `boolean` `object` `array` `number` `integer` `string` |
| `enum`                 | `any`       | `array[any]`             |       1 ||
| `const`                | `any`       | `any`                    |       6 ||
| `oneOf`                | `any`       | `array[schema]`          |       4 ||
| `allOf`                | `any`       | `array[schema]`          |       4 ||
| `anyOf`                | `any`       | `array[schema]`          |       4 ||
| `properties`           | `object`    | `object[string,schema]`  |       1 ||
| `additionalProperties` | `object`    | `boolean`                |       1 ||
| `required`             | `object`    | `array[string]`          |       3 ||
| `items`                | `array`     | `schema`                 |       1 ||
| `maxItems`             | `array`     | `integer`                |       1 ||
| `minItems`             | `array`     | `integer`                |       1 ||
| `uniqueItems`          | `array`     | `boolean`                |       2 ||
| `minLength`            | `string`    | `integer`                |       1 ||
| `maxLength`            | `string`    | `integer`                |       1 ||
| `pattern`              | `string`    | `string`                 |       1 ||
| `maximum`              | `number`    | `number`                 |       1 ||
| `minimum`              | `number`    | `number`                 |       1 ||
| `exclusiveMaximum`     | `number`    | `number`                 |       3 ||
| `exclusiveMinimum`     | `number`    | `number`                 |       3 ||
| `multipleOf`           | `number`    | `number`                 |       4 ||

## Official Not Supported Keywords

The following keywords are **NOT SUPPORTED** by YAC and VAYS!

| Keyword                 | Schema Type | Keyword Type                   |   Draft |
|:------------------------|:------------|:-------------------------------|--------:|
| `$schema`               | `any`       | `string`                       |       3 |
| `$id`                   | `any`       | `string`                       |       6 |
| `$ref`                  | `any`       | `string`                       |       3 |
| `$defs`                 | `any`       | `object[string,schema]`        | 2019-09 |
| `$comment`              | `any`       | `string`                       |       7 |
| `$dynamicAnchor`        | `any`       | `string`                       | 2020-12 |
| `$dynamicRef`           | `any`       | `string`                       | 2020-12 |
| `$anchor`               | `any`       | `string`                       | 2019-09 |
| `$vocabulary`           | `any`       | `object[string,boolean]`       | 2019-09 |
| `unevaluatedProperties` | `object`    | `boolean`                      | 2019-09 |
| `patternProperties`     | `object`    | `object[string,schema]`        |       3 |
| `propertyNames`         | `object`    | `schema`                       |       6 |
| `dependentRequired`     | `object`    | `object[string,array[string]]` | 2019-09 |
| `dependentSchemas`      | `object`    | `object[string,schema]`        | 2019-09 |
| `minProperties`         | `object`    | `integer`                      |       4 |
| `maxProperties`         | `object`    | `integer`                      |       4 |
| `contains`              | `array`     | `schema`                       |       6 |
| `prefixItems`           | `array`     | `array[schema]`                | 2020-12 |
| `unevaluatedItems`      | `array`     | `boolean`                      | 2019-09 |
| `maxContains`           | `array`     | `integer`                      | 2019-09 |
| `minContains`           | `array`     | `integer`                      | 2019-09 |
| `contentEncoding`       | `string`    | `string`                       |       7 |
| `contentMediaType`      | `string`    | `string`                       |       7 |
| `contentSchema`         | `string`    | `schema`                       | 2019-09 |
