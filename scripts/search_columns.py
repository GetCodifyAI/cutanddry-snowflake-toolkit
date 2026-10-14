"""
Read-only: searches every table/view in the configured database for
columns whose name contains the given text (case-insensitive, partial
match) - handy for finding every table that has, say, a "customer_id"
or "email" column.

Usage:
  python search_columns.py <partial_column_name>

Example:
  python search_columns.py email
"""

import sys
from sf_conn import get_connection, current_database


def main():
    if len(sys.argv) < 2:
        print("Usage: python search_columns.py <partial_column_name>")
        sys.exit(1)

    pattern = sys.argv[1]
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute(
            f'select table_schema, table_name, column_name, data_type, is_nullable '
            f'from "{db}".information_schema.columns '
            f'where column_name ilike %s '
            f'order by table_schema, table_name, ordinal_position',
            (f"%{pattern}%",),
        )
        rows = cs.fetchall()

        if not rows:
            print(f"No columns matching '{pattern}' found in {db}.")
            return

        print(f"Columns matching '{pattern}' in {db}:\n")
        for schema, table, column, data_type, nullable in rows:
            print(f"  {schema}.{table}.{column}  ({data_type}, nullable={nullable})")
        print(f"\n({len(rows)} matches)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
