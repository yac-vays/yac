from app.lib import locs

def test():
    assert not locs.is_specified('any-key', {})
    assert not locs.is_specified('any-other-key', True)
    assert locs.is_specified('a-key', {'type': 'object', 'properties': {'a-key': True}})
    assert locs.is_specified('a-key', {'type': 'object', 'properties': {'a-key': False}})
    assert not locs.is_specified('2nd-key', {'type': 'object', 'properties': {'a-key': True}})
    assert not locs.is_specified('2nd-key', {'type': 'object', 'properties': {}, 'additionalProperties': True})
    assert locs.is_specified('mykey', {'anyOf': [{}, {'type': 'object', 'if': True, 'then': {'properties': {'mykey': False}}}]})
