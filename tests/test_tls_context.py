"""
The TLS context of the HTTP plugins (`ssl_verify`) is built once per value:
loading the CA bundle for every request blocks the event loop far longer
than the request takes.
"""

import ssl

import httpx
import pytest

from app.lib import plugin
from app.lib import tls


def test_context_is_built_once_per_value():
    verified = tls.context(True)
    assert isinstance(verified, ssl.SSLContext)
    assert verified.verify_mode == ssl.CERT_REQUIRED and verified.check_hostname
    assert tls.context(True) is verified

    unverified = tls.context(False)
    assert unverified is not verified
    assert unverified.verify_mode == ssl.CERT_NONE
    assert tls.context(False) is unverified


def test_bad_values_raise_what_the_plugins_expect(tmp_path):
    with pytest.raises(OSError):
        tls.context(str(tmp_path / "missing.pem"))
    with pytest.raises(TypeError):
        tls.context(["not", "hashable"])


@pytest.mark.parametrize("kind, name", [("log", "http"), ("log", "elastic"), ("action", "http")])
async def test_plugins_hand_the_shared_context_to_their_client(monkeypatch, kind, name):
    mod = plugin.get_module(kind, name)
    seen = []
    real = httpx.AsyncClient

    def answer(request):
        del request
        return httpx.Response(200, json={"hits": {"events": []}} if name == "elastic" else [])

    def client(**kwargs):
        seen.append(kwargs.pop("verify"))
        return real(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", client)
    details = {"url": "https://example.org/x"}
    for _ in range(2):
        if kind == "log":
            await mod.log.get("install", details=details, props={})
        else:
            await mod.action.run(details=details, props={})
    assert seen == [tls.context(True)] * 2
