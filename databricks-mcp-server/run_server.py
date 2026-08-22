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
DEFAULT_PORT = 7103  # registered in ~/.config/ports.json


def main(argv=None):
    """Run over stdio (default) or as a shared HTTP daemon.

    ONE DAEMON PER PROFILE, NOT ONE OVERALL. This codebase backs two configured
    MCP servers that differ ONLY by environment: DATABRICKS_CONFIG_PROFILE
    (smwlw / exploration) and DATABRICKS_WAREHOUSE_ID. get_workspace_client()
    builds a fresh client per call, but the profile resolves through the SDK's
    own chain from PROCESS env — the ContextVars in databricks_tools_core.auth
    are for Databricks Apps, not these tools. So a shared daemon pins whichever
    profile it started with, and serving both needs two daemons on two ports.

    MEASURED, AND THE ANSWER IS: DO NOT DEPLOY THIS AT NORMAL LOAD.

    This server is not m365. The Databricks SDK is imported inside tool
    functions rather than at module scope, so an idle stdio server never pays
    for it — 4.3 MB each, measured across 22 live processes. A daemon pays for
    it permanently, and grows as it serves:

        idle                 21.0 MB
        after tools/list     57.7 MB
        after one SDK call   80.7 MB   <- steady state

    One daemon at steady state (80.7 MB) nearly costs what ALL 22 stdio
    processes cost together (94.4 MB), and two are required because the two
    configured servers differ only by process env. So:

        10 sessions   stdio  94.6 MB  vs daemons 161.4 MB   COSTS  66.8 MB
        25 sessions   stdio 236.5 MB  vs daemons 161.4 MB   saves  75.1 MB

    Break-even is ~19 sessions PER PROFILE. The observed peak is 25 sessions
    total, so at typical load (10) this is a 67 MB regression and only at an
    unusual peak does it pay back.

    The flag is kept because it is free — stdio stays the default, nothing
    changes unless someone passes --transport http — and because the numbers
    above are the reason not to, which is worth being able to re-check rather
    than re-derive. If sustained session counts ever rise past ~40, re-measure
    and revisit.

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
