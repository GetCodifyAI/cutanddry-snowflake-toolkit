"""
Read-only: lists dynamic tables (a distinct Snowflake object type - a table
whose contents are kept in sync on a schedule via `SHOW DYNAMIC TABLES`,
separate from plain tables/views) in the configured database, showing target
lag, warehouse, refresh mode, scheduling state, row count, and size.
Optionally scoped to one schema.

Usage:
  python list_dynamic_tables.py                       # whole database
  python list_dynamic_tables.py --schema SOME_SCHEMA   # just one schema
  python list_dynamic_tables.py --output dynamic_tables.csv
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, fmt_count, fmt_bytes, resolve_schema, quoted, fmt_value, plural, show_rows


def main():
    parser = argparse.ArgumentParser(description="List dynamic tables and their refresh settings.")
    parser.add_argument("--schema", help="Only list dynamic tables in this schema (case-insensitive).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        if args.schema:
            real_schema = resolve_schema(conn, db, args.schema)
            rows = show_rows(cs, f"show dynamic tables in schema {quoted(db, real_schema)}")
        else:
            rows = show_rows(cs, f"show dynamic tables in database {quoted(db)}")

        if not rows:
            scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
            print(f"No dynamic tables found in {scope}.")
            return

        display_rows = []
        for row in rows:
            schema_name = row.get("schema_name")
            name = row.get("name")
            table_display = f"{schema_name}.{name}" if schema_name else name
            display_rows.append([
                table_display,
                row.get("target_lag") or "",
                row.get("warehouse") or "",
                row.get("refresh_mode") or "",
                row.get("scheduling_state") or "",
                fmt_count(row.get("rows")),
                fmt_bytes(row.get("bytes")),
                fmt_value(row.get("data_timestamp")),
            ])

        headers = ["TABLE", "TARGET_LAG", "WAREHOUSE", "REFRESH_MODE", "STATE", "ROWS", "SIZE", "DATA_TIMESTAMP"]
        print_table(headers, display_rows)
        print(f"\n({plural(len(rows), 'dynamic table')})")

        if args.output:
            write_csv(args.output, [h.lower() for h in headers], display_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
