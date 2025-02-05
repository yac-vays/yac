from pydantic import BaseModel, Field
from typing_extensions import Annotated
from pydantic.config import Extra  # pylint: disable=no-name-in-module

from app.model import out


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
    logs: list[TypeLog] = []
    actions: list[TypeAction] = []  # TODO fix (also see router.read.get_types())


class Repo(BaseModel):
    details: dict = {}


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
    version: int | None = None
    request: Request = Request()
    types: list[Type]
    type: Type | None = None
    repo: Repo = Repo()
    roles: list[Role] = []
    sets: Sets = Sets()
    json_schema: Annotated[Schema, Field(alias="schema")]
