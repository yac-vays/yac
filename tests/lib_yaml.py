from app.lib import yaml

DATA1 = {
    'my_str': 'blub',
    'my_list': [8, 2, 5],
    'my_dict': {
        'a': True,
        'b': 'foo',
        'c': [1, False],
        'd': None
    }
}

YAML_FROM_DATA1 = '''---
my_str: blub
my_list:
  - 8
  - 2
  - 5
my_dict:
  a: true
  b: foo
  c:
    - 1
    - false
  d: null
'''

YAML2 = '''---
# file header
# multiline comment

# before first element
my_dict: # first element line
  # before child line
  a: true # child line
  b: foo
  c: [1, false] # later child line
  d: null
# after first element

# in between

# before second element
my_list: [8, 2, 5]
# between two elements
my_str: blub # later element line
'''

YAML2_WITHOUT_MYDICTB = '''---
# file header
# multiline comment

# before first element
my_dict: # first element line
  # before child line
  a: true # child line
  c: [1, false] # later child line
  d: null
# after first element

# in between

# before second element
my_list: [8, 2, 5]
# between two elements
my_str: blub # later element line
'''

YAML2_WITHOUT_MYDICT = '''---
# file header
# multiline comment

# before first element
my_list: [8, 2, 5]
# between two elements
my_str: blub # later element line
'''

YAML3 = '''---
# my start comment


# one more
top:
  # yaay
  - myky: a
    2ky: b # blub
    3ky: c
  - 1ky: d # yes!
    2ky: e

# end of top comment



# complete end
a: b
'''

YAML3_CHANGED_LISTELEM = '''---
# my start comment


# one more
top:
  # yaay
  - myky: a
    2ky: b
    3ky: c
  - 1ky: d
  - true
a: b
'''

UNSAFE_YAML1 = '''---
illegal_tuple: !!python/tuple [t, e, s, t]
'''

UNSAFE_YAML2 = '''!!python/object/apply:subprocess.Popen
- ls
'''

def test():

    assert yaml.load_as_dict(YAML_FROM_DATA1) == DATA1
    assert yaml.dump(DATA1) == YAML_FROM_DATA1

    assert yaml.update(YAML2, {'new_key': True}) == f'{YAML2}new_key: true\n'
    assert yaml.update(YAML2, {'my_dict': '~undefined'}) == YAML2_WITHOUT_MYDICT
    assert yaml.update(YAML2, {'my_dict': {'b': '~undefined'}}) == YAML2_WITHOUT_MYDICTB
    assert yaml.update(YAML3, {'top': [{'myky': 'a', '2ky': 'b', '3ky': 'c'}, {'1ky': 'd', '2ky': '~undefined'}, True]}) == YAML3_CHANGED_LISTELEM
    
    assert type(yaml.load(UNSAFE_YAML1)['illegal_tuple']) is not tuple
    assert isinstance(yaml.load(UNSAFE_YAML1)['illegal_tuple'], list)
    assert isinstance(yaml.load(UNSAFE_YAML2), yaml.YAMLSafeBase)
    assert yaml.load_as_dict('') == {}
    assert yaml.load_as_dict('blub') == {}
    assert yaml.load_as_dict('null') == {}
    assert yaml.load_as_dict('false') == {}
    assert yaml.load_as_dict('a: b') == {'a': 'b'}
