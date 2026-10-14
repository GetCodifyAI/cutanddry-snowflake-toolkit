"""
Read-only: searches every table/view in the configured database for
columns whose name contains the given text (case-insensitive, partial
match) - handy for finding every table that has, say, a "customer_id"
or "email" column.

Usage:
  python search_columns.py <partial_column_name>
  python search_columns.py <partial_column_name> --output matches.csv

Example:
  python search_columns.py email
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli


def main():
    parser = argparse.ArgumentParser(description="Search for columns by partial name match.")
    parser.add_argument("pattern", help="Partial column name to search for.")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute(
            f'select table_schema, table_name, column_name, data_type, is_nullable '
            f'from "{db}".information_schema.columns '
            f'where column_name ilike %s '
            f'order by table_schema, table_name, ordinal_position',
            (f"%{args.pattern}%",),
        )
        rows = cs.fetchall()

        if not rows:
            print(f"No columns matching '{args.pattern}' found in {db}.")
            return

        display_rows = [
            [f"{schema}.{table}", column, data_type, nullable]
            for schema, table, column, data_type, nullable in rows
        ]

        print(f"Columns matching '{args.pattern}' in {db}:\n")
        print_table(["TABLE", "COLUMN", "TYPE", "NULLABLE"], display_rows)
        print(f"\n({len(rows)} matches)")

        if args.output:
            n = write_csv(
                args.output,
                ["schema", "table", "column", "data_type", "is_nullable"],
                rows,
            )
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
