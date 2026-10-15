"""
Read-only: lists every schema in the configured database with a quick
table/view count and total size - a fast overview before drilling into
one schema with list_tables.py, list_dynamic_tables.py, or diff_schema.py.

Usage:
  python list_schemas.py
  python list_schemas.py --output schemas.csv
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_bytes, fmt_value


def main():
    parser = argparse.ArgumentParser(description="List every schema in the configured database.")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()

        cs.execute(f'select schema_name, created from {info_schema(db, "schemata")} order by schema_name')
        schema_rows = cs.fetchall()

        if not schema_rows:
            print(f"No schemas found in {db}.")
            return

        # Per-schema stats: table/view counts, total size, latest change.
        # Views report NULL bytes, so the size really means "size of the
        # tables only" - treat it as an estimate, not a precise total.
        cs.execute(
            f'select table_schema, table_type, count(*), sum(bytes), max(last_altered) '
            f'from {info_schema(db, "tables")} group by table_schema, table_type'
        )
        stats = {}  # schema -> {"TABLE": n, "VIEW": n, "bytes": n, "last_altered": ts}
        for schema, ttype, cnt, total_bytes, last_altered in cs.fetchall():
            s = stats.setdefault(schema, {"TABLE": 0, "VIEW": 0, "bytes": 0, "last_altered": None})
            s[kind_from_type(ttype)] += cnt
            s["bytes"] += total_bytes or 0
            if last_altered and (s["last_altered"] is None or last_altered > s["last_altered"]):
                s["last_altered"] = last_altered

        display_rows = []
        for schema, created in schema_rows:
            s = stats.get(schema, {"TABLE": 0, "VIEW": 0, "bytes": 0, "last_altered": None})
            display_rows.append([
                schema,
                fmt_count(s["TABLE"]),
                fmt_count(s["VIEW"]),
                fmt_bytes(s["bytes"]),
                fmt_value(created),
                fmt_value(s["last_altered"]),
            ])

        print_table(["SCHEMA", "TABLES", "VIEWS", "SIZE", "CREATED", "LAST_ALTERED"], display_rows)
        print(f"\n({len(display_rows):,} schemas)")

        if args.output:
            write_csv(args.output, ["schema", "tables", "views", "size_bytes", "created", "last_altered"], display_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
