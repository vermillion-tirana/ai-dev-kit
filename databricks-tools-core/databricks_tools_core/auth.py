"""Authentication context for Databricks WorkspaceClient.

Uses Python contextvars to pass authentication through the async call stack
without threading parameters through every function.

All clients are tagged with a custom product identifier and auto-detected
project name so that API calls are attributable in ``system.access.audit``.

Usage in FastAPI:
    # In request handler or middleware
    set_databricks_auth(host, token)
    try:
        # Any code here can call get_workspace_client()
        result = some_databricks_function()
    finally:
        clear_databricks_auth()

Usage in functions:
    from databricks_tools_core.auth import get_workspace_client

    def my_function():
        client = get_workspace_client()  # Uses context auth or env vars
        # ...
"""

import logging
import os
from contextvars import ContextVar
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

from .identity import PRODUCT_NAME, PRODUCT_VERSION, tag_client

logger = logging.getLogger(__name__)

# Cached current username — only fetched once per process
_current_username: Optional[str] = None
_current_username_fetched: bool = False

# How long the SDK may keep RETRYING a request before giving up.
#
# The SDK default is 300s (5 minutes). That is the wrong shape for an MCP server:
# these processes are long-lived and hold pooled HTTP connections, so after a
# laptop sleep or a network change a control-plane call that takes ~0.2s from a
# fresh CLI process can block on a dead socket for the full five minutes. The
# caller sees an opaque stall, not a failure. 60s fails fast enough to be
# legible while still absorbing genuine transient throttling.
#
# Deliberately NOT setting `http_timeout_seconds`: that caps EVERY request,
# including volume uploads (`upload_to_volume`), where a large file legitimately
# takes minutes. Bounding retries fixes the hang without breaking slow-but-healthy
# transfers.
#
# Override with DATABRICKS_RETRY_TIMEOUT_SECONDS (the SDK exposes no env var for
# this — `retry_timeout_seconds` is a bare ConfigAttribute() with no `env=`).
_DEFAULT_RETRY_TIMEOUT_SECONDS = 60


def _retry_timeout_seconds() -> int:
    """Retry budget for SDK calls, from env or the module default.

    A non-numeric or non-positive value falls back to the default rather than
    raising — a malformed env var must not make the client unconstructible.
    """
    raw = os.environ.get("DATABRICKS_RETRY_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_RETRY_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric DATABRICKS_RETRY_TIMEOUT_SECONDS=%r; using %ds",
            raw,
            _DEFAULT_RETRY_TIMEOUT_SECONDS,
        )
        return _DEFAULT_RETRY_TIMEOUT_SECONDS
    if value <= 0:
        logger.warning(
            "Ignoring non-positive DATABRICKS_RETRY_TIMEOUT_SECONDS=%d; using %ds",
            value,
            _DEFAULT_RETRY_TIMEOUT_SECONDS,
        )
        return _DEFAULT_RETRY_TIMEOUT_SECONDS
    return value


def _has_oauth_credentials() -> bool:
    """Check if OAuth credentials (SP) are configured in environment."""
    return bool(os.environ.get("DATABRICKS_CLIENT_ID") and os.environ.get("DATABRICKS_CLIENT_SECRET"))


# Context variables for per-request authentication
_host_ctx: ContextVar[Optional[str]] = ContextVar("databricks_host", default=None)
_token_ctx: ContextVar[Optional[str]] = ContextVar("databricks_token", default=None)


def set_databricks_auth(host: Optional[str], token: Optional[str]) -> None:
    """Set Databricks authentication for the current async context.

    Call this at the start of a request to set per-user credentials.
    The credentials will be used by all get_workspace_client() calls
    within this async context.

    Args:
        host: Databricks workspace URL (e.g., https://xxx.cloud.databricks.com)
        token: Databricks access token
    """
    _host_ctx.set(host)
    _token_ctx.set(token)


def clear_databricks_auth() -> None:
    """Clear Databricks authentication from the current context.

    Call this at the end of a request to clean up.
    """
    _host_ctx.set(None)
    _token_ctx.set(None)


def get_workspace_client() -> WorkspaceClient:
    """Get a WorkspaceClient using context auth or environment variables.

    Authentication priority:
    1. If OAuth credentials exist in env, use explicit OAuth M2M auth (Databricks Apps)
       - This explicitly sets auth_type to prevent conflicts with other auth methods
    2. Context variables with explicit token (PAT auth for development)
    3. Fall back to default authentication (env vars, config file)

    Returns:
        Configured WorkspaceClient instance
    """
    host = _host_ctx.get()
    token = _token_ctx.get()

    # Product identification in the user-agent (so calls are attributable in
    # system.access.audit) is passed explicitly at each construction site rather
    # than splatted from a dict — a `**dict[str, str]` splat makes every
    # WorkspaceClient parameter look str-typed to a type checker.

    # Bound the retry budget on every client (see _DEFAULT_RETRY_TIMEOUT_SECONDS).
    # `retry_timeout_seconds` is not a WorkspaceClient kwarg — it only lands via a
    # Config. Config() with no auth args still performs the SDK's normal
    # resolution chain (env vars, DATABRICKS_CONFIG_PROFILE, ~/.databrickscfg),
    # so the default branch below keeps working exactly as before.
    retry_timeout = _retry_timeout_seconds()

    # In Databricks Apps (OAuth credentials in env), explicitly use OAuth M2M
    # This prevents the SDK from detecting other auth methods like PAT or config file
    if _has_oauth_credentials():
        oauth_host = host or os.environ.get("DATABRICKS_HOST", "")
        client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
        client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")

        # Explicitly configure OAuth M2M to prevent auth conflicts
        return tag_client(
            WorkspaceClient(
                config=Config(
                    host=oauth_host,
                    client_id=client_id,
                    client_secret=client_secret,
                    retry_timeout_seconds=retry_timeout,
                ),
                product=PRODUCT_NAME,
                product_version=PRODUCT_VERSION,
            )
        )

    # Development mode: use explicit token if provided
    if host and token:
        return tag_client(
            WorkspaceClient(
                config=Config(host=host, token=token, retry_timeout_seconds=retry_timeout),
                product=PRODUCT_NAME,
                product_version=PRODUCT_VERSION,
            )
        )

    if host:
        return tag_client(
            WorkspaceClient(
                config=Config(host=host, retry_timeout_seconds=retry_timeout),
                product=PRODUCT_NAME,
                product_version=PRODUCT_VERSION,
            )
        )

    # Fall back to default authentication (env vars, config file)
    return tag_client(
        WorkspaceClient(
            config=Config(retry_timeout_seconds=retry_timeout),
            product=PRODUCT_NAME,
            product_version=PRODUCT_VERSION,
        )
    )


def get_current_username() -> Optional[str]:
    """Get the current authenticated user's username (email).

    Cached after first successful call — the authenticated user doesn't
    change mid-session. Returns None if the API call fails, allowing
    callers to degrade gracefully (e.g., skip user-based filtering).

    Returns:
        Username string (typically an email), or None on failure.
    """
    global _current_username, _current_username_fetched
    if _current_username_fetched:
        return _current_username
    try:
        w = get_workspace_client()
        _current_username = w.current_user.me().user_name
        _current_username_fetched = True
        return _current_username
    except Exception as e:
        logger.debug(f"Failed to fetch current username: {e}")
        _current_username_fetched = True
        return None
