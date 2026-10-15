"""
Read-only: lists every table AND view in the database set as
SNOWFLAKE_DATABASE in .env, sorted by schema then name, with kind, row
count, size, and created/last altered dates. Uses INFORMATION_SCHEMA (so
case-sensitive schema/table names, e.g. from Airbyte, just work without
any quoting).

Usage:
  python list_tables.py                        # everything in the database
  python list_tables.py --schema SOME_SCHEMA    # just one schema (case-insensitive)
  python list_tables.py --output tables.csv     # also write results to CSV
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_bytes, fmt_value, resolve_schema, plural


def main():
    parser = argparse.ArgumentParser(description="List tables and views in the configured database.")
    parser.add_argument("--schema", help="Only list objects in this schema (case-insensitive).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        query = (
            'select table_schema, table_name, table_type, row_count, bytes, '
            f'created, last_altered from {info_schema(db, "tables")}'
        )
        params = ()
        if args.schema:
            query += " where table_schema = %s"
            params = (resolve_schema(conn, db, args.schema),)
        query += " order by table_schema, table_name"

        cs.execute(query, params)
        rows = cs.fetchall()

        if not rows:
            scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
            print(f"No tables or views found in {scope}.")
            return

        display_rows = [
            [f"{schema}.{name}", kind_from_type(ttype), fmt_count(row_count), fmt_bytes(bytes_), fmt_value(created), fmt_value(last_altered)]
            for schema, name, ttype, row_count, bytes_, created, last_altered in rows
        ]
        print_table(["TABLE", "KIND", "ROWS", "SIZE", "CREATED", "LAST_ALTERED"], display_rows)
        n_schemas = len({r[0] for r in rows})
        print(f"\n({plural(len(rows), 'table/view', 'tables/views')} in {plural(n_schemas, 'schema')})")

        if args.output:
            write_csv(args.output, ["schema", "name", "type", "row_count", "bytes", "created", "last_altered"], rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
