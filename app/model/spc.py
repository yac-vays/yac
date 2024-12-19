from pydantic import BaseModel, Field
from typing_extensions import Annotated
from pydantic.config import Extra

from app.model import out

# pylint: disable=too-few-public-methods


class Request(BaseModel):
    headers: dict = {}


class TypeLog(out.TypeLog):
    plugin: str
    details: dict = {}


class TypeAction(out.TypeAction):
    plugin: str
    details: dict = {}


class Type(out.Type):
    name_generator: str = "uuid()"
    details: dict = {}
    logs: list[TypeLog] = []
    actions: list[TypeAction] = []  # TODO fix (also see router.read.get_types())


class Role(BaseModel):
    class Config:
        extra = Extra.allow


class Sets(BaseModel):
    class Config:
        extra = Extra.allow


class Schema(BaseModel):
    class Config:
        extra = Extra.allow


class Specs(BaseModel):
    request: Request = Request()
    types: list[Type]
    type: Type | None = None
    roles: list[Role] = []
    sets: Sets = Sets()
    json_schema: Annotated[Schema, Field(alias="schema")]
