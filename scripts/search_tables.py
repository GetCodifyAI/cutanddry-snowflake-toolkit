"""
Read-only: searches every schema in the configured database for tables
or views whose name contains the given text (case-insensitive, partial
match) - handy when you remember roughly what a table is called but not
which schema it lives in.

Usage:
  python search_tables.py <partial_name>
  python search_tables.py <partial_name> --output matches.csv

Example:
  python search_tables.py monday
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv


def main():
    parser = argparse.ArgumentParser(description="Search for tables/views by partial name match.")
    parser.add_argument("pattern", help="Partial table/view name to search for.")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute(
            f'select table_schema, table_name, table_type, row_count, last_altered '
            f'from "{db}".information_schema.tables '
            f'where table_name ilike %s '
            f'order by table_schema, table_name',
            (f"%{args.pattern}%",),
        )
        rows = cs.fetchall()

        if not rows:
            print(f"No tables or views matching '{args.pattern}' found in {db}.")
            return

        display_rows = []
        for schema, name, ttype, row_count, last_altered in rows:
            kind = "VIEW" if "VIEW" in ttype else "TABLE"
            rows_display = f"{row_count:,}" if row_count is not None else "n/a"
            display_rows.append([f"{schema}.{name}", kind, rows_display, last_altered])

        print(f"Matches for '{args.pattern}' in {db}:\n")
        print_table(["TABLE", "KIND", "ROWS", "LAST_ALTERED"], display_rows)
        print(f"\n({len(rows)} matches)")

        if args.output:
            n = write_csv(
                args.output,
                ["schema", "name", "type", "row_count", "last_altered"],
                rows,
            )
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
