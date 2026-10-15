"""
Read-only: runs `select * from <table> limit <N>` and prints the result.
Automatically resolves the correct case of the schema/table name, so you
don't need to worry about quoting lowercase schemas like "airbyte_internal".
Numbers get commas, dates show as MM/DD/YY, JSON is compacted to one line
and long values are cut short on screen - the CSV keeps every value whole.

Usage:
  python query_table.py SCHEMA.TABLE_NAME
  python query_table.py SCHEMA.TABLE_NAME --limit 25
  python query_table.py SCHEMA.TABLE_NAME --output rows.csv

Examples:
  python query_table.py PUBLIC.ORDERS
  python query_table.py PUBLIC.ORDERS --limit 25 --output orders_sample.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, positive_int, quoted, print_table, write_csv, run_cli, fmt_cell, json_columns, truncate, plural


def main():
    parser = argparse.ArgumentParser(description="Sample rows from a table or view.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("--limit", "-n", type=positive_int, default=10, help="Max rows to return (default 10).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, _ = resolve_object(conn, schema, name, db)

        # One row past the limit, just to tell whether there's more.
        cs.execute(f"select * from {quoted(db, real_schema, real_name)} limit %s", (args.limit + 1,))
        cols = [d[0] for d in cs.description]
        json_cols = json_columns(cs.description)
        rows = cs.fetchall()
        more = len(rows) > args.limit
        rows = rows[:args.limit]

        print_table(cols, [[truncate(fmt_cell(v, is_json)) for v, is_json in zip(row, json_cols)] for row in rows])
        if more:
            print(f"\n(showing first {plural(len(rows), 'row')}, more available - raise --limit to see more)")
        else:
            print(f"\n({plural(len(rows), 'row')})")

        if args.output:
            write_csv(args.output, cols, rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
