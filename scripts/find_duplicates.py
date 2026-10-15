"""
Read-only: checks whether a column - or a combination of columns - is
really unique in a table or view. Shows how many key values appear more
than once, how many extra rows that adds, and the most-repeated keys with
their counts. Handy for Airbyte tables, where re-syncs can leave the same
record behind more than once.

NULL is treated as a value of its own, so rows whose key is NULL count as
duplicates of each other.

Usage:
  python find_duplicates.py SCHEMA.TABLE_OR_VIEW COLUMN
  python find_duplicates.py SCHEMA.TABLE_OR_VIEW COL_A COL_B         # combination of columns
  python find_duplicates.py SCHEMA.TABLE_OR_VIEW COLUMN --limit 50
  python find_duplicates.py SCHEMA.TABLE_OR_VIEW COLUMN --sample 100000
  python find_duplicates.py SCHEMA.TABLE_OR_VIEW COLUMN --output dupes.csv

Column names are case-insensitive; quote names with spaces in the shell.
To see the full rows behind a duplicated key, use run_query.py.
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, resolve_column, split_table_arg, positive_int, quoted, quote_ident, print_table, write_csv, run_cli, fmt_count, fmt_pct, fmt_cell, json_columns, truncate

# Types Snowflake can't GROUP BY.
UNGROUPABLE_TYPES = {"MAP", "GEOGRAPHY", "GEOMETRY", "VECTOR"}


def main():
    parser = argparse.ArgumentParser(description="Check whether a column (or combination of columns) is unique, and show the duplicated values.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("columns", nargs="+", metavar="column", help="Key column(s) to check, case-insensitive. Several columns are checked as a combination.")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="How many duplicated keys to show (default 20).")
    parser.add_argument("--sample", type=positive_int, metavar="N", help="Check a random sample of N rows instead of the whole table.")
    parser.add_argument("--output", "-o", help="Also write the duplicated keys shown to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)
        key_cols = []
        for column in args.columns:
            col_name, data_type = resolve_column(conn, db, real_schema, real_name, column)
            if data_type in UNGROUPABLE_TYPES:
                raise SystemExit(f"Can't check {col_name}: {data_type} columns can't be grouped.")
            if col_name in key_cols:
                parser.error(f"column {col_name} given more than once")
            key_cols.append(col_name)

        source = quoted(db, real_schema, real_name)
        if args.sample:
            source += f" sample ({args.sample} rows)"

        # The window functions run over every key before the limit applies, so
        # the totals cover the whole table. The most-repeated keys sort first;
        # if even the top one appears only once, the key is unique.
        cols = ", ".join(quote_ident(c) for c in key_cols)
        cs.execute(
            f"with keys as (select {cols}, count(*) as n from {source} group by {cols}) "
            f"select {cols}, n, sum(n) over () as total_rows, count(*) over () as distinct_keys, "
            "sum(iff(n > 1, 1, 0)) over () as dup_keys, sum(iff(n > 1, n - 1, 0)) over () as extra_rows "
            f"from keys order by n desc, {cols} nulls last limit %s",
            (args.limit,),
        )
        json_cols = json_columns(cs.description)[:len(key_cols)]
        rows = cs.fetchall()

        label = f"{real_schema}.{real_name} ({', '.join(key_cols)})"
        scope = "random sample of " if args.sample else ""
        if not rows:
            print(f"{label}  [{kind}]  -  {scope}0 rows, nothing to check.")
            return
        total, distinct_keys, dup_keys, extra_rows = rows[0][-4:]

        print(f"{label}  [{kind}]  -  {scope}{fmt_count(total)} rows, {fmt_count(distinct_keys)} distinct keys")
        print()
        if not dup_keys:
            print(f"Unique - every key appears exactly once{' in the sample' if args.sample else ''}.")
            return

        display_rows, csv_rows = [], []
        for row in rows:
            n = row[len(key_cols)]
            if n < 2:
                break
            values = ["(null)" if v is None else fmt_cell(v, is_json) for v, is_json in zip(row, json_cols)]
            csv_rows.append(values + [fmt_count(n)])
            display_rows.append([truncate(v) for v in values] + [fmt_count(n)])

        print_table(key_cols + ["COUNT"], display_rows)
        shown = f"top {len(display_rows):,} of " if len(display_rows) < dup_keys else ""
        print(f"\n(NOT unique - {shown}{fmt_count(dup_keys)} keys appear more than once "
              f"({fmt_pct(dup_keys, distinct_keys)} of keys), adding {fmt_count(extra_rows)} extra rows "
              f"({fmt_pct(extra_rows, total)} of rows))")
        if args.sample:
            print("Checked a random sample only - the whole table may have more duplicates.")

        if args.output:
            write_csv(args.output, key_cols + ["count"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
