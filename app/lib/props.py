"""
Raises: []
"""

import re

from app import consts
from app.model import inp
from app.model import spc


def __request_headers(request_headers: dict, request_spec: spc.Request) -> dict:
    headers = {}
    for key, spec in request_spec.headers.items():
        value = request_headers.get(f'yac-{key.replace("_","-").lower()}', None)
        if value is not None and re.fullmatch(spec.get("pattern", "^$"), value):
            headers[key] = value
        else:
            headers[key] = spec.get("default", "")
    return headers


def get_request() -> dict:
    return {
        "env": consts.ENV.env,
    }


def get_repo() -> dict:
    return {
        "env": consts.ENV.env,
    }


def get_auth() -> dict:
    return {
        "env": consts.ENV.env,
    }


def get_types(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
    }


def get_action(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "old": {
            "name": op.name,
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
        },
        "name": op.entity.name if op.entity and op.entity.name else op.name,
    }


def get_log(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
        "old": {
            "name": op.name,
        },
        "name": op.name,
    }


def get_roles_base(
    op: inp.OperationRequest, request_spec: spc.Request
) -> dict:
    """
    The per-request portion of role props — everything that does not depend
    on the current entity. Callers iterating over many entities can build
    this once and shallow-extend it per entity, avoiding the repeated
    headers regex sweep and user/env dict construction.
    """
    return {
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "type": op.type_name,
    }


def get_roles(
    op: inp.OperationRequest, request_spec: spc.Request, old_data: None | dict
) -> dict:
    return {
        **get_roles_base(op, request_spec),
        "old": {
            "name": op.name,
            "data": old_data or {},
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
        },
        "name": op.entity.name if op.entity and op.entity.name else op.name,
    }


def get_limits_base(
    op: inp.OperationRequest, request_spec: spc.Request, context: dict
) -> dict:
    """
    The per-request portion of limit props — everything that does not depend
    on the entity currently being scanned. The aggregation loop shallow-
    extends this once per scanned entity with its own `old`/`name` keys.

    `new` (the entity being created/changed) is constant across the scan and
    is added by the caller (`lib.limits`) once.
    """
    return {
        "context": context,
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
        "operation": op.operation,
        "type": op.type_name,
    }


def get_namegen(
    op: inp.OperationRequest,
    request_spec: spc.Request,
    old_list: list[str],
    new_data: None | dict,
) -> dict:
    return {
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "old": {"list": old_list},
        "new": {"data": new_data or {}},
    }


def get_schema(
    op: inp.OperationRequest,
    request_spec: spc.Request,
    old_data: None | dict,
    perms: list[str],
    new_data: None | dict,
    context: dict,
) -> dict:
    return {
        "context": context,
        "env": consts.ENV.env,
        "request": {
            "ip": op.request_ip,
            "headers": __request_headers(op.request_headers, request_spec),
        },
        "user": {**dict(op.user), "perms": perms},
        "operation": op.operation,
        "actions": op.actions,
        "type": op.type_name,
        "old": {
            "name": op.name,
            "data": old_data or {},
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
            "data": new_data or {},
        },
        "name": op.entity.name if op.entity and op.entity.name else op.name,
    }
