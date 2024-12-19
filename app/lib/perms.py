"""
Raises: []
"""

import logging

from app.model import spc
from app.model import inp
from app.lib import props
from app.lib import j2

logger = logging.getLogger(__name__)


def __expand_perms(p: list[str]) -> list[str]:
    res = []
    _ = [res.extend(q.split("+")) for q in p]

    result = []
    for r in res:
        if r == "all":
            result.extend(
                ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act"]
            )
        elif r == "adm":
            result.extend(
                ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act", "adm"]
            )
        elif r == "cln":
            result.extend(["see", "cln"])
        elif r == "edt":
            result.extend(["see", "edt"])
        elif r == "lnk":
            result.extend(["see", "lnk"])
        elif r == "cpy":
            result.extend(["see", "cpy"])
        elif r == "rnm":
            result.extend(["see", "rnm"])
        elif r == "add":
            result.extend(["see", "add"])
        else:
            result.append(r)

    return sorted(list(set(result)))


async def get_from_roles(
    op: inp.OperationRequest, specs: spc.Specs, old_data: dict, new_name: bool = False
) -> list[str]:
    """
    Reads the role definitions from specs and renders them with given data and
    request context. If they match, the perms are returned if the role
    definition also matches (including set definition for sets).
    """
    name = getattr(op.entity, "name", None) if new_name else op.name
    role_props = props.get_roles(op, specs.request, old_data)
    perms = []
    for role in specs.roles:
        for role_def, role_test in dict(role).items():
            try:
                rtest = await j2.render_test(role_test, role_props)
            except j2.J2Error as error:
                logger.error(f"Role {role_def} could not be rendered: {error}")
                rtest = False
            if rtest:
                logger.debug(f"Extracting perms from role {role_def}")
                if role_def.startswith(f"all:{op.type_name}:"):
                    perms.append(role_def.split(":", 2)[2])
                elif role_def.startswith(f"set:{op.type_name}:"):
                    _, _, set_name, perm = role_def.split(":", 3)
                    set_test = getattr(specs.sets, op.type_name, {}).get(
                        set_name, "false"
                    )
                    try:
                        stest = await j2.render_test(set_test, role_props)
                    except j2.J2Error as error:
                        logger.error(
                            f"Set {op.type_name}.{set_name} could not be rendered: {error}"
                        )
                        stest = False
                    if stest:
                        perms.append(perm)
                elif name is not None and role_def.startswith(
                    f"{op.type_name}:{name}:"
                ):
                    perms.append(role_def.split(":", 2)[2])
    logger.debug(f'Extracted perms: {", ".join(perms)}')
    return __expand_perms(perms)
