"""
Read-only: lists tables and views across the configured database (or one
schema), sorted by last_altered descending - answers "what changed
recently" without having to check each schema individually.

Usage:
  python recent_tables.py                        # 20 most recently altered, whole database
  python recent_tables.py --limit 50
  python recent_tables.py --schema SOME_SCHEMA
  python recent_tables.py --output recent.csv
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_bytes, resolve_schema


def main():
    parser = argparse.ArgumentParser(description="List tables/views sorted by last_altered, most recent first.")
    parser.add_argument("--schema", help="Only consider this schema (case-insensitive).")
    parser.add_argument("--limit", "-n", type=int, default=20, help="Max rows to show (default 20).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        query = (
            'select table_schema, table_name, table_type, row_count, bytes, last_altered '
            f'from {info_schema(db, "tables")}'
        )
        params = ()

        if args.schema:
            real_schema = resolve_schema(conn, db, args.schema)
            query += " where table_schema = %s"
            params = (real_schema,)

        query += " order by last_altered desc nulls last limit %s"
        params = params + (args.limit,)

        cs = conn.cursor()
        cs.execute(query, params)
        rows = cs.fetchall()

        if not rows:
            scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
            print(f"No tables or views found in {scope}.")
            return

        display_rows = []
        for schema, name, ttype, row_count, bytes_, last_altered in rows:
            kind = kind_from_type(ttype)
            display_rows.append([
                f"{schema}.{name}",
                kind,
                fmt_count(row_count),
                fmt_bytes(bytes_),
                last_altered,
            ])

        print_table(["TABLE", "KIND", "ROWS", "SIZE", "LAST_ALTERED"], display_rows)
        print(f"\n({len(rows)} tables/views, most recently altered first)")

        if args.output:
            n = write_csv(
                args.output,
                ["table", "kind", "rows", "size_bytes", "last_altered"],
                display_rows,
            )
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
