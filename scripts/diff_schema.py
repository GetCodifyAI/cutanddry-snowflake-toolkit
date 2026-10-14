"""
Read-only: compares two schemas in the configured database and reports
what's different - tables/views present in only one of them, and for
tables/views present in both, any columns that were added, removed, or
changed type/nullability. Handy for environments with versioned schema
copies (e.g. DTAM / DTAM_V2 / DTAM_V3).

Usage:
  python diff_schema.py SCHEMA_A SCHEMA_B
  python diff_schema.py SCHEMA_A SCHEMA_B --output diff.csv
"""

import argparse

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, info_schema, resolve_schema


def fetch_tables(conn, db, schema):
    """Return {table_name: kind} for every table/view in a schema."""
    cs = conn.cursor()
    cs.execute(
        f'select table_name, table_type from {info_schema(db, "tables")} where table_schema = %s',
        (schema,),
    )
    return {name: ttype for name, ttype in cs.fetchall()}


def fetch_columns(conn, db, schema):
    """Return {table_name: {column_name: (data_type, is_nullable)}} for a schema."""
    cs = conn.cursor()
    cs.execute(
        f'select table_name, column_name, data_type, is_nullable '
        f'from {info_schema(db, "columns")} where table_schema = %s',
        (schema,),
    )
    columns = {}
    for table_name, col_name, data_type, nullable in cs.fetchall():
        columns.setdefault(table_name, {})[col_name] = (data_type, nullable)
    return columns


def main():
    parser = argparse.ArgumentParser(description="Compare two schemas' tables/views and columns.")
    parser.add_argument("schema_a", help="First schema to compare.")
    parser.add_argument("schema_b", help="Second schema to compare.")
    parser.add_argument("--output", "-o", help="Also write the full diff to this CSV file.")
    args = parser.parse_args()

    db = current_database()

    conn = get_connection()
    try:
        real_a = resolve_schema(conn, db, args.schema_a)
        real_b = resolve_schema(conn, db, args.schema_b)

        tables_a = fetch_tables(conn, db, real_a)
        tables_b = fetch_tables(conn, db, real_b)

        only_in_a = sorted(set(tables_a) - set(tables_b))
        only_in_b = sorted(set(tables_b) - set(tables_a))
        common = sorted(set(tables_a) & set(tables_b))

        cols_a = fetch_columns(conn, db, real_a)
        cols_b = fetch_columns(conn, db, real_b)

        display_rows = []

        for name in only_in_a:
            display_rows.append(["TABLE_ONLY_IN_A", name, f"only in {real_a}"])
        for name in only_in_b:
            display_rows.append(["TABLE_ONLY_IN_B", name, f"only in {real_b}"])

        for name in common:
            a_cols = cols_a.get(name, {})
            b_cols = cols_b.get(name, {})

            added = sorted(set(b_cols) - set(a_cols))
            removed = sorted(set(a_cols) - set(b_cols))
            shared = sorted(set(a_cols) & set(b_cols))

            for col in added:
                data_type, nullable = b_cols[col]
                display_rows.append(["COLUMN_ADDED", f"{name}.{col}", f"{data_type}, nullable={nullable} (in {real_b} only)"])
            for col in removed:
                data_type, nullable = a_cols[col]
                display_rows.append(["COLUMN_REMOVED", f"{name}.{col}", f"{data_type}, nullable={nullable} (in {real_a} only)"])
            for col in shared:
                if a_cols[col] != b_cols[col]:
                    a_type, a_null = a_cols[col]
                    b_type, b_null = b_cols[col]
                    display_rows.append([
                        "COLUMN_CHANGED",
                        f"{name}.{col}",
                        f"{real_a}: {a_type}, nullable={a_null}  ->  {real_b}: {b_type}, nullable={b_null}",
                    ])

        if not display_rows:
            print(f"No differences found between {real_a} and {real_b}.")
            return

        print(f"Diff: {real_a} vs {real_b}\n")
        print_table(["DIFF", "OBJECT", "DETAIL"], display_rows)
        print(f"\n({len(display_rows)} differences; {len(common)} tables/views compared in common)")

        if args.output:
            n = write_csv(args.output, ["diff", "object", "detail"], display_rows)
            print(f"Wrote {n} rows to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
