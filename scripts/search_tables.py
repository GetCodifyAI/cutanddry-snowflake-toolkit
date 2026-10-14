"""
Read-only: searches every schema in the configured database for tables
or views whose name contains the given text (case-insensitive, partial
match) - handy when you remember roughly what a table is called but not
which schema it lives in.

Usage:
  python search_tables.py <partial_name>

Example:
  python search_tables.py monday
"""

import sys
from sf_conn import get_connection, current_database


def main():
    if len(sys.argv) < 2:
        print("Usage: python search_tables.py <partial_name>")
        sys.exit(1)

    pattern = sys.argv[1]
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute(
            f'select table_schema, table_name, table_type, row_count, last_altered '
            f'from "{db}".information_schema.tables '
            f'where table_name ilike %s '
            f'order by table_schema, table_name',
            (f"%{pattern}%",),
        )
        rows = cs.fetchall()

        if not rows:
            print(f"No tables or views matching '{pattern}' found in {db}.")
            return

        print(f"Matches for '{pattern}' in {db}:\n")
        for schema, name, ttype, row_count, last_altered in rows:
            kind = "VIEW" if "VIEW" in ttype else "TABLE"
            rows_display = f"{row_count:,}" if row_count is not None else "n/a"
            print(f"  {schema}.{name}  [{kind}]  rows={rows_display}  last_altered={last_altered}")
        print(f"\n({len(rows)} matches)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
