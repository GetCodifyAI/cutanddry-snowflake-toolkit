"""
Read-only: profiles every column of a table or view - null count and %,
distinct count, min and max - in a single aggregate query. Distinct
counts are approximate (HyperLogLog, typically within ~1%) by default for
speed; use --exact for exact counts, which can be much slower on wide
tables (minutes for a few hundred columns). On very large tables,
--sample profiles a random subset of rows instead of the whole table.

Usage:
  python profile_table.py SCHEMA.TABLE_OR_VIEW
  python profile_table.py SCHEMA.TABLE_OR_VIEW --exact
  python profile_table.py SCHEMA.TABLE_OR_VIEW --sample 100000
  python profile_table.py SCHEMA.TABLE_OR_VIEW --output profile.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, positive_int, quoted, quote_ident, print_table, write_csv, run_cli, info_schema, fmt_count, fmt_value, fmt_pct, truncate, JSON_TYPES, UNGROUPABLE_TYPES, plural

# UNGROUPABLE_TYPES columns get only a null count. JSON_TYPES columns get no
# MIN/MAX (ARRAY/OBJECT fail outright; for VARIANT the result isn't
# meaningful), but distinct values are still counted.

def main():
    parser = argparse.ArgumentParser(description="Profile every column of a table or view: nulls, distinct values, min/max.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("--exact", action="store_true", help="Exact distinct counts instead of approximate (much slower on wide or big tables).")
    parser.add_argument("--sample", type=positive_int, metavar="N", help="Profile a random sample of N rows instead of the whole table.")
    parser.add_argument("--output", "-o", help="Also write the profile to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)

        cs.execute(
            f'select column_name, data_type from {info_schema(db, "columns")} '
            'where table_schema = %s and table_name = %s order by ordinal_position',
            (real_schema, real_name),
        )
        columns = cs.fetchall()

        # One aggregate query over the whole table: count(*), then per column
        # count(col) and - where the type supports it - distinct, min, max.
        distinct_fn = "count(distinct {})" if args.exact else "approx_count_distinct({})"
        select_parts = ["count(*)"]
        for col_name, data_type in columns:
            col = quote_ident(col_name)
            select_parts.append(f"count({col})")
            if data_type not in UNGROUPABLE_TYPES:
                select_parts.append(distinct_fn.format(col))
            if data_type not in UNGROUPABLE_TYPES | JSON_TYPES:
                select_parts += [f"min({col})", f"max({col})"]

        source = quoted(db, real_schema, real_name)
        if args.sample:
            source += f" sample ({args.sample} rows)"
        cs.execute(f"select {', '.join(select_parts)} from {source}")
        result = list(cs.fetchone())

        total = result.pop(0)
        display_rows, csv_rows = [], []
        for col_name, data_type in columns:
            nulls = total - result.pop(0)
            distinct, min_v, max_v = "n/a", "n/a", "n/a"
            if data_type not in UNGROUPABLE_TYPES:
                # The approximate count can overshoot slightly; it can never really exceed the non-null count.
                distinct = fmt_count(min(result.pop(0), total - nulls))
            if data_type not in UNGROUPABLE_TYPES | JSON_TYPES:
                min_v, max_v = fmt_value(result.pop(0)), fmt_value(result.pop(0))
            row = [col_name, data_type, fmt_count(nulls), fmt_pct(nulls, total), distinct, min_v, max_v]
            csv_rows.append(row)
            display_rows.append(row[:5] + [truncate(min_v), truncate(max_v)])

        scope = f"random sample of {plural(total, 'row')}" if args.sample else plural(total, "row")
        print(f"{real_schema}.{real_name}  [{kind}]  -  {scope}")
        print()

        distinct_header = "DISTINCT" if args.exact else "DISTINCT (~)"
        print_table(["COLUMN", "TYPE", "NULLS", "NULL_%", distinct_header, "MIN", "MAX"], display_rows)
        print(f"\n({plural(len(columns), 'column')})")
        if not args.exact:
            print("Distinct counts are approximate - use --exact for exact counts.")

        if args.output:
            write_csv(args.output, ["column", "type", "nulls", "null_pct", "distinct", "min", "max"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
