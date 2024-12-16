"""
Raises: []
"""

import re

from fastapi import Request

from app import consts
from app.model import inp
from app.model import spc


def __request(request: Request, request_spec: spc.Request) -> dict:
    headers = {}
    for key, spec in request_spec.headers.items():
        value = request.headers.get(f'yac-{key.replace("_","-").lower()}', None)
        if value is not None and re.match(spec.get("pattern", "^$"), value):
            headers[key] = value
        else:
            headers[key] = spec.get("default", "")

    return {
        "ip": request.client.host,
        "headers": headers,
    }


def get_request() -> dict:
    return {
        "env": consts.ENV.env,
    }


def get_types(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "env": consts.ENV.env,
        "request": __request(op.request, request_spec),
        "user": dict(op.user),
    }


def get_action(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "request": __request(op.request, request_spec),
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "old": {
            "name": op.name,
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
        },
    }


def get_log(op: inp.OperationRequest, request_spec: spc.Request) -> dict:
    return {
        "request": __request(op.request, request_spec),
        "user": dict(op.user),
        "old": {
            "name": op.name,
        },
    }


def get_roles(
    op: inp.OperationRequest, request_spec: spc.Request, old_data: None | dict
) -> dict:
    return {
        "env": consts.ENV.env,
        "request": __request(op.request, request_spec),
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "type": op.type_name,
        "old": {
            "name": op.name,
            "data": old_data or {},
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
        },
    }


def get_namegen(
    op: inp.OperationRequest,
    request_spec: spc.Request,
    old_list: list[str],
    new_data: None | dict,
) -> dict:
    return {
        "env": consts.ENV.env,
        "request": __request(op.request, request_spec),
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
    old_perms: list[str],
    new_data: None | dict,
) -> dict:
    return {
        "env": consts.ENV.env,
        "request": __request(op.request, request_spec),
        "user": dict(op.user),
        "operation": op.operation,
        "actions": op.actions,
        "type": op.type_name,
        "old": {
            "name": op.name,
            "data": old_data or {},
            "perms": old_perms,
        },
        "new": {
            "name": None if op.entity is None else op.entity.name,
            "data": new_data or {},
        },
    }
