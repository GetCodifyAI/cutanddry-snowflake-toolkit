"""
Read-only: lists every schema in the configured database with a quick
table/view count and total size - a fast overview before drilling into
one schema with list_tables.py, list_dynamic_tables.py, or diff_schema.py.

Usage:
  python list_schemas.py
  python list_schemas.py --output schemas.csv
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_bytes


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

        # Table/view counts + sizes per schema. Views report NULL bytes, so
        # totals here really mean "size of the TABLE kind only" - treat as
        # an estimate, not a precise total.
        cs.execute(
            f'select table_schema, table_type, count(*), sum(bytes) '
            f'from {info_schema(db, "tables")} group by table_schema, table_type'
        )
        counts_by_schema = {}
        for schema, ttype, cnt, total_bytes in cs.fetchall():
            kind = kind_from_type(ttype)
            d = counts_by_schema.setdefault(schema, {})
            prev_cnt, prev_bytes = d.get(kind, (0, 0))
            d[kind] = (prev_cnt + cnt, prev_bytes + (total_bytes or 0))

        cs.execute(
            f'select table_schema, max(last_altered) from {info_schema(db, "tables")} group by table_schema'
        )
        last_altered_by_schema = dict(cs.fetchall())

        display_rows = []
        for schema, created in schema_rows:
            counts = counts_by_schema.get(schema, {})
            table_count, _ = counts.get("TABLE", (0, 0))
            view_count, _ = counts.get("VIEW", (0, 0))
            total_bytes = sum(b for _, b in counts.values())
            last_altered = last_altered_by_schema.get(schema)
            display_rows.append([
                schema,
                fmt_count(table_count),
                fmt_count(view_count),
                fmt_bytes(total_bytes),
                created,
                last_altered,
            ])

        print_table(["SCHEMA", "TABLES", "VIEWS", "SIZE", "CREATED", "LAST_ALTERED"], display_rows)
        print(f"\n({len(display_rows)} schemas)")

        if args.output:
            n = write_csv(
                args.output,
                ["schema", "tables", "views", "size_bytes", "created", "last_altered"],
                display_rows,
            )
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
