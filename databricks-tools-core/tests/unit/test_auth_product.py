"""The product label must reach the SDK Config, or query history shows client_application 'unknown'."""

from databricks_tools_core import auth
from databricks_tools_core.identity import PRODUCT_NAME, PRODUCT_VERSION


def test_workspace_client_carries_the_product_in_its_user_agent(monkeypatch):
    for k in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_CONFIG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    w = auth.get_workspace_client()
    assert w.config.user_agent.startswith(f"{PRODUCT_NAME}/{PRODUCT_VERSION} ")
