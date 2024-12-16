# Section `schema`

YAC uses [JSON-Schema](json-schema.org) Draft-7 with custom extensions
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

| Keyword                         | Schema Type    | Keyword Type    |
|:--------------------------------|:---------------|:----------------|
| `yac_changable`                 | `any`          | `boolean`       |
| `yac_if`                        | `any`          | `string`        |
| `yac_optional`                  | *some*         | `boolean`       |
| `yac_perms`                     | `any`          | `array[string]` |
| `yac_types`                     | `any`          | `array[string]` |
| `vays_category`                 | *some*         | `string`        |
| `vays_group`                    | *some*         | `string`        |
| `vays_options`                  | *some*         | `object`        |
| `vays_options.initial`          | `vays_options` | `any`           |
| `vays_options.initial_editable` | `vays_options` | `boolean`       |
| `vays_options.renderer`         | `vays_options` | `string`        |
| `vays_options.renderer_options` | `vays_options` | `object`        |

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

## Official Keywords With Modified Or Extended Behaviour

| Keyword                | Schema Type | Keyword Type |
|:-----------------------|:------------|:-------------|
| `title`                | `any`       | `string`     |
| `description`          | `any`       | `string`     |
| `default`              | `any`       | `any`        |
| `not`                  | `any`       | `schema`     |
| `format`               | `string`    | `string`     | 
### Keyword `title`

Will used for VAYS forms and supports markdown formatting.

### Keyword `description`

Will used for VAYS forms and supports markdown formatting.

### Keyword `default`

The value of `default` will always be present in the data (and thus the YAML
file in the end) unless the user changes (or purposely deletes) it.

### Keyword `not`

YAC only supports the `not` keyword below the `if` keyword. Using `not` outside
of that context may lead to strange behaviour.

### Keyword `format`

The following formats are supported by default (TODO):

`date-time`, `date`, `time`, `duration`, `email`, `idn-email`, `hostname`,
`idn-hostname`, `ipv4`, `ipv6`, `uri`, `uri-reference`, `iri`, `iri-reference`,
`uuid`, `uri-template`, `json-pointer`, `relative-json-pointer`, `regex`

But you are free to add custom formats by adding a function with the name
of the format to `plugin/json_schema_format/*.py`.

*Note* that custom formats are only validated on the server side, so the user
might not get immediate feedback when using the VAYS form.

## Official Keywords (Supported)

Use the following link for a more detailed description/reference of the keywords below:
https://www.learnjsonschema.com/2020-12/

| Keyword                | Schema Type | Keyword Type             | Details |
|:-----------------------|:------------|:-------------------------|:--------|
| `type`                 | `any`       | `string\|array[string]`  | Options: `null` `boolean` `object` `array` `number` `integer` `string` |
| `enum`                 | `any`       | `array[any]`             ||
| `const`                | `any`       | `any`                    ||
| `oneOf`                | `any`       | `array[schema]`          ||
| `allOf`                | `any`       | `array[schema]`          ||
| `anyOf`                | `any`       | `array[schema]`          ||
| `if`                   | `any`       | `schema`                 ||
| `else`                 | `any`       | `schema`                 ||
| `then`                 | `any`       | `schema`                 ||
| `properties`           | `object`    | `object[string,schema]`  ||
| `additionalProperties` | `object`    | `boolean`                ||
| `required`             | `object`    | `array[string]`          ||
| `items`                | `array`     | `schema`                 ||
| `maxItems`             | `array`     | `integer`                ||
| `minItems`             | `array`     | `integer`                ||
| `uniqueItems`          | `array`     | `boolean`                ||
| `minLength`            | `string`    | `integer`                ||
| `maxLength`            | `string`    | `integer`                ||
| `pattern`              | `string`    | `string`                 ||
| `maximum`              | `number`    | `number`                 ||
| `minimum`              | `number`    | `number`                 ||
| `exclusiveMaximum`     | `number`    | `number`                 ||
| `exclusiveMaximums`    | `number`    | `number`                 ||
| `multipleOf`           | `number`    | `number`                 ||

## Official Not Supported Keywords

The following keywords are **NOT SUPPORTED** by YAC and VAYS!

| Keyword                 | Schema Type | Keyword Type                   |
|:------------------------|:------------|:-------------------------------|
| `$schema`               | `any`       | `string`                       |
| `$id`                   | `any`       | `string`                       |
| `$ref`                  | `any`       | `string`                       |
| `$defs`                 | `any`       | `object[string,schema]`        |
| `$comment`              | `any`       | `string`                       |
| `$dynamicAnchor`        | `any`       | `string`                       |
| `$dynamicRef`           | `any`       | `string`                       |
| `$anchor`               | `any`       | `string`                       |
| `$vocabulary`           | `any`       | `object[string,boolean]`       |
| `unevaluatedProperties` | `object`    | `boolean`                       |
| `patternProperties`     | `object`    | `object[string,schema]`        |
| `propertyNames`         | `object`    | `schema`                       |
| `dependentRequired`     | `object`    | `object[string,array[string]]` |
| `dependentSchemas`      | `object`    | `object[string,schema]`        |
| `minProperties`         | `object`    | `integer`                      |
| `maxProperties`         | `object`    | `integer`                      |
| `contains`              | `array`     | `schema`                       |
| `prefixItems`           | `array`     | `array[schema]`                |
| `unevaluatedItems`      | `array`     | `boolean`                       |
| `maxContains`           | `array`     | `integer`                      |
| `minContains`           | `array`     | `integer`                      |
| `contentEncoding`       | `string`    | `string`                       |
| `contentMediaType`      | `string`    | `string`                       |
| `contentSchema`         | `string`    | `schema`                       |
