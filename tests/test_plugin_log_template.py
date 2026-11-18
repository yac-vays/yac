"""
Tests for the `template` log plugin -- one entry (or none) and a content
document rendered from the details, with the entity data as `old.data`.
"""

import pytest

from app.lib import plugin
from app.model.err import LogContentNotFound
from app.model.err import LogSpecsError

PROPS = {
    "name": "bucket1",
    "env": {"endpoint": "https://s3.example.com"},
    "old": {"name": "bucket1", "data": {"quota": 0}},
}

DETAILS = {
    "content": "## Bucket `{{ name }}`\n\nEndpoint: {{ env.endpoint }}\n",
    "message": "How to connect",
    "problem": "{{ old.data.quota == 0 }}",
}


@pytest.fixture
def tpl():
    return plugin.get_module("log", "template").log


async def test_one_entry_with_content(tpl):
    logs = await tpl.get("howto", details=DETAILS, props=PROPS)
    assert len(logs) == 1
    entry = logs[0]
    assert entry.name == "howto"
    assert entry.message == "How to connect"
    assert entry.time == ""
    assert entry.has_content is True
    assert entry.problem is True
    assert entry.progress is None


async def test_content_is_rendered(tpl):
    c = await tpl.content("howto", details=DETAILS, props=PROPS)
    assert c.name == "howto"
    assert c.content == "## Bucket `bucket1`\n\nEndpoint: https://s3.example.com"


async def test_empty_content_means_no_entry(tpl):
    details = {"content": "{% if old.data.quota > 0 %}has quota{% endif %}"}
    assert await tpl.get("howto", details=details, props=PROPS) == []
    with pytest.raises(LogContentNotFound):
        await tpl.content("howto", details=details, props=PROPS)


async def test_progress_is_clamped(tpl):
    logs = await tpl.get("howto", details={"content": "x", "progress": "{{ 150 }}"}, props=PROPS)
    assert logs[0].progress == 100


@pytest.mark.parametrize(
    "details",
    [
        {"message": "no content"},
        {"content": "x", "problem": "{{ 'not a bool' }}"},
        {"content": "x", "progress": "{{ 'nan' }}"},
        {"content": "{{ undefined_function() }}"},
    ],
)
async def test_bad_details_are_specs_errors(tpl, details):
    with pytest.raises(LogSpecsError):
        await tpl.get("howto", details=details, props=PROPS)
