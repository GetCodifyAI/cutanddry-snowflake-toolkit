"""
Shared Snowflake connection helper.
Loads credentials from .env (see .env.template) and applies a
compatibility shim needed on very new Python/OpenSSL builds where the
legacy ssl.PROTOCOL_TLSv1 / TLSv1_1 constants have been removed.
(See snowflakedb/snowflake-connector-python issue #3048.)
"""

import os
import ssl

if not hasattr(ssl, "PROTOCOL_TLSv1"):
    ssl.PROTOCOL_TLSv1 = -1001  # unique sentinel, must not collide with real constants
if not hasattr(ssl, "PROTOCOL_TLSv1_1"):
    ssl.PROTOCOL_TLSv1_1 = -1002  # unique sentinel, must not collide with real constants

from dotenv import load_dotenv
import snowflake.connector

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
        f'select table_schema, table_name, table_type '
        f'from "{db}".information_schema.tables '
        f'where table_schema ilike %s and table_name ilike %s',
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
    kind = "VIEW" if "VIEW" in table_type else "TABLE"
    return real_schema, real_name, kind


def quoted(db, schema, name):
    return f'"{db}"."{schema}"."{name}"'
