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
