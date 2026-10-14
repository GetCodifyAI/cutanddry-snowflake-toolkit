"""
Read-only: prints the full CREATE TABLE/VIEW statement (DDL) for a table
or view, exactly as Snowflake would use to recreate it (constraints,
clustering keys, view definitions, etc. all in one place).

Usage:
  python get_ddl.py SCHEMA.TABLE_OR_VIEW
"""

import sys
from sf_conn import get_connection, current_database, resolve_object, quoted


def main():
    if len(sys.argv) < 2 or "." not in sys.argv[1]:
        print("Usage: python get_ddl.py SCHEMA.TABLE_OR_VIEW")
        sys.exit(1)

    schema, name = sys.argv[1].split(".", 1)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)
        full_name = quoted(db, real_schema, real_name)
        sf_kind = "view" if kind == "VIEW" else "table"

        cs.execute("select get_ddl(%s, %s)", (sf_kind, full_name))
        ddl = cs.fetchone()[0]
        print(ddl)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
