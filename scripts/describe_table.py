"""
Read-only: shows column definitions (name, type, nullable, default,
comment) plus basic metadata (row count, size, created/last altered) for
a table or view. Works regardless of how the schema/table name was
cased when it was created.

Usage:
  python describe_table.py SCHEMA.TABLE_OR_VIEW
"""

import sys
from sf_conn import get_connection, current_database, resolve_object


def main():
    if len(sys.argv) < 2 or "." not in sys.argv[1]:
        print("Usage: python describe_table.py SCHEMA.TABLE_OR_VIEW")
        sys.exit(1)

    schema, name = sys.argv[1].split(".", 1)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)

        # Metadata (row count, size, timestamps) straight from INFORMATION_SCHEMA.
        cs.execute(
            f'select row_count, bytes, created, last_altered, comment '
            f'from "{db}".information_schema.tables '
            f'where table_schema = %s and table_name = %s',
            (real_schema, real_name),
        )
        row_count, bytes_, created, last_altered, table_comment = cs.fetchone()

        print(f"{real_schema}.{real_name}  [{kind}]")
        rows_display = f"{row_count:,}" if row_count is not None else "n/a"
        size_display = f"{bytes_:,} bytes" if bytes_ is not None else "n/a"
        print(f"  rows: {rows_display}   size: {size_display}")
        print(f"  created: {created}   last_altered: {last_altered}")
        if table_comment:
            print(f"  comment: {table_comment}")
        print()

        # Column definitions.
        cs.execute(
            f'select column_name, data_type, is_nullable, column_default, comment, '
            f'character_maximum_length, numeric_precision, numeric_scale '
            f'from "{db}".information_schema.columns '
            f'where table_schema = %s and table_name = %s '
            f'order by ordinal_position',
            (real_schema, real_name),
        )
        columns = cs.fetchall()

        print(f"{'COLUMN':30} {'TYPE':20} {'NULLABLE':9} {'DEFAULT':15} COMMENT")
        print("-" * 100)
        for col_name, data_type, nullable, default, comment, char_len, num_prec, num_scale in columns:
            type_display = data_type
            if char_len:
                type_display = f"{data_type}({char_len})"
            elif num_prec and num_scale is not None:
                type_display = f"{data_type}({num_prec},{num_scale})"
            print(
                f"{col_name:30} {type_display:20} {nullable:9} "
                f"{(default or ''):15} {comment or ''}"
            )
        print(f"\n({len(columns)} columns)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
