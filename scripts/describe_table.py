"""
Read-only: shows column definitions (name, type, nullable, default,
comment) plus basic metadata (row count, size, created/last altered) for
a table or view. Works regardless of how the schema/table name was
cased when it was created.

Usage:
  python describe_table.py SCHEMA.TABLE_OR_VIEW
  python describe_table.py SCHEMA.TABLE_OR_VIEW --output columns.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, print_table, write_csv, run_cli, info_schema, fmt_count, fmt_bytes


def main():
    parser = argparse.ArgumentParser(description="Describe a table or view's columns and metadata.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("--output", "-o", help="Also write the column list to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)

        # Metadata (row count, size, timestamps) straight from INFORMATION_SCHEMA.
        cs.execute(
            'select row_count, bytes, created, last_altered, comment '
            f'from {info_schema(db, "tables")} '
            'where table_schema = %s and table_name = %s',
            (real_schema, real_name),
        )
        row_count, bytes_, created, last_altered, table_comment = cs.fetchone()

        print(f"{real_schema}.{real_name}  [{kind}]")
        print(f"  rows: {fmt_count(row_count)}   size: {fmt_bytes(bytes_)}")
        print(f"  created: {created}   last_altered: {last_altered}")
        if table_comment:
            print(f"  comment: {table_comment}")
        print()

        # Column definitions.
        cs.execute(
            'select column_name, data_type, is_nullable, column_default, comment, '
            'character_maximum_length, numeric_precision, numeric_scale '
            f'from {info_schema(db, "columns")} '
            'where table_schema = %s and table_name = %s order by ordinal_position',
            (real_schema, real_name),
        )
        columns = cs.fetchall()

        display_rows = []
        for col_name, data_type, nullable, default, comment, char_len, num_prec, num_scale in columns:
            type_display = data_type
            if char_len:
                type_display = f"{data_type}({char_len})"
            elif num_prec and num_scale is not None:
                type_display = f"{data_type}({num_prec},{num_scale})"
            display_rows.append([col_name, type_display, nullable, default or "", comment or ""])

        print_table(["COLUMN", "TYPE", "NULLABLE", "DEFAULT", "COMMENT"], display_rows)
        print(f"\n({len(columns):,} columns)")

        if args.output:
            write_csv(args.output, ["column", "type", "nullable", "default", "comment"], display_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
