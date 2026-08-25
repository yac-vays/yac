"""
Generates `options.detail` on a nested object's Control from `vays_options`
attached to the object's properties (at any depth).

`vays_category` creates one Control for the whole object; JsonForms then
renders the object's properties from an auto-generated sub-ui-schema that
carries no options, so a `vays_options.renderer` on a nested property would
silently fall back to the default renderer. The supported JsonForms hook for
this is the object Control's `options.detail`: when it holds a ui_schema, the
object renderer dispatches it instead of generating one (scopes inside are
relative to the object's schema).

This plugin builds that detail (a `Group` labeled with the object's title,
mirroring what JsonForms would generate) whenever an object with a Control has
`vays_options` somewhere below it. Nested objects get a nested detail; nested
arrays get `options.details.elements` exactly like `vays_array_details` (which
cannot handle them itself: it requires a Control at the array's own location).

Requires `vays_category` on the object itself, so that a Control for the
object already exists in the ui_schema to attach to (this plugin runs after
`vays_category`). Runs post order, so a nested object is visited before its
parent: with no Control at the child's location it is a no-op there, and the
parent's pass consumes the whole subtree.

Raises: []
"""

from app.model.plg import IUiSchema
from app.plugin.ui_schema.vays_array_details import (
    _build_row_elements,
    _extract_control_options,
    _find_control,
    _strip_vays_options,
)


def _contains_vays_options(schema) -> bool:
    if not isinstance(schema, dict):
        return False
    if "vays_options" in schema:
        return True
    props = schema.get("properties")
    if isinstance(props, dict):
        if any(_contains_vays_options(s) for s in props.values()):
            return True
    return _contains_vays_options(schema.get("items"))


def _build_detail(obj_schema, label: str) -> dict | None:
    """
    Build the object Control's `options.detail` ui_schema. Returns None if
    there is no `vays_options` anywhere below (in which case the object's
    default rendering is left untouched).
    """
    props = obj_schema.get("properties")
    if not isinstance(props, dict):
        return None
    if not any(_contains_vays_options(s) for s in props.values()):
        return None

    elements = []
    for name, sub in props.items():
        ctrl: dict = {"type": "Control", "scope": f"#/properties/{name}"}
        if isinstance(sub, dict):
            opts = _extract_control_options(sub.get("vays_options"))
            if opts:
                ctrl["options"] = opts
            if sub.get("type") == "object":
                detail = _build_detail(sub, sub.get("title", name))
                if detail is not None:
                    ctrl.setdefault("options", {}).setdefault("detail", detail)
            elif sub.get("type") == "array":
                rows = _build_row_elements(sub.get("items"))
                if rows is not None:
                    details = ctrl.setdefault("options", {}).setdefault("details", {})
                    details.setdefault("elements", rows)
        elements.append(ctrl)

    return {"type": "Group", "label": label, "elements": elements}


def _strip_subtree_vays_options(schema) -> None:
    if not isinstance(schema, dict):
        return
    schema.pop("vays_options", None)
    props = schema.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            _strip_subtree_vays_options(sub)
    if "items" in schema:
        _strip_vays_options(schema.get("items"))


class VaysObjectDetails(IUiSchema):
    def order(self) -> tuple[bool, int]:
        # Must run after vays_category (True, 150), so the object's Control
        # already exists in the ui_schema.
        return True, 200

    async def process(
        self, loc: str, json_schema: dict, ui_schema: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        if json_schema.get("type") != "object":
            return json_schema, ui_schema

        obj_ctrl = _find_control(ui_schema, loc)
        if obj_ctrl is None:
            return json_schema, ui_schema

        label = json_schema.get("title", loc.rsplit("/", maxsplit=1)[-1])
        detail = _build_detail(json_schema, label)
        if detail is None:
            return json_schema, ui_schema

        obj_ctrl.setdefault("options", {}).setdefault("detail", detail)

        for sub in json_schema.get("properties", {}).values():
            _strip_subtree_vays_options(sub)
        return json_schema, ui_schema


processor = VaysObjectDetails()
