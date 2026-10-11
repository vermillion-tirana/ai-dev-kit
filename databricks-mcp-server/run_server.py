#!/usr/bin/env python
"""Run the Databricks MCP Server."""

import logging
import os
import sys

if os.environ.get("DATABRICKS_MCP_DEBUG"):
    logging.basicConfig(
        level=logging.DEBUG,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

from databricks_mcp_server.server import mcp

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7108  # registered in ~/.config/ports.json


def main(argv=None):
    """Run over stdio (default) or as a shared HTTP daemon.

    ONE DAEMON PER PROFILE, NOT ONE OVERALL. This codebase backs two configured
    MCP servers that differ ONLY by environment: DATABRICKS_CONFIG_PROFILE
    (smwlw / exploration) and DATABRICKS_WAREHOUSE_ID. get_workspace_client()
    builds a fresh client per call, but the profile resolves through the SDK's
    own chain from PROCESS env — the ContextVars in databricks_tools_core.auth
    are for Databricks Apps, not these tools. So a shared daemon pins whichever
    profile it started with, and serving both needs two daemons on two ports.

    This server holds no per-session state, so one daemon can serve every
    session that uses the same profile. Measured 2026-10-11 with macOS
    `footprint`: each stdio copy is ~120 MB physical footprint (almost all
    swapped-out private Python heap, per `vmmap -summary`), while one HTTP
    daemon is ~81 MB at steady state. One shared daemon therefore replaces
    ~1.7 GB at 14 sessions. (An earlier RSS-based analysis missed the swapped
    pages and wrongly concluded the daemon did not pay off.)

    Operational notes: binds loopback only (it holds workspace credentials),
    and the daemon's working directory matters because tracked resources
    (`.databricks-resources.json`) and project tags resolve relative to it.

    stdio stays the default.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(prog="databricks-mcp")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.environ.get("DATABRICKS_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--host", default=os.environ.get("DATABRICKS_MCP_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DATABRICKS_MCP_PORT", DEFAULT_PORT)),
    )
    args = parser.parse_args(argv)
    if args.transport == "http":
        # Loopback only — this process holds Databricks workspace credentials.
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
