from typing import Literal

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="yac_", env_nested_delimiter="__")

    root_path: str = "/"
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"
    format_plugin: str = "plain"
    # ATTENTION: high risk of leaking secrets to the users, only use in dev environments!
    debug_mode: bool = False

    # Absolute filesystem path inside the container. The specs file is static:
    # changing it requires a pod restart. Repository, authentication (OIDC)
    # and CORS configuration live inside the specs file (`repo.*`, `auth.*`).
    specs: str = "/yac.yml"

    # Upper bound (characters) for a YAML document in a request (`yaml`,
    # `yaml_old`, `yaml_new`, `yaml_base`). The round-trip parser handles
    # about 100 kB per second on the event loop -- and a document is parsed
    # before its data can be validated -- so this bounds how long one request
    # can keep a worker busy. The request body as a whole is limited to four
    # times this plus 64 kB (see app.lib.bodylimit).
    max_yaml_size: int = 65536

    # Seconds the permissions on an entity are reused for the entity list of
    # the same user (while the entity is unchanged), 0 to evaluate the
    # entity-dependent roles on every list request. Only the list is affected:
    # operations on an entity always evaluate the roles anew.
    list_perms_ttl: int = 300

    env: dict = {}  # custom env vars available in props
