"""
Tests for `lib.uischema` -- the small helper that incrementally builds the
JSONForms UI schema (a `Categorization/Category[/Group]/Control` tree) one
control at a time. The functions mutate-and-return a schema dict, so the tests
focus on: bootstrapping an empty schema, de-duplicating Category/Group nodes,
and placing controls at the right nesting level.
"""

from app.lib import uischema


# ----- add_element on an empty schema -----

def test_add_element_bootstraps_categorization_and_category():
    schema = uischema.add_element("#/properties/a", {"opt": 1}, "Cat", None, {})
    assert schema["type"] == "Categorization"
    assert len(schema["elements"]) == 1
    cat = schema["elements"][0]
    assert cat["type"] == "Category" and cat["label"] == "Cat"
    # the control lands directly under the category (no group given)
    assert cat["elements"] == [
        {"type": "Control", "scope": "#/properties/a", "options": {"opt": 1}}
    ]


def test_add_element_with_group_nests_control_under_group():
    schema = uischema.add_element("#/properties/a", {}, "Cat", "Grp", {})
    cat = schema["elements"][0]
    grp = cat["elements"][0]
    assert grp["type"] == "Group" and grp["label"] == "Grp"
    assert grp["elements"][0]["scope"] == "#/properties/a"
    # the control is NOT directly under the category when a group is given
    assert all(e["type"] != "Control" for e in cat["elements"])


# ----- de-duplication of Category / Group containers -----

def test_add_element_reuses_existing_category():
    schema = uischema.add_element("#/properties/a", {}, "Cat", None, {})
    schema = uischema.add_element("#/properties/b", {}, "Cat", None, schema)
    # same category label -> still exactly one Category, two controls under it
    assert len(schema["elements"]) == 1
    scopes = [c["scope"] for c in schema["elements"][0]["elements"]]
    assert scopes == ["#/properties/a", "#/properties/b"]


def test_add_element_separate_categories_and_groups():
    schema = {}
    schema = uischema.add_element("#/p/a", {}, "Cat1", "G1", schema)
    schema = uischema.add_element("#/p/b", {}, "Cat1", "G2", schema)
    schema = uischema.add_element("#/p/c", {}, "Cat2", "G1", schema)

    assert [c["label"] for c in schema["elements"]] == ["Cat1", "Cat2"]
    cat1 = schema["elements"][0]
    assert [g["label"] for g in cat1["elements"]] == ["G1", "G2"]
    # G1 in Cat1 and G1 in Cat2 are distinct nodes
    cat2 = schema["elements"][1]
    assert cat2["elements"][0]["elements"][0]["scope"] == "#/p/c"


def test_add_element_reuses_group_within_same_category():
    schema = {}
    schema = uischema.add_element("#/p/a", {}, "Cat", "G", schema)
    schema = uischema.add_element("#/p/b", {}, "Cat", "G", schema)
    grp = schema["elements"][0]["elements"][0]
    assert len(schema["elements"][0]["elements"]) == 1  # one group reused
    assert [c["scope"] for c in grp["elements"]] == ["#/p/a", "#/p/b"]
