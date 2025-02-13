import logging

from app.lib import locs
from app.model.plg import IJsonSchema

logger = logging.getLogger(__name__)


class YacIfCleanup(IJsonSchema):
    def order(self) -> tuple[bool, int]:
        # Must run after add_consts.py and before yac_optional.py
        return True, 100

    async def process(
        self, loc: str, json_schema: dict, context: dict, props: dict
    ) -> tuple[dict | bool | None, dict]:
        """
        This plugin is the 2nd half of yac_if and has only one purpose. The yac_if
        plugin will remove the "yac_if" key if it validates to true, but keep it
        if it validates to false.

        This prevents the add_consts plugin from adding illegal data back into the
        schema since the schema is still defined for this data.

        Then, after add_consts was executed, this plugin will cleanup all subschemas
        where yac_if validated to false.

        Then, the yac_optional plugin will take care of cleaning up the required list
        if the removed subschema is inside object roperties.
        """
        if "yac_if" in json_schema:
            if json_schema["yac_if"] != False:
                logger.error("There seems to be a bug in the yac_if plugin pair!")
            return None, context

        return json_schema, context


processor = YacIfCleanup()
