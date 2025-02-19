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