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

    # Unsetting a key that is not present in the stored YAML must be a no-op and
    # must never write the literal string "~undefined" (e.g. for schema-defaulted
    # fields that were never persisted).
    assert yaml.update(YAML2, {'absent_key': '~undefined'}) == YAML2
    assert yaml.update(YAML2, {'my_dict': {'absent_child': '~undefined'}}) == YAML2
    assert yaml.load_as_dict(yaml.update(YAML2, {'absent_key': '~undefined'})) == DATA1

    assert type(yaml.load(UNSAFE_YAML1)['illegal_tuple']) is not tuple
    assert isinstance(yaml.load(UNSAFE_YAML1)['illegal_tuple'], list)
    assert isinstance(yaml.load(UNSAFE_YAML2), yaml.YAMLSafeBase)
    assert yaml.load_as_dict('') == {}
    assert yaml.load_as_dict('blub') == {}
    assert yaml.load_as_dict('null') == {}
    assert yaml.load_as_dict('false') == {}
    assert yaml.load_as_dict('a: b') == {'a': 'b'}

    assert yaml.load_as_dict('date: 2025-01-02') == {'date': '2025-01-02'}
    assert yaml.load_as_dict('datetime: 2023-10-15T14:30:00') == {'datetime': '2023-10-15T14:30:00'}
    assert yaml.load_as_dict('bin: !!binary YWJj') == {'bin': b'abc'}
    assert yaml.load_as_dict('pairs: !!pairs [a: b, c: d]') == {'pairs': [('a', 'b'), ('c', 'd')]}
    assert yaml.load_as_dict('omap: !!omap [{z: first}, {a: last}]') == {'omap': {'z': 'first', 'a': 'last'}}
    assert yaml.load_as_dict('set: !!set {a, b, c}') == {'set': set(['a', 'b', 'c'])}

    # The fast (read-path) loader and the round-trip (validation) loader MUST
    # agree on scalar resolution -- otherwise a value reads one way for the form
    # / permissions and validates another way, blocking a commit invisibly.
    # PyYAML defaults to YAML 1.1; ruamel (the validator) follows YAML 1.2. This
    # table pins every known divergence the fast loader is patched to fix.
    bool_int_float_cases = [
        # bool: yes/no/on/off are 1.1 booleans, 1.2 strings.
        ('no', 'no'), ('yes', 'yes'), ('on', 'on'), ('off', 'off'),
        ('No', 'No'), ('OFF', 'OFF'), ('n', 'n'), ('y', 'y'),
        ('true', True), ('false', False), ('True', True), ('FALSE', False),
        # int: leading-zero is octal in 1.1, decimal in 1.2; sexagesimal is an
        # int in 1.1, a string in 1.2; 1.2 understands 0o/0x/0b.
        ('42', 42), ('-7', -7), ('+5', 5), ('0', 0), ('007', 7),
        ('0644', 644), ('0o644', 420), ('0x1F', 31), ('0b101', 5),
        ('1_000', 1000), ('1:2:3', '1:2:3'),
        # float: exponent without a dot is a string in 1.1, a float in 1.2;
        # sexagesimal float is a float in 1.1, a string in 1.2.
        ('1.0', 1.0), ('1e3', 1000.0), ('1.5e-3', 0.0015), ('.5', 0.5),
        ('5.', 5.0), ('1_000.5', 1000.5), ('1:2.5', '1:2.5'),
        ('-.inf', float('-inf')),
        # unchanged across versions (sanity).
        ('null', None), ('~', None), ('hello', 'hello'),
    ]
    for tok, expected in bool_int_float_cases:
        doc = f'x: {tok}'
        assert yaml.load_as_dict_fast(doc) == {'x': expected}, (tok, 'fast', yaml.load_as_dict_fast(doc))
        assert yaml.load_as_dict(doc) == {'x': expected}, (tok, 'rt', yaml.load_as_dict(doc))
        assert yaml.load_as_dict_fast(doc) == yaml.load_as_dict(doc), tok
