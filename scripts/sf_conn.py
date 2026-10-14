"""
Shared Snowflake connection helper.
Loads credentials from .env (see .env.template) and applies a
compatibility shim needed on very new Python/OpenSSL builds where the
legacy ssl.PROTOCOL_TLSv1 / TLSv1_1 constants have been removed.
(See snowflakedb/snowflake-connector-python issue #3048.)
"""

import csv
import os
import ssl
import sys

if not hasattr(ssl, "PROTOCOL_TLSv1"):
    ssl.PROTOCOL_TLSv1 = -1001  # unique sentinel, must not collide with real constants
if not hasattr(ssl, "PROTOCOL_TLSv1_1"):
    ssl.PROTOCOL_TLSv1_1 = -1002  # unique sentinel, must not collide with real constants

from dotenv import load_dotenv
import snowflake.connector
import snowflake.connector.errors as sf_errors

load_dotenv()

REQUIRED = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_WAREHOUSE"]


def get_connection():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        raise SystemExit(
            f"Missing required .env values: {', '.join(missing)}. "
            "Copy .env.template to .env and fill it in first."
        )

    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        role=os.getenv("SNOWFLAKE_ROLE") or None,
        database=os.getenv("SNOWFLAKE_DATABASE") or None,
        schema=os.getenv("SNOWFLAKE_SCHEMA") or None,
    )


def current_database():
    db = os.getenv("SNOWFLAKE_DATABASE")
    if not db:
        raise SystemExit("Set SNOWFLAKE_DATABASE in .env first.")
    return db


def resolve_object(conn, schema, name, database=None):
    """
    Look up a table or view by schema+name, tolerant of case (Snowflake
    auto-uppercases unquoted identifiers, so a schema/table that was
    actually created lowercase, e.g. via Airbyte, won't match a plain
    `schema.table` reference). Returns (real_schema, real_name, kind)
    where kind is 'TABLE' or 'VIEW', using the exact stored case so the
    caller can safely build a quoted reference. Raises SystemExit with a
    clear message if there's no match or more than one.
    """
    db = database or current_database()
    cs = conn.cursor()
    cs.execute(
        f'select table_schema, table_name, table_type from {info_schema(db, "tables")} '
        'where table_schema ilike %s and table_name ilike %s',
        (schema, name),
    )
    rows = cs.fetchall()
    if not rows:
        raise SystemExit(f"No table or view matching {schema}.{name} found in {db}.")
    if len(rows) > 1:
        options = ", ".join(f"{r[0]}.{r[1]}" for r in rows)
        raise SystemExit(
            f"Multiple matches for {schema}.{name}: {options}. Be more specific."
        )
    real_schema, real_name, table_type = rows[0]
    return real_schema, real_name, kind_from_type(table_type)


def quoted(db, schema, name):
    return f'"{db}"."{schema}"."{name}"'


def info_schema(db, view_name):
    """Return the quoted, schema-qualified name of an INFORMATION_SCHEMA view, e.g. info_schema(db, "tables")."""
    return f'"{db}".information_schema.{view_name}'


def kind_from_type(table_type):
    """Map an INFORMATION_SCHEMA table_type value to the short 'TABLE'/'VIEW' label used for display."""
    return "VIEW" if "VIEW" in table_type else "TABLE"


def fmt_count(n):
    """Format a row count for display: comma-grouped, or 'n/a' if unknown."""
    return f"{n:,}" if n is not None else "n/a"


def fmt_bytes(n):
    """Format a byte size for display: comma-grouped with a 'bytes' suffix, or 'n/a' if unknown."""
    return f"{n:,} bytes" if n is not None else "n/a"


def print_table(headers, rows):
    """Pretty-print rows as an aligned, column-width-aware table."""
    str_rows = [["" if v is None else str(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    def fmt(vals):
        return "  ".join(val.ljust(widths[i]) for i, val in enumerate(vals))

    print(fmt(headers))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in str_rows:
        print(fmt(row))


def write_csv(path, headers, rows):
    """Write headers+rows to a CSV file at `path`. Returns the row count."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


# Known error substrings -> a short, actionable hint printed under the error.
_ERROR_HINTS = [
    ("Incorrect username or password",
     "Double-check SNOWFLAKE_USER / SNOWFLAKE_PASSWORD in scripts/.env."),
    ("does not exist or not authorized",
     "Check the schema/table name - try search_tables.py or search_columns.py to find it."),
    ("Object does not exist",
     "Check the schema/table name - try search_tables.py or search_columns.py to find it."),
    ("JWT token is invalid", "Your Snowflake session/token has expired - just re-run the command."),
    ("getaddrinfo failed", "Check SNOWFLAKE_ACCOUNT in scripts/.env and your network connection."),
    ("Name or service not known", "Check SNOWFLAKE_ACCOUNT in scripts/.env and your network connection."),
]


def run_cli(main_func):
    """
    Run a script's main() with friendly error handling, so connection/auth/
    SQL problems print a short, clear message instead of a raw Python
    traceback. argparse's own errors and Ctrl-C still behave normally.
    """
    try:
        main_func()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except sf_errors.Error as e:
        msg = str(e)
        print(f"Snowflake error: {msg}")
        for substr, hint in _ERROR_HINTS:
            if substr in msg:
                print(f"  -> {hint}")
                break
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)
