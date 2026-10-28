"""
Raises: []

Renders the `links` of a type per entity. A link is optional decoration of the
entity list, so a failing or invalid link is logged and left out instead of
failing the whole request.
"""

import logging
import re

from app import consts
from app.lib import j2
from app.model import spc
from app.model.int import Entity

logger = logging.getLogger(__name__)

# Only plain web links: anything else (e.g. `javascript:`) would end up as the
# `href` of a link in VAYS.
_URL_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)


async def render(
    type_spec: spc.Type | None, entity: Entity, base_props: dict
) -> dict[str, str]:
    if type_spec is None or not type_spec.links:
        return {}

    link_props = base_props | {
        "old": {"name": entity.name, "data": entity.data or {}},
        "name": entity.name,
    }
    links: dict[str, str] = {}
    for link in type_spec.links:
        loc = f"#/types/{type_spec.name}/links/{link.name}/url"
        try:
            url = await j2.render_str(link.url, link_props, loc=loc)
        except j2.J2Error as error:
            logger.warning("Link of entity '%s': %s", entity.name, error)
            continue
        if url is None or url == consts.OMIT or url == "":
            continue
        url = str(url).strip()
        if not _URL_PATTERN.match(url):
            logger.warning(
                "Link '%s' of entity '%s' is no http(s) URL: %s",
                link.name, entity.name, url,
            )
            continue
        links[link.name] = url
    return links
