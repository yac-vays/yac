"""
Type `links`: per-entity j2 URLs, rendered when listing/reading entities.
"""

from app import consts
from app.lib import links
from app.model.int import Entity
from app.model.spc import Type
from app.model.spc import TypeLink

DATA = {"spec": {"customer": "isg", "app": "web", "monitoring": True}}
PROPS = {"env": {}, "request": {"ip": "", "headers": {}}, "user": {}, "type": "t"}


async def _render(*link_specs: TypeLink, data=None) -> dict:
    typ = Type(name="t", title="T", links=list(link_specs))
    entity = Entity(name="isg-web", data=DATA if data is None else data)
    return await links.render(typ, entity, PROPS)


async def test_renders_entity_data_and_name():
    assert await _render(
        TypeLink(
            name="repo",
            title="Repo",
            url="https://git.example.com/{{ old.data.spec.customer }}/{{ old.data.spec.app }}",
        ),
        TypeLink(name="dash", title="Dash", url="https://dash.example.com/{{ name }}"),
    ) == {
        "repo": "https://git.example.com/isg/web",
        "dash": "https://dash.example.com/isg-web",
    }


async def test_empty_or_omit_hides_the_link():
    assert await _render(
        TypeLink(name="a", title="A", url="{{ '' }}"),
        TypeLink(name="b", title="B", url="{{ omit }}"),
        TypeLink(
            name="c",
            title="C",
            url="{{ 'https://x.example.com' if old.data.spec.monitoring else omit }}",
        ),
    ) == {"c": "https://x.example.com"}


async def test_render_error_drops_only_that_link():
    assert await _render(
        TypeLink(name="bad", title="Bad", url="https://x/{{ old.data.nope.deeper }}"),
        TypeLink(name="ok", title="Ok", url="https://x.example.com/{{ name }}"),
    ) == {"ok": "https://x.example.com/isg-web"}


async def test_non_http_urls_are_dropped():
    assert await _render(
        TypeLink(name="js", title="JS", url="javascript:alert(1)"),
        TypeLink(name="rel", title="Rel", url="/relative"),
        TypeLink(name="ok", title="Ok", url="HTTP://x.example.com"),
    ) == {"ok": "HTTP://x.example.com"}


async def test_no_links_or_no_type():
    assert await _render() == {}
    assert await links.render(None, Entity(name="e"), PROPS) == {}


def test_to_public_strips_url_and_keeps_icon():
    typ = Type(
        name="t",
        title="T",
        links=[TypeLink(name="repo", title="Repo", url="https://x/{{ name }}")],
    )
    public = typ.to_public().model_dump()
    assert public["links"] == [
        {"name": "repo", "title": "Repo", "icon": consts.SVG_LINK}
    ]
