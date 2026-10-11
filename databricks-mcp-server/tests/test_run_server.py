"""Tests for run_server.py defaults."""

import importlib.util
from pathlib import Path


def _load_run_server():
    path = Path(__file__).resolve().parent.parent / "run_server.py"
    spec = importlib.util.spec_from_file_location("run_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_port_is_7108():
    assert _load_run_server().DEFAULT_PORT == 7108


def test_default_host_is_loopback():
    assert _load_run_server().DEFAULT_HOST == "127.0.0.1"


# --- DNS-rebinding guard (HTTP mode) -----------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

PORT = 7977
_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
_ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def client():
    mod = _load_run_server()
    app = mod.mcp.http_app(middleware=[Middleware(mod.LoopbackOnly, port=PORT)])
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def _post(client, **headers):
    return client.post("/mcp", json=_INIT, headers={**_ACCEPT, **headers})


def test_evil_host_is_421(client):
    assert _post(client, Host="evil.example").status_code == 421


def test_wrong_port_host_is_421(client):
    assert _post(client, Host="127.0.0.1:9999").status_code == 421


def test_evil_origin_is_403(client):
    assert _post(client, Origin="http://evil.example").status_code == 403


def test_no_origin_initializes(client):
    r = _post(client)
    assert r.status_code == 200
    assert "serverInfo" in r.text


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_loopback_host_and_origin_ok(client, host):
    r = _post(client, Host=f"{host}:{PORT}", Origin=f"http://{host}:{PORT}")
    assert r.status_code == 200
    assert "serverInfo" in r.text


def test_main_http_installs_guard(monkeypatch):
    mod = _load_run_server()
    seen = {}
    monkeypatch.setattr(mod.mcp, "run", lambda **kw: seen.update(kw))
    mod.main(["--transport", "http", "--port", "7977"])
    assert seen["transport"] == "http" and seen["port"] == 7977
    assert any(m.cls is mod.LoopbackOnly for m in seen["middleware"])
