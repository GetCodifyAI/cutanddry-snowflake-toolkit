"""
Read-only: compares two schemas and reports
what's different - tables/views present in only one of them, and for
tables/views present in both, any that switched between table and view,
and any columns that were added, removed, or changed type/nullability.
Types are compared in full, so NUMBER(38,0) -> NUMBER(10,2) or
VARCHAR(16) -> VARCHAR(256) counts as a change. Handy for environments
with versioned schema copies (e.g. DTAM / DTAM_V2 / DTAM_V3).

Either schema can be DATABASE.SCHEMA to compare across databases;
otherwise SNOWFLAKE_DATABASE from .env is used.

Usage:
  python diff_schema.py SCHEMA_A SCHEMA_B
  python diff_schema.py SCHEMA_A OTHER_DB.SCHEMA_B
  python diff_schema.py SCHEMA_A SCHEMA_B --output diff.csv
"""

import argparse

from sf_conn import get_connection, resolve_target, print_table, write_csv, run_cli, info_schema, plural, fmt_type, kind_from_type


def fetch_tables(conn, db, schema):
    """Return {table/view name: 'TABLE' or 'VIEW'} for a schema."""
    cs = conn.cursor()
    cs.execute(
        f'select table_name, table_type from {info_schema(db, "tables")} where table_schema = %s',
        (schema,),
    )
    return {name: kind_from_type(ttype) for name, ttype in cs.fetchall()}


def fetch_columns(conn, db, schema):
    """
    Return {table_name: {column_name: (full_type, is_nullable)}} for a schema.
    The full type includes length/precision, so NUMBER(38,0) -> NUMBER(10,2)
    or VARCHAR(16) -> VARCHAR(256) count as changes.
    """
    cs = conn.cursor()
    cs.execute(
        f'select table_name, column_name, data_type, character_maximum_length, numeric_precision, numeric_scale, is_nullable '
        f'from {info_schema(db, "columns")} where table_schema = %s',
        (schema,),
    )
    columns = {}
    for table_name, col_name, data_type, char_len, num_prec, num_scale, nullable in cs.fetchall():
        columns.setdefault(table_name, {})[col_name] = (fmt_type(data_type, char_len, num_prec, num_scale), nullable)
    return columns


def main():
    parser = argparse.ArgumentParser(description="Compare two schemas' tables/views and columns.")
    parser.add_argument("schema_a", help="First schema (SCHEMA or DATABASE.SCHEMA).")
    parser.add_argument("schema_b", help="Second schema (SCHEMA or DATABASE.SCHEMA).")
    parser.add_argument("--output", "-o", help="Also write the full diff to this CSV file.")
    args = parser.parse_args()

    conn = get_connection()
    try:
        db_a, schema_a = resolve_target(conn, args.schema_a)
        db_b, schema_b = resolve_target(conn, args.schema_b)
        if (db_a, schema_a) == (db_b, schema_b):
            parser.error("both arguments are the same schema")
        # Names in the output: just the schema, or DATABASE.SCHEMA when comparing across databases.
        cross_db = db_a != db_b
        real_a = f"{db_a}.{schema_a}" if cross_db else schema_a
        real_b = f"{db_b}.{schema_b}" if cross_db else schema_b

        tables_a = fetch_tables(conn, db_a, schema_a)
        tables_b = fetch_tables(conn, db_b, schema_b)

        only_in_a = sorted(set(tables_a) - set(tables_b))
        only_in_b = sorted(set(tables_b) - set(tables_a))
        common = sorted(set(tables_a) & set(tables_b))

        cols_a = fetch_columns(conn, db_a, schema_a)
        cols_b = fetch_columns(conn, db_b, schema_b)

        display_rows = []

        for name in only_in_a:
            display_rows.append(["TABLE_ONLY_IN_A", name, f"only in {real_a}"])
        for name in only_in_b:
            display_rows.append(["TABLE_ONLY_IN_B", name, f"only in {real_b}"])

        for name in common:
            if tables_a[name] != tables_b[name]:
                display_rows.append(["KIND_CHANGED", name, f"{real_a}: {tables_a[name]}  ->  {real_b}: {tables_b[name]}"])
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
        print(f"\n({plural(len(display_rows), 'difference')}; {plural(len(common), 'table/view', 'tables/views')} compared in common)")

        if args.output:
            write_csv(args.output, ["diff", "object", "detail"], display_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
