"""
Read-only: runs `select * from <table> limit <N>` and prints the result.
Automatically resolves the correct case of the schema/table name, so you
don't need to worry about quoting lowercase schemas like "airbyte_internal".

Usage:
  python query_table.py SCHEMA.TABLE_NAME
  python query_table.py SCHEMA.TABLE_NAME --limit 25
  python query_table.py SCHEMA.TABLE_NAME --output rows.csv

Examples:
  python query_table.py PUBLIC.ORDERS
  python query_table.py PUBLIC.ORDERS --limit 25 --output orders_sample.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, positive_int, quoted, print_table, write_csv, run_cli


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

        cs.execute(f"select * from {quoted(db, real_schema, real_name)} limit %s", (args.limit,))
        cols = [d[0] for d in cs.description]
        rows = cs.fetchall()

        print_table(cols, rows)
        print(f"\n({len(rows):,} rows)")

        if args.output:
            write_csv(args.output, cols, rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
