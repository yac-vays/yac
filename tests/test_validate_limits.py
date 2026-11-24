"""/validate reports a limit that cannot be evaluated as a validation result."""

import copy

from app.lib import specs as specs_lib

from tests.test_routers import RAW_SPECS
from tests.test_routers import client  # noqa: F401
from tests.test_routers import login  # noqa: F401
from tests.test_routers import repo_session  # noqa: F401


async def test_unevaluable_limit_is_reported_not_raised(monkeypatch, client, login):  # noqa: F811
    raw = copy.deepcopy(RAW_SPECS)
    raw["types"][0]["limits"] = [{"title": "cpus", "max": "new.data.cpus.total"}]
    monkeypatch.setattr(specs_lib, "_RAW_DATA", raw)

    login("alice")
    r = await client.post(
        "/validate",
        json={
            "operation": "create",
            "type": "host",
            "entity": {"name": "new01", "yaml": "owner: alice\n"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request"]["valid"] is False
    assert 'Limit "cpus"' in body["request"]["message"]
    # the schema is still there for the form
    assert body["schemas"]["json_schema"]
