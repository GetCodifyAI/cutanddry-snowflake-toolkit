"""
Read-only: runs `select * from <table> limit <N>` and prints the result.
Automatically resolves the correct case of the schema/table name, so you
don't need to worry about quoting lowercase schemas like "airbyte_internal".

Usage:
  python query_table.py SCHEMA.TABLE_NAME [limit]

Examples:
  python query_table.py PUBLIC.ORDERS
  python query_table.py PUBLIC.ORDERS 25
"""

import sys
from sf_conn import get_connection, current_database, resolve_object, quoted


def main():
    if len(sys.argv) < 2 or "." not in sys.argv[1]:
        print("Usage: python query_table.py <schema.table_name> [limit]")
        sys.exit(1)

    schema, name = sys.argv[1].split(".", 1)
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)
        full_name = quoted(db, real_schema, real_name)

        cs.execute(f"select * from {full_name} limit {limit}")
        cols = [d[0] for d in cs.description]
        rows = cs.fetchall()

        print(" | ".join(cols))
        print("-" * 100)
        for row in rows:
            print(" | ".join("" if v is None else str(v) for v in row))
        print(f"\n({len(rows)} rows)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
