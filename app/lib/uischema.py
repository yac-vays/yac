"""
Raises: []
"""


def __add_categorization(schema: dict) -> dict:
    """
    Adds the top-level type Categorization if the schema is empty.
    """
    if not schema:
        schema = {"type": "Categorization", "elements": []}
    return schema


def __add_subschema(typ: str, lbl: str, schema: dict) -> dict:
    """
    Adds a subschema of given type and label to the schema if not there already
    and returns the added/found subschema.
    """
    sub = next(
        (s for s in schema["elements"] if s["type"] == typ and s["label"] == lbl), None
    )
    if sub is None:
        schema["elements"].append({"type": typ, "label": lbl, "elements": []})
        sub = schema["elements"][-1]
    return sub


def __add_control(loc: str, opt: dict, schema: dict) -> None:
    """
    Adds an element with options to the given schema.
    """
    schema["elements"].append(
        {
            "type": "Control",
            "scope": loc,
            "options": opt,
        }
    )


def add_element(loc: str, opt: dict, cat: str, grp: str | None, schema: dict) -> dict:
    """
    Adds an element with loc-string to a ui_schema with the following static
    structure (and generates the structure if not there already):

        Categorization/Category[/Group]/Control
    """
    schema = __add_categorization(schema)
    subschema = __add_subschema("Category", cat, schema)
    if grp is not None:
        subschema = __add_subschema("Group", grp, subschema)
    __add_control(loc, opt, subschema)
    return schema
