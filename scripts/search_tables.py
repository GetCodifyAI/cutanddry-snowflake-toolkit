"""
Read-only: searches every schema in the configured database for tables
or views whose name contains the given text (case-insensitive, partial
match) - handy when you remember roughly what a table is called but not
which schema it lives in.

Usage:
  python search_tables.py <partial_name>
  python search_tables.py <partial_name> --schema SOME_SCHEMA   # just one schema
  python search_tables.py <partial_name> --output matches.csv

Example:
  python search_tables.py monday
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, kind_from_type, fmt_count, fmt_value, like_contains, plural, resolve_schema


def main():
    parser = argparse.ArgumentParser(description="Search for tables/views by partial name match.")
    parser.add_argument("pattern", help="Partial table/view name to search for.")
    parser.add_argument("--schema", help="Only search this schema (case-insensitive).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        schema_filter, params = "", [like_contains(args.pattern)]
        if args.schema:
            schema_filter = " and table_schema = %s"
            params.append(resolve_schema(conn, db, args.schema))
        cs.execute(
            'select table_schema, table_name, table_type, row_count, last_altered '
            f'from {info_schema(db, "tables")} '
            f"where table_name ilike %s escape '\\\\'{schema_filter} order by table_schema, table_name",
            tuple(params),
        )
        rows = cs.fetchall()

        where = f"{db}.{params[1]}" if args.schema else db
        if not rows:
            print(f"No tables or views matching '{args.pattern}' found in {where}.")
            return

        display_rows = [
            [f"{schema}.{name}", kind_from_type(ttype), fmt_count(row_count), fmt_value(last_altered)]
            for schema, name, ttype, row_count, last_altered in rows
        ]

        print(f"Matches for '{args.pattern}' in {where}:\n")
        print_table(["TABLE", "KIND", "ROWS", "LAST_ALTERED"], display_rows)
        print(f"\n({plural(len(rows), 'match', 'matches')})")

        if args.output:
            write_csv(args.output, ["schema", "name", "type", "row_count", "last_altered"], rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
