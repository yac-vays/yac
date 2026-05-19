"""
Raises: []
"""

import logging

from app.model import spc
from app.lib import j2
from app.lib.cache import keyed_alru_cache, stable_key

logger = logging.getLogger(__name__)


_PERM_EXPANSIONS: dict[str, list[str]] = {
    "all": ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act"],
    "adm": ["see", "add", "rnm", "cpy", "lnk", "edt", "cln", "del", "act", "adm"],
    "cln": ["see", "cln"],
    "edt": ["see", "edt"],
    "lnk": ["see", "lnk"],
    "cpy": ["see", "cpy"],
    "rnm": ["see", "rnm"],
    "add": ["see", "add"],
}


def __expand_perms(p: list[str]) -> list[str]:
    parts: list[str] = []
    for q in p:
        parts.extend(q.split("+"))

    expanded: set[str] = set()
    for r in parts:
        expanded.update(_PERM_EXPANSIONS.get(r, [r]))
    return sorted(expanded)


@keyed_alru_cache(
    key_fn=lambda type_name, specs, role_props: (
        type_name,
        specs._signature,
        stable_key(role_props),
    ),
    maxsize=10000,
    ttl=3600,
    copy_result=True,
)
async def get_from_roles(
    type_name: str, specs: spc.Specs, role_props: dict
) -> list[str]:
    """
    Reads the role definitions from specs and renders them with given data and
    request context. If they match, the perms are returned if the role
    definition also matches (including set definition for sets).
    """

    perms = []
    for role in specs.roles:
        for role_def, role_test in dict(role).items():
            logger.debug(f"Extracting perms from role {role_def}")
            role_type_name, set_name, perm = role_def.split(":", maxsplit=2)

            if role_type_name != type_name:
                continue

            if set_name == "all":
                stest = True
            else:
                set_test = getattr(specs.sets, role_type_name, {}).get(
                    set_name, "false"
                )
                try:
                    stest = await j2.render_test(set_test, role_props)
                except j2.J2Error as error:
                    logger.error(
                        f"Set {type_name}.{set_name} could not be rendered:" f" {error}"
                    )
                    stest = False

            if not stest:
                continue

            try:
                rtest = await j2.render_test(role_test, role_props)
            except j2.J2Error as error:
                logger.error(f"Role {role_def} could not be rendered: {error}")
                rtest = False

            if not rtest:
                continue

            perms.append(perm)
    logger.debug(f'Extracted perms: {", ".join(perms)}')
    return __expand_perms(perms)
