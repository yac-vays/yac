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

    env: dict = {}  # custom env vars available in props
