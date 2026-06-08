from typing import Literal

from fastapi import Query, Path
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app import consts
from app.model import out

PathType = Annotated[str, Path(alias="type", pattern=consts.TYPE_PATTERN)]
PathName = Annotated[str, Path(alias="name", pattern=consts.NAME_PATTERN)]
PathAction = Annotated[str, Path(alias="action", pattern=consts.ACTION_PATTERN)]

QuerySearch = Annotated[str, Query(pattern=consts.SEARCH_PATTERN)]
QueryMsg = Annotated[str, Query(pattern=consts.MSG_PATTERN)]
QuerySkip = Annotated[int, Query(ge=0)]
QueryLimit = Annotated[int, Query(gt=0, le=10000)]
QueryActions = Annotated[
    list[Annotated[str, Field(pattern=consts.ACTION_PATTERN)]], Query()
]


class Entity(BaseModel):
    name: Annotated[str | None, Field(pattern=consts.NAME_PATTERN)]


class LinkEntity(Entity):
    """
    A new entity that referes to an other one regarding all its data.
    """

    link_name: Annotated[
        str,
        Field(
            description="Contains the name of the entity where this one is linked to",
            alias="link",
            pattern=consts.NAME_PATTERN,
        ),
    ]


class CopyEntity(Entity):
    """
    A new entity that copies all data form an existing one.
    """

    copy_name: Annotated[
        str,
        Field(
            description="Contains the name of the entity to copy",
            alias="copy",
            pattern=consts.NAME_PATTERN,
        ),
    ]


class NewEntity(Entity):
    """
    A new entity with raw YAML data.
    """

    yaml: str


class ReplaceEntity(Entity):
    """
    An existing entity with raw YAML data.

    To ensure the entity was not modified in the meantime, you have to send the
    old data in raw YAML.
    """

    yaml_old: str
    yaml_new: str


class UpdateEntity(Entity):
    """
    An existing entity with only the data that needs to be changed (as an
    object, not raw YAML data).

    Objects will be integrated, so only supplying one key means you only modify
    this one key (and not replace the whole object). Lists and base types on
    the other hand are completely replaced.

    **Hint:** The string "~undefined" will unset the whole object-key / list-item.

    To ensure the entity was not modified in the meantime, you can send the old
    data in raw YAML (optional).

    `yaml_base` is the YAML the `data` patch is merged into when computing the
    result (and the returned `schemas.yaml`). It lets a client validate against
    the YAML it is actually editing — preserving its comments / formatting / key
    order — instead of the stored entity. When omitted, the stored entity's YAML
    is used. This only affects the produced *content*; permissions, limits and
    schema constraints are still evaluated against the stored entity.
    """

    data: dict
    yaml_old: str | None = None
    yaml_base: str | None = None


class Operation(BaseModel):
    operation: Literal["read", "create", "change", "delete", "arbitrary"] = "read"
    type_name: Annotated[str, Field(alias="type", pattern=consts.TYPE_PATTERN)]
    name: Annotated[str | None, Field(pattern=consts.NAME_PATTERN)] = None
    actions: list[Annotated[str, Field(pattern=consts.ACTION_PATTERN)]] = []
    entity: (
        NewEntity | CopyEntity | LinkEntity | UpdateEntity | ReplaceEntity | None
    ) = None


class OperationRequest(Operation):
    class Config:
        arbitrary_types_allowed = True

    request_headers: dict
    request_ip: str
    user: out.User
