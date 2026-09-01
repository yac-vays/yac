from typing import Literal

from pydantic import BaseModel, Field
from typing_extensions import Annotated
from pydantic.config import Extra  # pylint: disable=no-name-in-module

from app.model import out


class Request(BaseModel):
    headers: dict = {}


class AuthOIDCJWT(BaseModel):
    # Format-strings applied to the validated JWT id-token (e.g. "{name}",
    # "{givenName} {surname}"). The `_fallback` variants are used when the
    # primary template references a claim that is not present.
    name: str = "{sub}"
    full_name: str = "{name}"
    full_name_fallback: str = "{given_name} {family_name}"
    email: str = "{email}"
    email_fallback: str = "{sub}@localhost"


class AuthOIDCAccessTokenJWT(AuthOIDCJWT):
    # Access tokens from the `client_credentials` grant carry no user
    # claims (`name`, `given_name`, ...), so the `sub` (usually the
    # technical client name) is the only sensible default fallback.
    full_name_fallback: str = "{sub}"


class AuthOIDCAccount(BaseModel):
    # Static identity attached to a machine client, keyed by the token's
    # `sub` in `AuthOIDCAccessTokens.accounts`. Unset fields fall back to
    # the `access_tokens.jwt` format-strings.
    name: str = ""
    full_name: str = ""
    email: str = ""


class AuthOIDCAccessTokens(BaseModel):
    # Accepting OIDC JWT access tokens (RFC 9068, e.g. from the OAuth2
    # `client_credentials` grant) is enabled by listing at least one
    # accepted `aud`. A bearer token whose `aud` matches one of these
    # takes the access-token path instead of the id-token path.
    audiences: list[str] = []
    algorithms: list[str] = ["RS256"]
    # Optional allow-list of accepted `sub` values (empty = any subject
    # that passes the signature/iss/exp/aud checks).
    subjects: list[str] = []
    jwt: AuthOIDCAccessTokenJWT = AuthOIDCAccessTokenJWT()
    accounts: dict[str, AuthOIDCAccount] = {}


class AuthOIDC(BaseModel):
    url: str = "https://localhost/.well-known/openid-configuration"
    client_ids: list[str] = []
    jwt: AuthOIDCJWT = AuthOIDCJWT()
    access_tokens: AuthOIDCAccessTokens = AuthOIDCAccessTokens()


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
    A cap on how much of a group's summed `value` may exist for this type.
    Evaluated per write operation; see `app.lib.limits` and
    docs/yac/specs/file/limits.md.

    `value` is summed over every in-scope entity (plus the incoming one). It
    defaults to `1`, so an unset `value` simply counts entities; set it to a
    data expression (e.g. `old.data.quota`) to sum a quota instead.

    All of `scope`, `value` and `max` are Jinja2 expressions rendered with
    the same variables as `roles`/`sets` (`old`, `new`, `name`, `user`,
    `context`, `env`, `request`). While aggregating, `old` is the entity being
    scanned and `new` is the entity being created/changed.
    """

    title: str
    # Jinja2 test deciding whether a scanned entity (`old`) belongs to the
    # same group as the incoming entity (`new`). Default: every entity counts.
    scope: str = "true"
    # Jinja2 number expression: one entity's contribution to the sum. Defaults
    # to 1 (so the sum counts entities); set to e.g. `old.data.quota` to sum a
    # field instead.
    value: str = "1"
    # Jinja2 number expression: the cap. May depend on `user`/`context`/`new`
    # to express per-user or plan-dependent limits.
    max: str
    # Operations this limit is enforced on. `delete` can never exceed a cap.
    on: list[Literal["create", "edit"]] = ["create", "edit"]
    # Optional data-loc of the entity-data property this limit relates to,
    # in the same `#/key/subkey` syntax as `data_loc` (e.g. `#/cpus` or
    # `#/disks/data_gb`; quote it in YAML, `#` starts a comment). Purely
    # informational for UIs (VAYS anchors the usage indicator on that field);
    # never evaluated.
    path: str | None = None


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
