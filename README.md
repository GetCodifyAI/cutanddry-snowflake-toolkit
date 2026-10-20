# cutanddry-snowflake-toolkit

Read-only Python scripts for browsing, querying, and exploring the structure
of a Snowflake database — table/view listing, row sampling, column
definitions, full DDL, and name-based search — all from your own terminal.

This repo can also be used as a **Claude Skill** (see [Using this with Claude](#using-this-with-claude) below), so Claude can hand you the exact command to run and interpret the results you paste back.

## Project Setup

### Add .env file to the project

1. Copy the template:
   ```
   cp scripts/.env.template scripts/.env
   ```
2. Open `scripts/.env` and fill in your real values:

   | Variable | Required | Notes |
   |---|---|---|
   | `SNOWFLAKE_ACCOUNT` | yes | your account identifier, e.g. `xy12345.us-east-1` or `orgname-accountname` |
   | `SNOWFLAKE_USER` | yes | your Snowflake username |
   | `SNOWFLAKE_PASSWORD` | yes | your Snowflake password |
   | `SNOWFLAKE_WAREHOUSE` | yes | the warehouse to run queries on |
   | `SNOWFLAKE_ROLE` | no | ideally a role granted only `SELECT`/`USAGE` privileges |
   | `SNOWFLAKE_DATABASE` | yes* | the database every script works in. *Only `test_connection.py` runs without it |
   | `SNOWFLAKE_SCHEMA` | no | optional default schema |

3. `scripts/.env` is already listed in `.gitignore` — it never gets committed. Never share this file or paste its contents anywhere public.

### Install dependencies

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On macOS with a Homebrew-managed Python, a plain `pip install` outside a venv
will fail with `externally-managed-environment` — the venv above is the fix.

### Verify your connection

```
cd scripts
python test_connection.py
```

This prints your current user/role/warehouse/account/version and lists every
database you can see. If this works, everything else will too.

### Run from anywhere (optional)

Add this to `~/.zshrc` (or `~/.bashrc`), with `SF_HOME` set to where you
cloned this repo, then open a new terminal:

```
SF_HOME=~/path/to/cutanddry-snowflake-toolkit
sf() { "$SF_HOME"/venv/bin/python "$SF_HOME"/scripts/"$1".py "${@:2}"; }
```

Now `sf <script name without .py> [arguments]` works from any folder, with
no `cd scripts` and no `source venv/bin/activate` — e.g.
`sf table_access JIRA.ISSUES` or `sf storage_usage --by database`. It calls
the venv's Python directly, and `sf_conn.py` finds `scripts/.env` on its
own. Relative paths you pass, like `--output out.csv` or
`run_query.py --file report.sql`, are relative to the folder you're in.

## Scripts

Run all of these from inside the `scripts/` folder (with the venv activated).

| Script | What it does | Example |
|---|---|---|
| `test_connection.py` | Sanity-checks your `.env` and credentials | `python test_connection.py` |
| `list_tables.py` | Lists every table + view in the configured database, sorted by schema, with kind/row count/size/created/last_altered | `python list_tables.py` or `python list_tables.py --schema PUBLIC` |
| `search_tables.py` | Finds tables/views by partial name match across every schema | `python search_tables.py monday` |
| `search_columns.py` | Finds every table/view that has a column matching a partial name | `python search_columns.py email` |
| `query_table.py` | Samples rows from a table/view, with readable formatting (commas, MM/DD/YY dates, one-line JSON) and a note when more rows exist | `python query_table.py PUBLIC.ORDERS --limit 25` |
| `describe_table.py` | Shows full column definitions (name, type, nullable, default, comment) plus table metadata (row count, size, timestamps) | `python describe_table.py PUBLIC.ORDERS` |
| `profile_table.py` | Profiles every column: null count/%, distinct count, min/max | `python profile_table.py PUBLIC.ORDERS` or `python profile_table.py PUBLIC.ORDERS --sample 100000` |
| `top_values.py` | Most frequent values in one column, with count, % and cumulative % (NULL counted as a value) | `python top_values.py PUBLIC.ORDERS STATUS` or `python top_values.py PUBLIC.ORDERS STATUS --limit 50` |
| `find_duplicates.py` | Checks whether a column (or combination of columns) is unique: how many keys repeat, extra rows they add, and the most-repeated keys | `python find_duplicates.py PUBLIC.ORDERS ORDER_ID` or `python find_duplicates.py PUBLIC.ORDERS ORDER_ID LINE_NO` |
| `query_history.py` | Recent queries (last 7 days max): start time, user, warehouse, status, seconds, rows, query text. Defaults to your own queries, past 24 hours | `python query_history.py --failed` or `python query_history.py --slowest --hours 72` |
| `warehouse_usage.py` | Credits per warehouse for the last N days vs the N before (change, share, cloud services), or per day/week/month with `--by`; `--price` adds dollars. Needs access to `SNOWFLAKE.ACCOUNT_USAGE` | `python warehouse_usage.py` or `python warehouse_usage.py --by month --days 365 --price 3` |
| `storage_usage.py` | What's taking up storage: each table's live data vs time travel, fail-safe and clone bytes (all billed), biggest first, including dropped tables still billed; `--by database` for the whole account, `--by schema`, `--price` for monthly cost. Needs access to `SNOWFLAKE.ACCOUNT_USAGE` | `python storage_usage.py --by database --price 23` or `python storage_usage.py --database CUT_AND_DRY_UNIFIED` |
| `table_access.py` | Who reads and writes a table/view: per user, reads/writes with last time and roles; without a table, every table/view with the same counts, and `--unused` for ones nobody read. Counts queries whose SQL names the table, from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` | `python table_access.py JIRA.ISSUES` or `python table_access.py --schema JIRA --unused --days 90` |
| `run_query.py` | Runs one custom read-only query (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN only), inline or from a file | `python run_query.py "select count(*) from PUBLIC.ORDERS"` or `python run_query.py --file report.sql` |
| `list_tasks.py` | Lists tasks with state, schedule, warehouse, last run/result, failures over the past 7 days and next run; `--runs` shows one task's runs with errors | `python list_tasks.py --failing` or `python list_tasks.py --runs DTAM.MY_TASK` |
| `compare_counts.py` | Compares row counts of same-named tables/views in two schemas (either can be `DATABASE.SCHEMA`), with difference and % | `python compare_counts.py CUT_AND_DRY_PROD_SCHEMA CUT_AND_DRY_UNIFIED.PRODUCTION --diff-only` |
| `diff_rows.py` | Compares two tables' rows matched on a key (either can be `DATABASE.SCHEMA.TABLE`): rows only in A, only in B, and rows whose values differ, with which columns differ most | `python diff_rows.py CUT_AND_DRY_PRODUCTION_DATABASE.GRAPHP.VERIFIEDVENDOR CUT_AND_DRY_UNIFIED.PRODUCTION.VERIFIEDVENDOR --key ID` |
| `show_grants.py` | Who has access to a table/view: roles and their privileges; `--users` lists the actual users and through which roles | `python show_grants.py PUBLIC.ORDERS` or `python show_grants.py PUBLIC.ORDERS --privilege SELECT --users` |
| `freshness.py` | When data last arrived: newest value, age and rows in the last 24h/7d for each date/timestamp column, plus last write time | `python freshness.py JIRA.ISSUES` or `python freshness.py JIRA.ISSUES UPDATED` |
| `daily_counts.py` | Rows per day (or hour/week/month) by a date/timestamp column, with a trend bar; flags periods with no rows and unusually low ones | `python daily_counts.py JIRA.ISSUES UPDATED` or `python daily_counts.py PUBLIC.ORDERS ORDER_DATE --by month --last 24` |
| `find_usages.py` | Finds views, dynamic tables, tasks, functions and procedures whose SQL mentions a table/view/column name - "what breaks if I change this?" | `python find_usages.py CANONICALPRODUCT_ID`, or `python find_usages.py UPB.ORDERS_FLATTENED --dependencies` for everything in any database that depends on a table, directly or through other objects |
| `get_ddl.py` | Prints the full `CREATE TABLE`/`CREATE VIEW` statement | `python get_ddl.py PUBLIC.ORDERS` |
| `list_dynamic_tables.py` | Lists dynamic tables with target lag, warehouse, refresh mode, scheduling state, row count/size; `--failing` keeps only ones with failed refreshes in the past 7 days; `--refreshes` shows one table's recent refreshes (queue vs run time, rows inserted/deleted, errors) | `python list_dynamic_tables.py --failing` or `python list_dynamic_tables.py --refreshes SUPPLIER_DASHBOARD.ACTIVE_CUSTOMERS_VIEW` |
| `pipe_status.py` | Lists Snowpipes with state (running/paused), files waiting, and files loaded / partially loaded / failed over the past 7 days with the latest error; `--all` covers every database, `--failing` keeps only pipes not running or with bad loads; `--loads` shows one pipe's recent files with errors | `python pipe_status.py --all --failing` or `python pipe_status.py --loads BIRITE.BIRITE_INVOICE_PIPE` |
| `diff_schema.py` | Compares two schemas: tables/views only in one, ones that switched between table and view, and columns added/removed/changed (full type incl. length/precision, and nullability) on tables present in both; either schema can be `DATABASE.SCHEMA` | `python diff_schema.py DTAM_V2 DTAM_V3` or `python diff_schema.py CUT_AND_DRY_PROD_SCHEMA CUT_AND_DRY_UNIFIED.PRODUCTION` |
| `list_schemas.py` | Lists every schema in the database with table/view counts, estimated size, created/last_altered | `python list_schemas.py` |
| `recent_tables.py` | Lists tables/views sorted by last_altered, most recent first - "what changed recently" | `python recent_tables.py --limit 50` or `python recent_tables.py --schema PUBLIC` |

All scripts accept schema/table (and column) names case-insensitively — they resolve the
real stored case internally via `INFORMATION_SCHEMA`, so lowercase or
mixed-case schemas (e.g. Airbyte's `airbyte_internal`) just work without
manual quoting. Views are treated identically to tables throughout. The
one exception is `run_query.py`, which runs your SQL exactly as written:
there, lowercase or mixed-case identifiers need double quotes, as in any
Snowflake query.

`run_query.py` only accepts a single SELECT/WITH/SHOW/DESCRIBE/EXPLAIN
statement and runs it inside a transaction that is always rolled back.
That stops accidental writes, but a SELECT can still call a system
function with side effects, so a role granted only `SELECT`/`USAGE` is
what really keeps it read-only.

### Flags

#### Common flags

- `--output` / `-o <path>` — every script except `test_connection.py`.
  Writes the full result to a file as well as printing it: a `.sql` text
  file for `get_ddl.py`, a CSV for everything else. Where the terminal
  shows only part of the result (`--limit`, long values cut short), the
  file gets all of it. Example:
  `python search_columns.py email --output matches.csv`.
- `--limit` / `-n <N>` — how many rows to show:

  | Script | Limits | Default |
  |---|---|---|
  | `query_table.py` | rows sampled | 10 |
  | `run_query.py` | rows printed (`--output` still gets every row) | 50 |
  | `recent_tables.py` | tables/views shown | 20 |
  | `top_values.py` | top values shown | 20 |
  | `find_duplicates.py` | duplicated keys shown | 20 |
  | `diff_rows.py` | differing rows listed (`--output` gets every one) | 20 |
  | `query_history.py` | queries shown | 20 |
  | `list_tasks.py --runs` | runs shown | 20 |
  | `list_dynamic_tables.py --refreshes` | refreshes shown | 20 |
  | `pipe_status.py --loads` | files shown | 20 |
  | `storage_usage.py` | tables shown (`--output` gets every one) | 20 |

  Example: `python query_table.py PUBLIC.ORDERS --limit 25`.
- `--schema <name>` — scopes the listing or search to one schema
  (case-insensitive): `list_tables.py`, `recent_tables.py`,
  `search_tables.py`, `search_columns.py`, `find_usages.py`,
  `list_tasks.py`, `list_dynamic_tables.py`, `pipe_status.py`,
  `table_access.py` (without a table) and `storage_usage.py` (per-table
  view).
- `--database <name>` — `find_usages.py`, `storage_usage.py` and
  `pipe_status.py`. Looks in
  another database instead of `SNOWFLAKE_DATABASE`.
- `--sample <N>` — `profile_table.py`, `top_values.py` and
  `find_duplicates.py`. Works on a random sample of N rows instead of the
  whole table; useful on very large tables.
- `--ntz-timezone <TZ>` — `freshness.py` and `daily_counts.py`.
  TIMESTAMP_NTZ values have no time zone and are treated as UTC by
  default; pass e.g. `America/Los_Angeles` if yours are stored in local time.

#### Script options

**Look at data**

- `run_query.py` — `--file` / `-f <path>` reads the query from a `.sql`
  file instead of the command line.
- `profile_table.py` — `--exact` gives exact distinct counts instead of
  the default approximate ones (typically within ~1%). Much slower on wide
  tables.
- `daily_counts.py` — `--by hour|day|week|month` (default `day`),
  `--last <N>` (how many periods back, including the current one; defaults
  to 48 hours / 30 days / 12 weeks / 12 months, max 1,000) and
  `--timezone <TZ>` (the zone periods are counted in, default UTC).

**Compare**

- `compare_counts.py` — `--diff-only` (only tables/views whose counts
  differ) and `--skip-views` (don't run `COUNT(*)` on views, which can be
  slow; tables always use the exact stored row count).
- `diff_rows.py` — `--key` / `-k <COL> [COL ...]`, required. The column(s)
  rows are matched on; give several for a composite key, or `A_COL:B_COL`
  when the key is named differently in the second table (e.g.
  `--key NODE_ID:ID`). The key must be unique on both sides (check with
  `find_duplicates.py`); rows with a NULL key can't be matched and show as
  only in A / only in B.

**Usage, access & lineage**

- `find_usages.py` — `--partial` (match the name inside longer names, e.g.
  `ORDERS` also finds `ORDERS_DAILY`; the default is a whole-word match)
  and `--dependencies` (for one `SCHEMA.TABLE_OR_VIEW`, list objects in
  any database that depend on it, directly or through other views/dynamic
  tables, from `SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES`; can lag up
  to 3 hours, needs access to the `SNOWFLAKE` database, and can't be
  combined with `--partial` or `--schema`).
- `table_access.py` — `--days <N>` (history to look at, default 30, max
  365) and, without a table, `--unused` (only tables/views no query read).
  A query counts when its SQL names the table (text in quotes doesn't
  count); it's a write when the table is the target of an
  INSERT/MERGE/UPDATE/DELETE/COPY/TRUNCATE/CREATE TABLE AS, otherwise a
  read. Reads through a view count against the view, so check
  `find_usages.py --dependencies` before dropping a table. Needs access to
  `SNOWFLAKE.ACCOUNT_USAGE` and can lag up to 45 minutes.
- `show_grants.py` — `--users` (expand roles to the users who hold them,
  following role inheritance) and `--privilege <name>` (only one
  privilege, e.g. `SELECT`).

**Pipelines**

- `list_tasks.py` — `--failing` (only tasks with recent failures or
  suspended due to errors), `--runs SCHEMA.TASK` (one task's runs with
  error messages) and `--hours <H>` (run-history window, default and max
  167, i.e. 7 days).
- `list_dynamic_tables.py` — `--failing` (only dynamic tables with a
  failed refresh in the past 7 days, with the latest error) and
  `--refreshes SCHEMA.TABLE` (one dynamic table's recent refreshes: when
  each ran, success, full or incremental, queue vs run time, rows
  inserted/deleted and any error). Refresh history only goes back about 7
  days. `--refreshes` can't be combined with `--failing` or `--schema`.
- `pipe_status.py` — `--all` (pipes in every database your role can see),
  `--failing` (only pipes that aren't running or had failed/partially
  loaded files; with `--loads`, only those files), `--loads SCHEMA.PIPE` or
  `DATABASE.SCHEMA.PIPE` (one pipe's recent files with row and error counts
  and the first error) and `--days <N>` (load-history window, default 7,
  max 365). Load counts come from `ACCOUNT_USAGE.COPY_HISTORY`, which can
  lag up to 2 hours.

**Cost & activity**

- `query_history.py` — `--hours <H>` (lookback, default 24, max 167),
  `--failed`, `--min-seconds <S>`, `--search <text>`, `--warehouse
  <name>`, `--slowest` (sort by duration), and `--user <name>` or
  `--all-users` to see other people's queries. `--all-users` on its own is
  slow (roughly 30 seconds per day of history); combine it with
  `--warehouse` or a small `--hours` to keep it fast.
- `warehouse_usage.py` — `--days <N>` (default 30; compared with the N
  days before, max 182, or up to 365 with `--by`), `--by day|week|month`
  (trend instead of per-warehouse comparison), `--warehouse <name>` and
  `--price <dollars per credit>` (adds cost columns).
- `storage_usage.py` — `--by database|schema` (totals per database across
  the whole account, or per schema, instead of per table), `--dropped`
  (per-table view: only dropped tables still being billed) and
  `--price <dollars per TB per month>` (adds monthly cost). NOT_LIVE_% is
  the share held for time travel, fail-safe and clones: lower the
  retention for high time travel, and make tables reloaded in full
  `TRANSIENT` to drop fail-safe. GB/TB are 1,024-based; from
  `TABLE_STORAGE_METRICS`, which can lag up to 90 minutes.

Terminal output is printed the same way whether or not `--output` is used:
an aligned, column-width-aware table.

## Using this with Claude

This repo's root `SKILL.md` + `scripts/` folder together form a valid Claude
Skill. To install it:

1. Zip this entire repo folder and rename the `.zip` to `.skill`.
2. Share the `.skill` file in a Claude conversation (Cowork/Claude Code) — it
   will offer a "Save skill" button.
3. Once saved, just ask Claude things like "what tables are in my Snowflake
   database" or "describe PUBLIC.ORDERS" in any future conversation. Claude
   will hand you the exact command to run locally and interpret whatever
   output you paste back — it cannot query Snowflake directly from its own
   sandbox due to network restrictions, so this relay step is expected.

## Known issues and fixes

**`AttributeError: module 'ssl' has no attribute 'PROTOCOL_TLSv1'`** — an
open upstream bug in `snowflake-connector-python`
([snowflakedb/snowflake-connector-python#3048](https://github.com/snowflakedb/snowflake-connector-python/issues/3048)).
On very new Python/OpenSSL builds, the legacy `ssl.PROTOCOL_TLSv1`/`TLSv1_1`
constants have been removed, but the connector's vendored urllib3 still
references them unconditionally. `sf_conn.py` already includes a fix (a shim
defining those constants with unique sentinel integers before importing the
connector).

**`error: externally-managed-environment`** — Homebrew-managed Python blocks
system-wide `pip install`. Use the venv steps above instead of
`--break-system-packages`.

**`SQL compilation error: Object does not exist`** — almost always a typo, or
the object genuinely isn't in the configured database. Try
`search_tables.py <partial_name>` to find the right name; every script here
already resolves case automatically, so case-sensitivity itself shouldn't be
the cause.

**`Incorrect username or password was specified`** — a real auth failure.
Double-check `scripts/.env` matches what works logging into the Snowflake
web UI directly.
