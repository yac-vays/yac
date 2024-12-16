import logging
from typing import Literal

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="yac_", env_nested_delimiter="__")

    root_path: str = "/"
    cors_origins: str = "https://localhost"  # comma separated string-list
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"
    # ATTENTION: high risk of leaking secrets to the users, only use in dev environments!
    debug_mode: bool = False

    repo_plugin: str = "git_direct"
    repo: dict = {}  # repo_plugin env vars, see app/plugin/repo/*.py

    oidc_url: str = "https://localhost/.well-known/openid-configuration"
    # comma separated list of accepted client_ids (first one will be used in Swagger UI)
    oidc_client_ids: str = ""

    # available format vars: all values from the JWT ID-Token
    oidc_jwt_name: str = "{name}"
    oidc_jwt_full_name: str = "{givenName} {surname}"
    oidc_jwt_full_name_fallback: str = "{name}"
    oidc_jwt_email: str = "{mail}"
    oidc_jwt_email_fallback: str = "{name}@localhost"

    # a . at the beginning means inside the repo (support depends on repo_plugin)
    specs: str = "./yac.yml"

    env: dict = {}  # custom env vars available in props
