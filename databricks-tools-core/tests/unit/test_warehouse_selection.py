"""Unit tests for warehouse selection — pin + denylist (no Databricks needed)."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.sql import State

import databricks_tools_core.sql.warehouse as wh


def _fake(id, name, state=State.STOPPED, creator=None):
    return SimpleNamespace(id=id, name=name, state=state, cluster_size="Small", auto_stop_mins=5, creator_name=creator)


@pytest.fixture(autouse=True)
def _clear_env_and_cache(monkeypatch):
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_DENYLIST", raising=False)
    wh.invalidate_warehouse_cache()
    yield
    wh.invalidate_warehouse_cache()


def test_pin_short_circuits_without_api(monkeypatch):
    """DATABRICKS_WAREHOUSE_ID returns directly — no client call at all."""

    def _boom():
        raise AssertionError("get_workspace_client must not be called when pinned")

    monkeypatch.setattr(wh, "get_workspace_client", _boom)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "cd34914de1d799aa")
    assert wh.get_best_warehouse() == "cd34914de1d799aa"


def test_pin_is_stripped(monkeypatch):
    monkeypatch.setattr(wh, "get_workspace_client", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "  abc123  ")
    assert wh.get_best_warehouse() == "abc123"


def test_denylist_excludes_broken_warehouse(monkeypatch):
    """The broken edp_compute_warehouse is filtered out of auto-select."""
    warehouses = [
        _fake("2af142a22622b7e2", "edp_compute_warehouse", State.RUNNING),  # broken, would win
        _fake("cd34914de1d799aa", "edp_baseline_warehouse_exploration_compute"),
    ]
    monkeypatch.setattr(
        wh, "get_workspace_client", lambda: SimpleNamespace(warehouses=SimpleNamespace(list=lambda: warehouses))
    )
    monkeypatch.setattr(wh, "get_current_username", lambda: None)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_DENYLIST", "2af142a22622b7e2")
    # Without the denylist the RUNNING broken one would be picked; with it, the baseline wins.
    assert wh.get_best_warehouse() == "cd34914de1d799aa"


def test_all_denylisted_returns_none(monkeypatch):
    warehouses = [_fake("2af142a22622b7e2", "edp_compute_warehouse", State.RUNNING)]
    monkeypatch.setattr(
        wh, "get_workspace_client", lambda: SimpleNamespace(warehouses=SimpleNamespace(list=lambda: warehouses))
    )
    monkeypatch.setattr(wh, "get_current_username", lambda: None)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_DENYLIST", "2af142a22622b7e2")
    assert wh.get_best_warehouse() is None
