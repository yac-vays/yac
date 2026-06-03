from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr
from typing_extensions import Annotated
from pydantic.config import Extra  # pylint: disable=no-name-in-module

from app.model import out


class Request(BaseModel):
    headers: dict = {}


class AuthOIDCJWT(BaseModel):
    # Format-strings applied to the validated JWT id-token (e.g. "{name}",
    # "{givenName} {surname}"). The `_fallback` variants are used when the
    # primary template references a claim that is not present.
    name: str = "{name}"
    full_name: str = "{givenName} {surname}"
    full_name_fallback: str = "{name}"
    email: str = "{mail}"
    email_fallback: str = "{name}@localhost"


class AuthOIDC(BaseModel):
    url: str = "https://localhost/.well-known/openid-configuration"
    client_ids: list[str] = []
    jwt: AuthOIDCJWT = AuthOIDCJWT()


class AuthCORS(BaseModel):
    origins: list[str] = ["https://localhost"]


class Auth(BaseModel):
    oidc: AuthOIDC = AuthOIDC()
    cors: AuthCORS = AuthCORS()


class TypeLog(out.TypeLog):
    plugin: str
    details: dict = {}


class TypeAction(out.TypeAction):
    plugin: str
    details: dict = {}


class TypeLimit(BaseModel):
    """
    A cap on how many entities (or how much of a summed quota) of this type
    may exist within a group. Evaluated per write operation; see
    `app.lib.limits` and docs/yac/specs/file/limits.md.

    All of `scope`, `value` and `max` are Jinja2 expressions rendered with
    the same variables as `roles`/`sets` (`old`, `new`, `name`, `user`,
    `context`, `env`, `request`). When aggregating, `old` is the entity being
    scanned and `new` is the entity being created/changed.
    """

    title: str
    # Jinja2 test deciding whether a scanned entity (`old`) belongs to the
    # same group as the incoming entity (`new`). Default: every entity counts.
    scope: str = "true"
    aggregate: Literal["count", "sum"] = "count"
    # Jinja2 number expression: each matching entity's contribution to the
    # sum. Ignored when `aggregate` is `count`.
    value: str = "1"
    # Jinja2 number expression: the cap. May depend on `user`/`context`/`new`
    # to express per-user or plan-dependent limits.
    max: str
    # Operations this limit is enforced on. `delete` can never exceed a cap.
    on: list[Literal["create", "change"]] = ["create", "change"]


class Type(out.Type):
    name_generator: str = "uuid()"
    logs: list[TypeLog] = []  # type: ignore[assignment]
    actions: list[TypeAction] = []  # type: ignore[assignment]
    limits: list[TypeLimit] = []

    def to_public(self) -> out.Type:
        """
        Strips spc-internal fields (`plugin`, `details` on logs/actions,
        `name_generator` and `limits`) so the result is safe to return from
        the API.
        """
        return out.Type.model_validate(
            self.model_dump(
                exclude={
                    "name_generator": True,
                    "limits": True,
                    "logs": {"__all__": {"plugin", "details"}},
                    "actions": {"__all__": {"plugin", "details"}},
                }
            )
        )


class Repo(BaseModel):
    plugin: str = "git_direct"
    connection: dict = {}  # repo_plugin connection config, see app/plugin/repo/*.py
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
    context: dict = {}
    types: list[Type]
    type: Type | None = None
    repo: Repo = Repo()
    auth: Auth = Auth()
    roles: list[Role] = []
    sets: Sets = Sets()
    json_schema: Annotated[Schema, Field(alias="schema")]
    # Stable digest of (specs source text, op signature) attached by lib.specs.read.
    # Used as a cheap cache key for downstream consumers (e.g. perms cache).
    _signature: str = PrivateAttr(default="")
