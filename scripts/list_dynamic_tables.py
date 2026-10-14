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

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, fmt_count, fmt_bytes


def resolve_schema(conn, db, schema):
    """Look up a schema by name, tolerant of case. Returns the exact stored case."""
    cs = conn.cursor()
    cs.execute(
        f'select schema_name from "{db}".information_schema.schemata where schema_name ilike %s',
        (schema,),
    )
    rows = cs.fetchall()
    if not rows:
        raise SystemExit(f"No schema matching '{schema}' found in {db}.")
    if len(rows) > 1:
        options = ", ".join(r[0] for r in rows)
        raise SystemExit(f"Multiple schemas matching '{schema}': {options}. Be more specific.")
    return rows[0][0]


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
            cs.execute(f'show dynamic tables in schema "{db}"."{real_schema}"')
        else:
            cs.execute(f'show dynamic tables in database "{db}"')

        # SHOW DYNAMIC TABLES isn't an INFORMATION_SCHEMA select, so column
        # order/count can vary across Snowflake versions - read names off
        # the cursor description instead of assuming a fixed layout.
        columns = [c[0].lower() for c in cs.description]
        rows = cs.fetchall()

        if not rows:
            scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
            print(f"No dynamic tables found in {scope}.")
            return

        def col(row, name):
            return row[columns.index(name)] if name in columns else None

        display_rows = []
        for row in rows:
            schema_name = col(row, "schema_name")
            name = col(row, "name")
            table_display = f"{schema_name}.{name}" if schema_name else name
            display_rows.append([
                table_display,
                col(row, "target_lag") or "",
                col(row, "warehouse") or "",
                col(row, "refresh_mode") or "",
                col(row, "scheduling_state") or "",
                fmt_count(col(row, "rows")),
                fmt_bytes(col(row, "bytes")),
                col(row, "data_timestamp") or "",
            ])

        headers = ["TABLE", "TARGET_LAG", "WAREHOUSE", "REFRESH_MODE", "STATE", "ROWS", "SIZE", "DATA_TIMESTAMP"]
        print_table(headers, display_rows)
        print(f"\n({len(rows)} dynamic tables)")

        if args.output:
            n = write_csv(args.output, [h.lower() for h in headers], display_rows)
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
