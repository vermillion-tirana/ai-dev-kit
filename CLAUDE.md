# ai-dev-kit — VENDORED. Do not rebase onto upstream.

This is a **fork of `databricks-solutions/ai-dev-kit` that has been deliberately
frozen and is treated as vendored code we own.** `origin` is
`vermillion-tirana/ai-dev-kit`.

> **The `upstream` remote was removed on 2026-07-28. Do not re-add it, and do not
> `git merge upstream/main`.** It was removed because it is a loaded gun: at the
> time of removal this fork was **219 commits behind** an actively-developed
> upstream (which had shipped the day before), and merging that churn to benefit
> six tools is a bad trade. If a future session "helpfully" re-adds the remote and
> rebases, it will pull hundreds of commits of unrelated work — Builder App
> refactors, install-script changes, skills — into code James depends on for a
> narrow, well-understood slice.

## Why this fork exists at all

Five local commits, all authored here, none of them upstream:

| commit | what |
|---|---|
| `d851058` | warehouse cache + batch SQL — cuts ad-hoc query cost (#1) |
| `462fc14` | serverless-first warnings on cluster tool docstrings (#2) |
| `7f6e4f9` | `sample_size` on `execute_sql_multi` so it returns >5 rows (#3) |
| `1006fe7` | **`DATABRICKS_WAREHOUSE_ID` pin + denylist** — stops auto-select grabbing a broken warehouse (#4) |
| `d84b30c` | gitignore the harness directory |

`execute_sql_batch` / `execute_sql_multi` are **our additions** — upstream has no
equivalent, and they account for ~530 of our tool calls. That alone means upstream
can never be a drop-in replacement.

## What we actually use — the real dependency surface

Measured over 5,220 session transcripts (**6,272 calls**):

| tool | calls | share |
|---|---|---|
| `execute_sql` | 4,663 | 74% |
| `upload_to_volume` | 331 | |
| `manage_job_runs` | 295 | |
| `execute_sql_multi` | 288 | |
| `execute_sql_batch` | 242 | |
| `list_volume_files` | 234 | |

**Six tools = 96.5% of all usage. 25 tools have ever been called. 74 of the 99
registered tools have never been called once.**

The logic behind them is thin: `SQLExecutor`
(`databricks-tools-core/databricks_tools_core/sql/sql_utils/executor.py`) is 176
lines — submit statement, poll every 2s, extract results, cancel on timeout.
Volume and job tools are near-passthrough to the Databricks SDK.

## How it's wired in

The **only** hook into James's setup is two MCP server entries in `~/.claude.json`
(`databricks-smwlw` and `databricks-exploration`). No skills are installed from
this repo; no other repo imports `databricks_tools_core`. The seam is genuinely
that thin — which is what makes freezing it safe.

Both entries **must** pin a warehouse, or `get_best_warehouse()` does two
control-plane round trips before every query (see Known sharp edges):

```json
"databricks-smwlw":       { "env": { "DATABRICKS_CONFIG_PROFILE": "smwlw",
                                     "DATABRICKS_WAREHOUSE_ID": "027669ff3f537fc4" },
                            "timeout": 120000 }
"databricks-exploration": { "env": { "DATABRICKS_CONFIG_PROFILE": "exploration",
                                     "DATABRICKS_WAREHOUSE_ID": "cd34914de1d799aa" },
                            "timeout": 120000 }
```

Env vars are read at process spawn, so a config change needs an MCP reconnect
(`/mcp`) or a session restart before it takes effect.

## Known sharp edges (unfixed, by choice)

- **`warehouses.list()` has no per-call timeout** — it inherits the SDK's
  5-minute default. Any tool that still resolves a warehouse (`list_warehouses`,
  `get_table_details`, `table_stats`) can therefore hang for five minutes on a
  stale pooled connection rather than failing fast. Pinning
  `DATABRICKS_WAREHOUSE_ID` sidesteps this for `execute_sql` (the pin returns at
  `warehouse.py:136` with **zero** client calls — measured 0.01 ms pinned vs
  283–546 ms unpinned on a healthy network) but does **not** cure the other tools.
- **The warehouse cache TTL is 60s** (`_WAREHOUSE_CACHE_TTL`), shorter than the
  gap between calls in interactive use, so an unpinned server re-resolves
  constantly.
- **Long-lived MCP processes hold pooled HTTP connections** that go stale across
  laptop sleep or network changes. This is why a control-plane call the CLI does
  in 0.2s can block for the full SDK timeout in a server that has been up for
  hours. A `/mcp` reconnect also does not reap the previous server process for
  that session (~6 MB each — noise, not a leak worth chasing).

*Origin: 2026-07-28. A `MERGE` that appeared to time out at 120s had actually
finished server-side in ~20s; the missing time was warehouse discovery, and a
follow-up query died with `Failed to list SQL warehouses: Timed out after
0:05:00`. Fixed by pinning, not by patching upstream.*

## If this ever needs replacing

**Don't write a new server — revive the one that already exists.**
`~/code/work/databricks-mcp-server` (`github.com/jdowzard/databricks-mcp-server`)
is a self-owned 4,127-LOC / 47-tool server covering SQL, UC volumes, jobs,
catalog, clusters, apps and vector search. Verified 2026-07-28: **it still imports
cleanly against the current Databricks SDK.**

Gaps to port before it could take over:

- `execute_sql_multi` / `execute_sql_batch` (our additions here — 530 calls)
- volume **writes** — its `tools/files.py` is read-only (`list_volume_files`,
  `get_file_metadata`); it has no `upload_to_volume` / `download_from_volume` /
  `create_volume_directory`

**Trigger to do it:** the next time an ai-dev-kit-shaped bug costs real time. Not
before — as of 2026-07-28 the acute problem is fixed and a rewrite would be
optimising the wrong thing.
