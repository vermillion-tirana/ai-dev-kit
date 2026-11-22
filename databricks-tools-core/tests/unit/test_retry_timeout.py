"""Tests for the bounded SDK retry budget on WorkspaceClient construction.

Why this exists: the SDK's default retry budget is 300s. In a long-lived MCP
server holding pooled HTTP connections, a control-plane call that takes ~0.2s
from a fresh CLI process can block on a dead socket for the full five minutes
after a laptop sleep or network change — surfacing as an opaque stall rather
than a failure.

These tests are deliberately **hermetic**: they patch out `WorkspaceClient` and
`Config` and assert on the kwargs handed to them. An earlier draft constructed
real clients with a fake token and hung for minutes doing auth resolution — a
unit test must never touch the network. (That it hung on a retry loop is, in
fairness, a decent argument for the bound this module adds.)
"""

import pytest

from databricks_tools_core import auth as auth_mod
from databricks_tools_core.auth import (
    _DEFAULT_RETRY_TIMEOUT_SECONDS,
    _retry_timeout_seconds,
    get_workspace_client,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neutralise ambient auth/env so no test picks up a real profile."""
    for var in (
        "DATABRICKS_RETRY_TIMEOUT_SECONDS",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def captured(monkeypatch):
    """Patch Config + WorkspaceClient; return the kwargs each was handed.

    Fully offline — no Config is really constructed, so no auth resolution and
    no sockets.
    """
    seen: dict[str, dict | None] = {"config_kwargs": None, "client_kwargs": None}

    class _FakeConfig:
        def __init__(self, **kwargs):
            seen["config_kwargs"] = kwargs

    class _FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

    monkeypatch.setattr(auth_mod, "Config", _FakeConfig)
    monkeypatch.setattr(auth_mod, "WorkspaceClient", _FakeClient)
    monkeypatch.setattr(auth_mod, "tag_client", lambda c: c)
    return seen


# --- the budget resolver (pure) ------------------------------------------


def test_default_when_env_unset():
    assert _retry_timeout_seconds() == _DEFAULT_RETRY_TIMEOUT_SECONDS


def test_default_is_well_under_the_sdk_300s():
    """The whole point is failing fast — guard against someone raising it back."""
    assert _DEFAULT_RETRY_TIMEOUT_SECONDS < 300


def test_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("DATABRICKS_RETRY_TIMEOUT_SECONDS", "17")
    assert _retry_timeout_seconds() == 17


def test_env_override_tolerates_whitespace(monkeypatch):
    monkeypatch.setenv("DATABRICKS_RETRY_TIMEOUT_SECONDS", "  25  ")
    assert _retry_timeout_seconds() == 25


@pytest.mark.parametrize("bad", ["abc", "   ", "12.5", "-5", "0"])
def test_malformed_env_falls_back_rather_than_raising(monkeypatch, bad):
    """A typo must not make get_workspace_client() unconstructible."""
    monkeypatch.setenv("DATABRICKS_RETRY_TIMEOUT_SECONDS", bad)
    assert _retry_timeout_seconds() == _DEFAULT_RETRY_TIMEOUT_SECONDS


# --- the bound reaches the Config on every auth branch -------------------


def test_default_branch_is_bounded(captured):
    """No host/token in context — the branch both MCP servers actually take."""
    get_workspace_client()
    assert captured["config_kwargs"]["retry_timeout_seconds"] == _DEFAULT_RETRY_TIMEOUT_SECONDS


def test_default_branch_honours_env_override(captured, monkeypatch):
    monkeypatch.setenv("DATABRICKS_RETRY_TIMEOUT_SECONDS", "90")
    get_workspace_client()
    assert captured["config_kwargs"]["retry_timeout_seconds"] == 90


def test_host_token_branch_is_bounded(captured):
    auth_mod.set_databricks_auth("https://example.cloud.databricks.com", "dapi-fake")
    try:
        get_workspace_client()
    finally:
        auth_mod.clear_databricks_auth()
    assert captured["config_kwargs"]["retry_timeout_seconds"] == _DEFAULT_RETRY_TIMEOUT_SECONDS
    assert captured["config_kwargs"]["token"] == "dapi-fake"


def test_host_only_branch_is_bounded(captured):
    auth_mod.set_databricks_auth("https://example.cloud.databricks.com", None)
    try:
        get_workspace_client()
    finally:
        auth_mod.clear_databricks_auth()
    assert captured["config_kwargs"]["retry_timeout_seconds"] == _DEFAULT_RETRY_TIMEOUT_SECONDS


def test_oauth_branch_is_bounded(captured, monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "cid")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    get_workspace_client()
    assert captured["config_kwargs"]["retry_timeout_seconds"] == _DEFAULT_RETRY_TIMEOUT_SECONDS
    assert captured["config_kwargs"]["client_id"] == "cid"


# --- the two properties that make the change safe ------------------------


def test_http_timeout_is_never_set(captured):
    """Regression guard: capping http_timeout would break large volume uploads.

    `upload_to_volume` is ~331 of our calls and a big file legitimately takes
    minutes. Bounding *retries* fixes the hang; bounding every *request* would
    introduce a new failure mode. If someone later sets http_timeout_seconds
    here, this test should make them justify it.
    """
    get_workspace_client()
    assert "http_timeout_seconds" not in captured["config_kwargs"]


def test_product_identity_still_tagged(captured):
    """Product identity is what makes calls attributable (system.access.audit,
    query history client_application). It must go on the Config: the SDK reads
    WorkspaceClient(product=...) only when it builds the Config itself, so next
    to config= it was dropped and every statement showed 'unknown'.
    """
    get_workspace_client()
    assert captured["config_kwargs"]["product"]
    assert captured["config_kwargs"]["product_version"]
    assert "product" not in captured["client_kwargs"]
