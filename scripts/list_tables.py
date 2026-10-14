"""
Read-only: lists every table AND view in the database set as
SNOWFLAKE_DATABASE in .env, using INFORMATION_SCHEMA (so case-sensitive
schema/table names, e.g. from Airbyte, just work without any quoting).

Usage:
  python list_tables.py                  # everything in the database
  python list_tables.py SOME_SCHEMA      # just one schema (case-insensitive)
"""

import sys
from sf_conn import get_connection, current_database


def main():
    db = current_database()
    schema_filter = sys.argv[1] if len(sys.argv) > 1 else None

    conn = get_connection()
    try:
        cs = conn.cursor()
        query = (
            f'select table_schema, table_name, table_type, row_count, bytes, '
            f'created, last_altered '
            f'from "{db}".information_schema.tables'
        )
        params = ()
        if schema_filter:
            query += " where table_schema ilike %s"
            params = (schema_filter,)
        query += " order by table_schema, table_name"

        cs.execute(query, params)
        rows = cs.fetchall()

        if not rows:
            scope = f"schema '{schema_filter}'" if schema_filter else f"database {db}"
            print(f"No tables or views found in {scope}.")
            return

        current_schema = None
        for schema, name, ttype, row_count, bytes_, created, last_altered in rows:
            if schema != current_schema:
                print(f"\n[{schema}]")
                current_schema = schema
            kind = "VIEW" if "VIEW" in ttype else "TABLE"
            size = f"{bytes_:,} bytes" if bytes_ is not None else "n/a"
            rows_display = f"{row_count:,}" if row_count is not None else "n/a"
            print(
                f"  - {name}  [{kind}]  rows={rows_display}  "
                f"size={size}  last_altered={last_altered}"
            )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
