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


# ----- to_data_path: keyword stripping -----

def test_to_data_path_basic():
    assert locs.to_data_path('#') == []
    assert locs.to_data_path('#/properties/foo') == [('key', 'foo')]
    assert locs.to_data_path('#/properties/foo/items') == [('key', 'foo'), ('index', '')]
    assert locs.to_data_path('#/prefixItems/1') == [('key', '1')]


def test_to_data_path_keyword_handling():
    # `if`/`not`/`const`/... have no data behind them (document root only).
    assert locs.to_data_path('#/properties/foo/if') is None
    # oneOf/allOf/anyOf consume the keyword and the following index.
    assert locs.to_data_path('#/oneOf/0/properties/foo') == [('key', 'foo')]
    assert locs.to_data_path('#/oneOf/0/properties/a/anyOf/1/oneOf/2/properties/b') == [
        ('key', 'a'), ('key', 'b')]
    # then/else are skipped.
    assert locs.to_data_path('#/properties/foo/then/properties/bar') == [('key', 'foo'), ('key', 'bar')]
    # property names that equal keywords are names, not keywords.
    assert locs.to_data_path('#/properties/if/properties/items') == [('key', 'if'), ('key', 'items')]
    # patternProperties keeps the (regex) key.
    assert locs.to_data_path('#/patternProperties/^a/properties/x') == [('pattern', '^a'), ('key', 'x')]


def test_reduce_root_and_keywords_without_data():
    locs_in = ['#', '#/foo', '#/foo/x']
    assert locs.reduce('#', locs_in) == ['#']
    assert locs.reduce('#', locs_in, recursive=True) == locs_in
    assert locs.reduce('#/properties/foo/if', locs_in) == ['#']


def test_reduce_items_and_literal_keys():
    locs_in = ['#/foo', '#/foo/0', '#/foo/12', '#/foo/x', '#/foo/0/a', '#/f.o/0']
    assert locs.reduce('#/properties/foo/items', locs_in) == ['#/foo/0', '#/foo/12']
    # keys are no regexes
    assert locs.reduce('#/properties/f.o/items', locs_in) == ['#/f.o/0']


def test_reduce_pattern_properties_search_the_key():
    locs_in = ['#/h1', '#/h1/x', '#/ah', '#/b', '#/h1/sub']
    # anchored: like JSON-Schema, the pattern is searched in the key
    assert locs.reduce('#/patternProperties/^h', locs_in) == ['#/h1']
    assert locs.reduce('#/patternProperties/h', locs_in) == ['#/h1', '#/ah']
    assert locs.reduce('#/patternProperties/^h.*$/properties/x', locs_in) == ['#/h1/x']
    # a pattern never spans more than one key (the regex matched '#/h1/sub')
    assert locs.reduce('#/patternProperties/h1.sub', locs_in) == []
    assert locs.reduce('#/patternProperties/(', locs_in) == []  # invalid pattern


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

def test_specification_and_removed():
    from app.consts import REMOVED

    live = {'type': 'object', 'properties': {'a': {'type': 'string'}}}
    assert locs.specification('a', live) == locs.DEFINED
    assert locs.specification('b', live) is None
    assert locs.specification('a', True) is None

    marked = {'type': 'object', 'properties': {'a': {REMOVED: 'perms', 'not': True}}}
    assert locs.specification('a', marked) == 'perms'
    assert locs.is_specified('a', marked)  # defined, just not live

    # a live definition anywhere wins over a removed one
    mixed = {'oneOf': [marked, live]}
    assert locs.specification('a', mixed) == locs.DEFINED
    nested = {'then': marked, 'else': {'properties': {'a': {REMOVED: 'if', 'not': True}}}}
    assert locs.specification('a', nested) == 'perms'  # first reason found

    patterned = {'type': 'object', 'patternProperties': {'^h': {'type': 'string'}, '^x': {REMOVED: 'perms', 'not': True}}}
    assert locs.specification('h1', patterned) == locs.DEFINED
    assert locs.specification('ah', patterned) is None
    assert locs.specification('x1', patterned) == 'perms'
    assert locs.specification('a', {'patternProperties': {'(': {}}}) is None  # invalid pattern

    assert locs.removed('#', 'perms') is None
    assert locs.removed('#/properties/a', 'if') == {REMOVED: 'if', 'not': True}
