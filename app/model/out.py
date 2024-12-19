import enum
from typing import Any
from typing import Literal
from typing_extensions import Annotated

from pydantic import BaseModel, Field

from app import consts

#
# User
#


class Permission(str, enum.Enum):
    """
    See docs/perms.md
    """

    READ = "see"
    CREATE = "add"
    RENAME = "rnm"
    COPY = "cpy"
    LINK = "lnk"
    EDIT = "edt"
    CLEANUP = "cln"
    DELETE = "del"
    RUNACTION = "act"
    ADMINISTER = "adm"


class User(BaseModel):
    """
    Note that data in `token` strongly depends on the OpenID Connect
    provider and should not be used directly (except for in the *specs*).
    """

    name: Annotated[str, Field(examples=["user73"])]
    email: Annotated[str, Field(examples=["user73@example.com"])]
    full_name: Annotated[str, Field(examples=["John Doe"])]
    token: dict = {}


#
# Entity
#


class Log(BaseModel):
    name: str
    message: str
    time: Annotated[
        str,
        Field(description="Should contain a parsable date/time string"),
    ]
    progress: Annotated[
        int | None,
        Field(
            description="Can indicate a progress for this name (from 0 to 100)",
            ge=0,
            le=100,
        ),
    ] = None
    problem: Annotated[
        bool | None,
        Field(description="Can indicate if a problem exists (true/false)"),
    ] = None


class Entity(BaseModel):
    name: str


class ListedEntity(Entity):
    """
    An entity as it will be listed, inluding some preview data and logs.
    """

    link: Annotated[
        str | None,
        Field(
            description="Contains the name of the entity where this one is linked to or null if it is not linked"
        ),
    ] = None
    options: Annotated[
        dict,
        Field(
            description="Some options of this entity for the list preview (including the defaults for undefined options)"
        ),
    ] = {}
    perms: list[Permission | str] = ["see"]


class EntityList(BaseModel):
    hash: Annotated[str, Field(description=consts.DESC_HASH)]
    list: list[ListedEntity]


class DetailedEntity(ListedEntity):
    """
    An entity as it will be returned with all its data.
    """

    data: Annotated[
        dict,
        Field(
            description="All data that this entity has defined (including the defaults for undefined options)"
        ),
    ] = {}
    yaml: Annotated[str | None, Field(description="The raw YAML data")] = None
    hash: Annotated[str, Field(description=consts.DESC_HASH)]


#
# Data
#


class Diff(BaseModel):
    name: Annotated[str, Field(description="Name of the (new) entity")]
    hash: Annotated[str, Field(description=consts.DESC_HASH)]
    patch: Annotated[
        str, Field(description="Patch that reflects the changes done on the entity.")
    ]


class Schema(BaseModel):
    """
    A generated schema (depending on data and definitions) that describes
    the possible options that can be set by the current user for a particular
    entity.

    It's in the format of a JSONForms and consisting of two separate schemata
    and the data.

    **Note**: The schemata are generated upon the data, so they might change
    when data changes. So make sure you regenerate them for chaning data to
    always have the most correct schemata.
    """

    json_schema: Annotated[
        dict,
        Field(
            description="""
    The data structure (json-schema.org 2020-12)
    """,
            examples=[{"type": "object", "properties": {"owner": {"type": "string"}}}],
        ),
    ]
    ui_schema: Annotated[
        dict,
        Field(
            description="The form representation (jsonforms.io v3.1.0)",
            examples=[{"type": "Control", "scope": "#/properties/owner"}],
        ),
    ]
    data: Annotated[
        dict,
        Field(
            description="""The data used to build the schemas and validate against it. For the different types of operations, this has different sources.

- NewEntity: `entity.yaml`
- CopyEntity: `{}`
- LinkEntity: `{}`
- UpdateEntity: The existing data **merged** with `entity.data`!
- ReplaceEntity: `entity.yaml_new`""",
            examples=[{"owner": "Bender"}],
        ),
    ] = {}
    valid: Annotated[
        bool,
        Field(description="If the data was valid according to the `json_schema`"),
    ] = False
    message: Annotated[
        str | None,
        Field(
            description="The explanation why the validation failed (if `valid` is `false`)",
            examples=["23 is not of type 'string'"],
        ),
    ] = None
    validator: Annotated[
        str,
        Field(
            description="The validator component that caused the message (if `valid` is `false`)",
            examples=["format", "additionalProperties"],
        ),
    ] = ""
    json_schema_loc: Annotated[
        str,
        Field(
            description="Location in the json_schema where the validation failed (if `valid` is `false`)",
            examples=["#/properties/abc"],
        ),
    ] = ""
    data_loc: Annotated[
        str,
        Field(
            description="Location in the data where the validation failed (if `valid` is `false`)",
            examples=["#/properties/abc"],
        ),
    ] = ""


class Request(BaseModel):
    valid: Annotated[
        bool,
        Field(description="If the requested operation is valid and permitted."),
    ] = False
    message: Annotated[
        str | None,
        Field(
            description="The explanation why the validation of the request failed (if `valid` is `false`)",
            examples=["Action 'blub' is not defined"],
        ),
    ] = None


class ValidationResult(BaseModel):
    schemas: Schema
    request: Request


#
# Type
#


class TypeOption(BaseModel):
    name: Annotated[str, Field(examples=["os"])]
    title: Annotated[str, Field(examples=["Operating System"])]
    default: Annotated[
        Any | None,
        Field(
            description="The default value to use in the list if the entity has not set this option"
        ),
    ] = None
    aliases: Annotated[
        dict,
        Field(description="A key-value list of aliases to use for the options"),
    ] = {}


class TypeLog(BaseModel):
    name: Annotated[str, Field(examples=["installation"])]
    title: Annotated[str, Field(examples=["Installation"])]
    progress: Annotated[
        bool,
        Field(description="Whether to show a progress indicator in UI"),
    ] = False
    problem: Annotated[
        bool,
        Field(description="Whether to show a problem indicator in UI"),
    ] = False


class TypeActionHook(str, enum.Enum):
    """
    Hooks are used to define on what operations an action can be (if
    `force: false`) or automatically gets (if `force: true`) triggered (for the
    specific entity type).

    The possible operations are:

      - `arbitrary` means the `POST /entity/{type}/{name}/run/{action}` endpoint
      - `create:*` means the `POST /entity/{type}` endpoint
      - `change:*` means the `PUT` and `PATCH /entity/{type}` endpoints
      - `delete:*` means the `DELETE /entity/{type}/{name}` endpoint
    """

    ARBITRARY = "arbitrary"
    CREATE_BEFORE = "create:before"
    CREATE_AFTER = "create:after"
    CHANGE_BEFORE = "change:before"
    CHANGE_AFTER = "change:after"
    DELETE_BEFORE = "delete:before"
    DELETE_AFTER = "delete:after"


class TypeAction(BaseModel):
    name: Annotated[str, Field(examples=["install"])]
    title: Annotated[str, Field(examples=["Install"])]
    description: str = ""
    dangerous: Annotated[
        bool,
        Field(
            description="Show confirmation dialogue in UI (with text from `description` field)"
        ),
    ] = False
    icon: Annotated[
        str,
        Field(description="An SVG image used as button icon for this action"),
    ] = consts.SVG_ACTION
    perms: Annotated[
        list[Permission | str],
        Field(
            description="The list of perms on the particular entity, at least one of which is required to run this action"
        ),
    ] = ["act"]
    force: Annotated[
        bool,
        Field(
            description="Run this action automatically when performing the hooked operation (otherwise it is optional; has no effect on the hook `arbitrary`; if `force` is `true`, actions perms are bypassed for all hooks except for `arbitrary`)"
        ),
    ] = False
    hooks: list[TypeActionHook] = [TypeActionHook.ARBITRARY]


class TypeFavoriteOperation(str, enum.Enum):
    """
    The following standard operations exist:

    For the endpoint `POST /entity/{type}`:

      - `create_new` (if `NewEntity` is sent)
        *But this is not available here because it's global operation for a
        type and not entity-specific.*
      - `create_copy` (if `CopyEntity` is sent)
      - `create_link` (if `LinkEntity` is sent)

    For the endpoints `PUT` and `PATCH /entity/{type}`:

      - `change`

    And for the endpoint `DELETE /entity/{type}/{name}`:

      - `delete`
    """

    # CREATE_NEW is not available as favorite
    CREATE_COPY = "create_copy"
    CREATE_LINK = "create_link"
    CHANGE = "change"
    DELETE = "delete"


class TypeFavorite(BaseModel):
    name: Annotated[
        str | TypeFavoriteOperation,
        Field(
            description="""
    Name of the action or operation
    """
        ),
    ] = "change"
    action: Annotated[
        bool,
        Field(
            description="""
    Is it a (custom defined) action (or a standard operation)
    """
        ),
    ] = False


class Type(BaseModel):
    name: Annotated[str, Field(examples=["host"])]
    title: Annotated[str, Field(examples=["Hosts"])]
    name_pattern: Annotated[
        str,
        Field(description="Regular expression for names of entities of this type"),
    ] = consts.NAME_PATTERN
    name_example: Annotated[
        str,
        Field(description="An example for a name of an entity of this type"),
    ] = ""
    name_generated: Annotated[
        Literal["never", "optional", "enforced"],
        Field(description=consts.DESC_NAME_GENERATED),
    ] = "never"
    description: str = ""
    create: Annotated[
        bool,
        Field(
            description="Allow creation of this type of entities (still requires the according *perms*)"
        ),
    ] = True
    change: Annotated[
        bool,
        Field(
            description="Allow modifications of this type of entities (still requires the according *perms*)"
        ),
    ] = True
    delete: Annotated[
        bool,
        Field(
            description="Allow deletion of this type of entities (still requires the according *perms*)"
        ),
    ] = True
    favorites: Annotated[
        list[TypeFavorite],
        Field(
            description="A list of actions and operations to defined the more prominent buttons and their order in UI"
        ),
    ] = []
    options: Annotated[
        list[TypeOption],
        Field(
            description="A list of data values always attached when listing entities of this type"
        ),
    ] = []
    logs: list[TypeLog] = []
    actions: list[TypeAction] = []


#
# Status
#


class Meta(BaseModel):
    version: str


class Status(BaseModel):
    hash: Annotated[str, Field(description=consts.DESC_HASH)]
