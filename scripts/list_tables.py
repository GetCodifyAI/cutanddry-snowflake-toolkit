"""
Read-only: lists every table AND view in the database set as
SNOWFLAKE_DATABASE in .env, using INFORMATION_SCHEMA (so case-sensitive
schema/table names, e.g. from Airbyte, just work without any quoting).

Usage:
  python list_tables.py                        # everything in the database
  python list_tables.py --schema SOME_SCHEMA    # just one schema (case-insensitive)
  python list_tables.py --output tables.csv     # also write results to CSV
"""

import argparse

from sf_conn import get_connection, current_database, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_bytes


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
            query += " where table_schema ilike %s"
            params = (args.schema,)
        query += " order by table_schema, table_name"

        cs.execute(query, params)
        rows = cs.fetchall()

        if not rows:
            scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
            print(f"No tables or views found in {scope}.")
            return

        current_schema = None
        for schema, name, ttype, row_count, bytes_, created, last_altered in rows:
            if schema != current_schema:
                print(f"\n[{schema}]")
                current_schema = schema
            kind = kind_from_type(ttype)
            size = fmt_bytes(bytes_)
            rows_display = fmt_count(row_count)
            print(
                f"  - {name}  [{kind}]  rows={rows_display}  "
                f"size={size}  last_altered={last_altered}"
            )

        if args.output:
            headers = ["schema", "name", "type", "row_count", "bytes", "created", "last_altered"]
            n = write_csv(args.output, headers, rows)
            print(f"\nWrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
