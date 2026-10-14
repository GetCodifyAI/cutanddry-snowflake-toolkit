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
   | `SNOWFLAKE_DATABASE` | no | leave blank to browse everything; set it to scope all scripts to one database |
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

## Scripts

Run all of these from inside the `scripts/` folder (with the venv activated).

| Script | What it does | Example |
|---|---|---|
| `test_connection.py` | Sanity-checks your `.env` and credentials | `python test_connection.py` |
| `list_tables.py` | Lists every table + view in the configured database, grouped by schema, with kind/row count/size/last_altered | `python list_tables.py` or `python list_tables.py PUBLIC` |
| `search_tables.py` | Finds tables/views by partial name match across every schema | `python search_tables.py monday` |
| `search_columns.py` | Finds every table/view that has a column matching a partial name | `python search_columns.py email` |
| `query_table.py` | Samples rows from a table/view | `python query_table.py PUBLIC.ORDERS 25` |
| `describe_table.py` | Shows full column definitions (name, type, nullable, default, comment) plus table metadata (row count, size, timestamps) | `python describe_table.py PUBLIC.ORDERS` |
| `get_ddl.py` | Prints the full `CREATE TABLE`/`CREATE VIEW` statement | `python get_ddl.py PUBLIC.ORDERS` |

All scripts accept schema/table names case-insensitively — they resolve the
real stored case internally via `INFORMATION_SCHEMA`, so lowercase or
mixed-case schemas (e.g. Airbyte's `airbyte_internal`) just work without
manual quoting. Views are treated identically to tables throughout.

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

## Security

- `scripts/.env` holds real credentials and is git-ignored — never commit it
  or paste its contents anywhere public.
- Every script here is read-only by construction (`select` / `show tables` /
  `get_ddl` only). Don't add write/DDL-modifying queries without
  understanding the risk.
- Use a Snowflake role scoped to read-only access (`SELECT`/`USAGE`) rather
  than a role with write privileges, if your Snowflake admin can set one up.
