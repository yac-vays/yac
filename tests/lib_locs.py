from app.lib import locs

def test():
    assert not locs.is_specified('any-key', {})
    assert not locs.is_specified('any-other-key', True)
    assert locs.is_specified('a-key', {'type': 'object', 'properties': {'a-key': True}})
    assert locs.is_specified('a-key', {'type': 'object', 'properties': {'a-key': False}})
    assert not locs.is_specified('2nd-key', {'type': 'object', 'properties': {'a-key': True}})
    assert not locs.is_specified('2nd-key', {'type': 'object', 'properties': {}, 'additionalProperties': True})
    assert locs.is_specified('mykey', {'anyOf': [{}, {'type': 'object', 'if': True, 'then': {'properties': {'mykey': False}}}]})

    assert locs.get_most_specific('#/a/b',         ['#', '#/a/b/c', '#/c/a', '#/a/b/d']) == '#'
    assert locs.get_most_specific('#/a/c',         ['#', '#/a/b/c', '#/c/a', '#/a/b/d']) == '#'
    assert locs.get_most_specific('#/a/b/c',       ['#', '#/a/b/c', '#/c/a', '#/a/b/d']) == '#/a/b/c'
    assert locs.get_most_specific('#/a/b/d/f/g/h', ['#', '#/a/b/c', '#/c/a', '#/a/b/d']) == '#/a/b/d'
    assert locs.get_most_specific('a/#',           ['#', '#/a/b/c', '#/c/a', '#/a/b/d']) == None
    assert locs.get_most_specific('#/abc/zzzzzzz', ['#', '#/abc/aa/bb']) == '#'
    assert locs.get_most_specific('#/abc/ab',      ['#', '#/abc/aa/bb', '#/abc', '#/abc/abc']) == '#/abc'


# ----- get: data-loc enumeration -----

def test_get_enumerates_dicts_and_list_items():
    assert locs.get({'a': {'b': 1}}, lambda d: isinstance(d, dict)) == ['#', '#/a']
    assert locs.get({'a': [{'x': 1}]}, lambda d: isinstance(d, dict)) == ['#', '#/a/0']
    # predicate selects leaves only
    assert locs.get({'a': 1, 'b': 2}, lambda d: d == 2) == ['#/b']
    assert locs.get({}, lambda d: isinstance(d, dict)) == ['#']  # root matches


# ----- extract: data-loc navigation -----

def test_extract_navigates_dicts_lists_and_misses():
    data = {'a': {'b': 5}, 'list': [10, 20, 30]}
    assert locs.extract('#/a/b', data) == 5
    assert locs.extract('#/list/2', data) == 30
    assert locs.extract('#', data) == data            # root
    assert locs.extract('#/list/9', data) is None     # index out of range
    assert locs.extract('#/missing', data) is None    # missing key
    assert locs.extract('#/a/b/c', data) is None      # descend past a scalar


# ----- on_schema_lvl -----

def test_on_schema_lvl():
    assert locs.on_schema_lvl(['properties', 'x'], 0) is True            # top is schema level
    assert locs.on_schema_lvl(['then', 'x'], 1) is True                  # after a SUBSCHEMA
    assert locs.on_schema_lvl(['properties', 'x'], 1) is False           # the prop name itself
    assert locs.on_schema_lvl(['properties', 'x'], 2) is True            # value of a prop


# ----- to_regex: keyword stripping + data-loc regex -----

def test_to_regex_basic_and_recursion():
    assert locs.to_regex('#', False) == r'^\#$'
    assert locs.to_regex('#', True) == r'^\#(/.+)*$'
    assert locs.to_regex('#/properties/foo', False) == r'^\#/foo$'
    assert locs.to_regex('#/properties/foo/items', False) == r'^\#/foo/\d+$'


def test_to_regex_keyword_handling():
    # `if`/`not`/`const`/... collapse to the document root (no data behind them).
    assert locs.to_regex('#/properties/foo/if', False) == r'^\#$'
    # oneOf/allOf/anyOf consume the keyword and the following index.
    assert locs.to_regex('#/oneOf/0/properties/foo', False) == r'^\#/foo$'
    # then/else are skipped.
    assert locs.to_regex('#/properties/foo/then/properties/bar', False) == r'^\#/foo/bar$'
    # patternProperties keeps the (regex) key unescaped.
    assert locs.to_regex('#/patternProperties/^a/properties/x', False) == r'^\#/^a/x$'


def test_reduce_filters_by_schema_loc():
    locs_in = ['#/foo', '#/bar', '#/foo/x']
    assert locs.reduce('#/properties/foo', locs_in, recursive=False) == ['#/foo']
    assert locs.reduce('#/properties/foo', locs_in, recursive=True) == ['#/foo', '#/foo/x']


# ----- is_specified: deeper combinator nesting -----

def test_is_specified_combinators_and_nondict():
    assert locs.is_specified('k', {'properties': {'k': {}}}) is True
    assert locs.is_specified('k', {'allOf': [{'properties': {'k': {}}}, {}]}) is True
    assert locs.is_specified('k', {'else': {'properties': {'k': {}}}}) is True
    assert locs.is_specified('k', {'oneOf': [{}, {}]}) is False
    assert locs.is_specified('k', None) is False
    assert locs.is_specified('k', 'not-a-schema') is False