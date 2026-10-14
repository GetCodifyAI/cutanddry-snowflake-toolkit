"""
Read-only: prints the full CREATE TABLE/VIEW statement (DDL) for a table
or view, exactly as Snowflake would use to recreate it (constraints,
clustering keys, view definitions, etc. all in one place).

Usage:
  python get_ddl.py SCHEMA.TABLE_OR_VIEW
  python get_ddl.py SCHEMA.TABLE_OR_VIEW --output table.sql
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, quoted, run_cli


def main():
    parser = argparse.ArgumentParser(description="Print the CREATE TABLE/VIEW DDL for a table or view.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("--output", "-o", help="Also write the DDL to this file (e.g. table.sql).")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
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

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(ddl + "\n")
            print(f"\nWrote DDL to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
