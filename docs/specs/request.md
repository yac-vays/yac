# Section `request`

In the request section, you can define custom header fields, a regular
expression for validation and a default value.

Headers are case-insensitive and `-` is converted to `_`. And they need to have
the prefix `YAC-` (or `yac-`), so for example the HTTP header field
`YAC-My-Super-Secret` can be defined as `my_super_secret` and then accessed as
`request.headers.my_super_secret` variable.

## Defaults

    headers: {}
      *:
        pattern: '^$'
        default: ''

## Example

    request:
      headers:
        my_super_secret:
          pattern: '^[0-9a-f]{3,12}$'
          default: '123'
