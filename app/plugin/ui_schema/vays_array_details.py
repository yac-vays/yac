"""
Generates `options.details.elements` on an array's Control from `vays_options`
attached to `items` (primitive items) or to the properties of an object items.

JsonForms renders array items via per-row dispatch with item-relative scopes.
A Control whose scope traverses `/items/` cannot work, because JsonForms'
`toDataPath` strips every other segment and yields a wrong data path
(e.g. `#/properties/users/items/properties/secret` -> `users.properties`).

The natural place for per-row renderer customization is the array Control's
`options.details.elements`. This plugin builds that structure from the items
subschema, so spec authors can attach `vays_options` to items (or to items'
properties) and have the right renderer dispatched to each row.

Requires `vays_category` on the array itself, so that a Control for the array
already exists in the ui_schema to attach to (this plugin runs after
`vays_category`).

Raises: []
"""

from app.model.plg import IUiSchema


_VAYS_OPT_KEYS = ("renderer", "renderer_options", "initial", "initial_editable")


def _extract_control_options(vays_options) -> dict | None:
    if not isinstance(vays_options, dict):
        return None
    out = {k: vays_options[k] for k in _VAYS_OPT_KEYS if k in vays_options}
    return out or None


def _build_row_elements(items_schema) -> list | None:
    """
    Build the per-row `elements` list from the items subschema. Returns None
    if there is no `vays_options` to propagate (in which case the array's
    default rendering is left untouched).
    """
    if not isinstance(items_schema, dict):
        return None

    if items_schema.get("type") == "object" and isinstance(
        items_schema.get("properties"), dict
    ):
        props = items_schema["properties"]
        if not any(
            isinstance(s, dict) and s.get("vays_options") for s in props.values()
        ):
            return None
        elements = []
        for name, sub in props.items():
            ctrl: dict = {"type": "Control", "scope": f"#/properties/{name}"}
            opts = _extract_control_options(
                sub.get("vays_options") if isinstance(sub, dict) else None
            )
            if opts:
                ctrl["options"] = opts
            elements.append(ctrl)
        return elements

    opts = _extract_control_options(items_schema.get("vays_options"))
    if opts:
        return [{"type": "Control", "scope": "#", "options": opts}]
    return None


def _strip_vays_options(items_schema) -> None:
    if not isinstance(items_schema, dict):
        return
    items_schema.pop("vays_options", None)
    if isinstance(items_schema.get("properties"), dict):
        for sub in items_schema["properties"].values():
            if isinstance(sub, dict):
                sub.pop("vays_options", None)


def _find_control(node, scope):
    if not isinstance(node, dict):
        return None
    if node.get("type") == "Control" and node.get("scope") == scope:
        return node
    if isinstance(node.get("elements"), list):
        for e in node["elements"]:
            found = _find_control(e, scope)
            if found is not None:
                return found
    return None


class VaysArrayDetails(IUiSchema):
    def order(self) -> tuple[bool, int]:
        # Must run after vays_category (True, 150), so the array's Control
        # already exists in the ui_schema.
        return True, 200

    async def process(
        self, loc: str, json_schema: dict, ui_schema: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        if json_schema.get("type") != "array":
            return json_schema, ui_schema

        items_schema = json_schema.get("items")
        elements = _build_row_elements(items_schema)
        if elements is None:
            return json_schema, ui_schema

        array_ctrl = _find_control(ui_schema, loc)
        if array_ctrl is None:
            return json_schema, ui_schema

        array_ctrl.setdefault("options", {})
        array_ctrl["options"].setdefault("details", {})
        if "elements" not in array_ctrl["options"]["details"]:
            array_ctrl["options"]["details"]["elements"] = elements

        _strip_vays_options(items_schema)
        return json_schema, ui_schema


processor = VaysArrayDetails()
