---
name: snowflake-query-relay
description: Use whenever the user wants to look at, query, pull, or sample data from their Snowflake account/database, OR wants to explore how their Snowflake tables/views are structured (e.g. "give me 10 rows from SCHEMA.TABLE", "what tables are in the CUT_AND_DRY database", "show me the schemas in snowflake", "query MONDAY_DATA", "pull some data from snowflake", "what columns does this table have", "show me the DDL for this table", "which tables have a customer_id column", "find tables with 'monday' in the name", "what's the row count / size / last modified date on this table"). Also use if the user mentions a Snowflake connection script, .env file with SNOWFLAKE_ variables, or asks to set up/fix a Snowflake Python connection. This covers the case where Claude cannot reach Snowflake's network directly and must relay the query through the user's own local terminal using pre-built Python scripts. Views are treated identically to tables throughout.
---

# Snowflake Query Relay

## Why this skill exists

Claude's own sandboxed shell cannot reach `*.snowflakecomputing.com` — outbound
network access is restricted to an allowlist that doesn't include Snowflake.
This was confirmed by direct testing (a connection attempt returned a `403`
from the sandbox's proxy). This is a fixed infrastructure constraint, not a
credentials or script problem, and it won't be fixed by writing better code.

Because of this, Claude can never directly run a Snowflake query itself (not
via bash, not as a skill, not as a scheduled task) unless the user's org admin
adds Snowflake's domain to the network allowlist in Admin settings →
Capabilities. Barring that, the only working pattern is: Claude hands the
user an exact command to run in their own terminal (where there's no network
restriction), the user runs it and pastes back the output, and Claude
interprets/summarizes that output. This skill exists to make that relay as
smooth and low-friction as possible, and to carry forward all the fixes
already discovered for this particular setup so Claude doesn't have to
rediscover them each time.

## One-time setup (skip if already done)

These files should live together in one folder — typically the user's
project workspace folder. If `sf_conn.py` isn't already present there, copy
the versions from this skill's `scripts/` folder into that project folder:

- `sf_conn.py` — shared connection helper (handles a known compatibility bug,
  see below). Also provides `resolve_object()`, which looks up a schema/table
  by name case-insensitively via `INFORMATION_SCHEMA` and returns its real
  stored case — every other script uses this so the user never has to worry
  about quoting lowercase/mixed-case identifiers (e.g. Airbyte's
  `airbyte_internal` schema).
- `list_tables.py` — lists every table AND view (with kind, row count, size,
  last_altered) in whatever database is set in `.env`, optionally filtered to
  one schema
- `query_table.py` — runs `select * from <table> limit <N>` and prints the
  result
- `describe_table.py` — shows a table/view's full column definitions (name,
  type, nullable, default, comment) plus metadata (row count, size, created,
  last_altered, comment)
- `profile_table.py` — profiles every column of a table/view in one
  aggregate query: null count and %, distinct count (approximate by
  default, `--exact` for exact), min and max. `--sample N` profiles a
  random N-row sample for very large tables
- `top_values.py` — shows the most frequent values in one column with
  count, % of rows and cumulative % (NULL counted as a value); `--limit`
  (default 20), `--sample N` for very large tables. Column name is
  case-insensitive
- `get_ddl.py` — prints the full `CREATE TABLE`/`CREATE VIEW` statement (DDL)
  for a table or view
- `search_tables.py` — searches the whole database for tables/views whose
  name partially matches a given string, across all schemas
- `search_columns.py` — searches the whole database for columns whose name
  partially matches a given string, showing every table/view that has one
- `list_dynamic_tables.py` — lists dynamic tables specifically (via
  `SHOW DYNAMIC TABLES`, distinct from `list_tables.py`'s plain
  tables/views), showing target lag, warehouse, refresh mode, scheduling
  state, row count, and size, optionally filtered to one schema
- `diff_schema.py` — compares two schemas: tables/views present in only one
  of them, and for tables/views present in both, columns that were added,
  removed, or changed type/nullability - useful for versioned schema copies
  (e.g. `DTAM_V2` vs `DTAM_V3`)
- `list_schemas.py` — lists every schema in the database with table/view
  counts, estimated size, and created/last_altered timestamps - a quick
  overview before drilling into one schema
- `recent_tables.py` — lists tables/views sorted by `last_altered`, most
  recent first, optionally scoped to one schema - answers "what changed
  recently"
- `.env.template` — template for credentials; the user copies this to `.env`
  and fills in real values (account identifier, username, password,
  warehouse, role, database). Never fill in or guess credentials yourself —
  ask the user for them, or confirm a `.env` already exists before relying on
  it.

If the user needs the Python environment set up from scratch (fresh machine,
new folder), walk them through:
```
python3 -m venv venv
source venv/bin/activate
pip install snowflake-connector-python python-dotenv
```
On macOS with a Homebrew-managed Python, plain `pip install` will fail with
`externally-managed-environment` — the venv above is the fix, don't suggest
`--break-system-packages` for a long-lived setup like this.

## The actual workflow, every time the user asks for data or structure

1. **Figure out what they want**:
   - Get the lay of the land across the whole database (what schemas exist,
     how big each is) → `list_schemas.py`
   - Browse what's available in one schema → `list_tables.py` (optionally
     scoped to one schema), or `search_tables.py <partial_name>` if they
     only remember part of a table's name
   - Sample rows from a known table → `query_table.py`
   - Understand a table/view's shape (columns, types, nullability, row
     count, size, timestamps) → `describe_table.py`
   - Understand what's actually in a table's data (how many nulls, how
     many distinct values, value ranges per column) → `profile_table.py`
   - What values a specific column holds and how often (e.g. "what statuses
     are there", "which vendor has the most orders") → `top_values.py`
   - Need the exact `CREATE TABLE`/`CREATE VIEW` statement → `get_ddl.py`
   - Trying to find which table(s) have a given column (e.g. "which tables
     have a customer_id column") → `search_columns.py`
   - Looking specifically for dynamic tables (refresh schedule, target lag,
     warehouse, scheduling state) rather than plain tables/views →
     `list_dynamic_tables.py`
   - Comparing two schemas (e.g. a versioned pair like `DTAM_V2` vs
     `DTAM_V3`) to see what tables/columns differ → `diff_schema.py`
   - "What changed recently" across the database (or one schema) →
     `recent_tables.py`
   - A specific number of rows defaults to 10 if unstated.

2. **Give the exact command**, assuming they're in the folder with these
   scripts and (if using a venv) have it activated:
   - Overview of all schemas: `python list_schemas.py`
   - Browse: `python list_tables.py` or `python list_tables.py --schema SOME_SCHEMA`
   - Search by table name: `python search_tables.py monday`
   - Search by column name: `python search_columns.py email`
   - Sample rows: `python query_table.py SCHEMA.TABLE_NAME` (add `--limit`/`-n`
     for a different row count than the default 10, e.g.
     `python query_table.py PUBLIC.ORDERS --limit 25`)
   - Column/type/metadata detail: `python describe_table.py SCHEMA.TABLE_NAME`
   - Data profile per column: `python profile_table.py SCHEMA.TABLE_NAME`
     (add `--sample 100000` for tables with hundreds of millions of rows;
     add `--exact` only if approximate distinct counts aren't good enough -
     it's much slower on wide tables)
   - Most frequent values in a column:
     `python top_values.py SCHEMA.TABLE_NAME COLUMN` (add `--limit`/`-n` for
     more/fewer than the default 20; quote column names with spaces, e.g.
     `python top_values.py MYSCHEMA.MYTABLE "Billing Status"`)
   - Full DDL: `python get_ddl.py SCHEMA.TABLE_NAME`
   - Dynamic tables only: `python list_dynamic_tables.py` or
     `python list_dynamic_tables.py --schema SOME_SCHEMA`
   - Compare two schemas: `python diff_schema.py SCHEMA_A SCHEMA_B`
   - Recently changed tables: `python recent_tables.py` (add `--limit`/`-n`
     for more/fewer than the default 20, or `--schema` to scope to one
     schema)

   All scripts accept schema/table names case-insensitively (they resolve
   the real stored case internally) — no manual quoting needed even for
   lowercase schemas like `airbyte_internal`.

   Every script also takes `--output`/`-o <path>` to save the full result to
   a file (CSV for tabular results; `.sql` text for `get_ddl.py`) — suggest
   this whenever the user wants to keep or share more than what fits in the
   terminal, e.g. `python search_columns.py email --output matches.csv` or
   `python get_ddl.py PUBLIC.ORDERS --output orders.sql`. Output is still
   printed to the terminal as an aligned table either way, so pasting it
   back to you works exactly as before.

   If you're not sure they're still in the right folder / venv, remind them
   briefly (`cd` into the folder, `source venv/bin/activate`) rather than
   assuming — but don't over-explain this every single time once they've
   clearly got the hang of it.

3. **Wait for them to paste back the terminal output.** Don't claim to have
   run the query yourself — you haven't, and can't. If they ask you to "just
   run it", remind them (briefly, don't belabor it) that it has to go through
   their terminal because of the network restriction described above.

4. **Once they paste output back**, actually answer what they asked —
   summarize the data, pull out the specific fields they care about, spot
   anomalies, etc. Don't just silently restate the raw dump back at them.

5. **If they paste an error instead of data**, diagnose it — see the common
   failure modes below before guessing. Most of what comes up has a known
   fix. Note that every script now runs through `run_cli()` in `sf_conn.py`,
   which catches Snowflake/connection errors and prints a short
   `Snowflake error: ...` line (plus a one-line hint for the common cases
   below) instead of a raw Python traceback — so what gets pasted back to
   you should already be fairly readable.

## Known failure modes and fixes (encountered and solved before)

**`AttributeError: module 'ssl' has no attribute 'PROTOCOL_TLSv1'`** — this
is an open upstream bug in `snowflake-connector-python`
(snowflakedb/snowflake-connector-python#3048): on very new Python/OpenSSL
builds (e.g. Homebrew's latest Python, which is what this keeps happening
on), the legacy `ssl.PROTOCOL_TLSv1`/`TLSv1_1` constants have been removed,
but the connector's vendored urllib3 still references them unconditionally
at import time. `sf_conn.py` already includes the fix (a shim that defines
those constants with unique sentinel integers before importing the
connector) — if this error still shows up, check that `sf_conn.py` actually
has the shim near the top and that the script imports `sf_conn` before
`snowflake.connector` directly.

Do NOT just set the sentinel to an existing ssl constant (e.g.
`ssl.PROTOCOL_TLS_CLIENT`) as a quick fix — that was tried once and caused a
much more confusing failure (`OpenSSL.SSL.Error: no protocols available`),
because the vendored library uses these constants as dict keys, and reusing
an existing constant's value silently overwrites a different, legitimate
protocol mapping. The sentinel values must be unique numbers that don't
collide with any real `ssl.PROTOCOL_*` constant (e.g. `-1001`, `-1002`).

**`zsh: command not found: pip` / `python`** — the user's shell doesn't have
`pip`/`python` aliased; try `pip3`/`python3` instead.

**`error: externally-managed-environment`** — Homebrew-managed Python blocks
system-wide pip installs. Use a venv (see setup section above), not
`--break-system-packages` — this is a persistent project setup, not a
one-off script.

**`SQL compilation error: Object does not exist`** — this used to come up
with lowercase/mixed-case schemas (e.g. Airbyte's `airbyte_internal`), since
Snowflake auto-uppercases unquoted identifiers. `query_table.py`,
`describe_table.py`, and `get_ddl.py` all go through `resolve_object()` in
`sf_conn.py`, which looks the name up case-insensitively via
`INFORMATION_SCHEMA` first and then builds the correctly quoted reference —
so this class of error should no longer occur through normal use of these
scripts. If it still shows up, the table genuinely doesn't exist in the
configured database, or there's a typo — double check with
`search_tables.py <partial_name>`.

**`No table or view matching X.Y found` / `Multiple matches for X.Y`** — from
`resolve_object()` itself. The first means the schema/table name (or its
partial match) doesn't exist in the configured database — suggest
`search_tables.py` to look for close matches. The second means the
case-insensitive match was ambiguous (e.g. both `Public.Orders` and
`public.orders` exist) — ask the user to specify further or check
`list_tables.py` for the exact schema.

**`Incorrect username or password was specified`** — a real auth failure,
not a script bug. Have them double-check `.env` matches what works logging
into the Snowflake web UI directly; if it still fails there, the password
needs to be reset by them (not something you can fix from here).

**Proxy/connection timeout errors mentioning `snowflakecomputing.com`** — if
this happens when *you* (Claude) try to run something via your own bash
tool, stop immediately — this is the sandbox network restriction, not a
real connectivity problem, and no amount of retrying or script-tweaking will
fix it. Hand the command back to the user to run locally instead.

