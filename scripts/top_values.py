"""
Read-only: shows the most frequent values in one column of a table or
view, with each value's row count, % of rows, and cumulative %. NULL is
counted as a value of its own. Handy for status/type columns and for
spotting bad or unexpected data.

Usage:
  python top_values.py SCHEMA.TABLE_OR_VIEW COLUMN
  python top_values.py SCHEMA.TABLE_OR_VIEW COLUMN --limit 50
  python top_values.py SCHEMA.TABLE_OR_VIEW COLUMN --sample 100000
  python top_values.py SCHEMA.TABLE_OR_VIEW COLUMN --output values.csv

Column names are case-insensitive too; quote names with spaces in the
shell, e.g.  python top_values.py MYSCHEMA.MYTABLE "Billing Status"
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, resolve_column, split_table_arg, positive_int, quoted, quote_ident, print_table, write_csv, run_cli, fmt_count, fmt_value, fmt_pct, truncate

# Types Snowflake can't GROUP BY.
UNGROUPABLE_TYPES = {"MAP", "GEOGRAPHY", "GEOMETRY", "VECTOR"}
# Semi-structured types come back pretty-printed over several lines; these
# are shown as compact one-line JSON instead.
JSON_TYPES = {"VARIANT", "OBJECT", "ARRAY"}


def main():
    parser = argparse.ArgumentParser(description="Show the most frequent values in one column of a table or view.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("column", help="Column name (case-insensitive).")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="How many top values to show (default 20).")
    parser.add_argument("--sample", type=positive_int, metavar="N", help="Count over a random sample of N rows instead of the whole table.")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)
        col_name, data_type = resolve_column(conn, db, real_schema, real_name, args.column)
        if data_type in UNGROUPABLE_TYPES:
            raise SystemExit(f"Can't count values of a {data_type} column.")

        source = quoted(db, real_schema, real_name)
        if args.sample:
            source += f" sample ({args.sample} rows)"

        # The window functions run over every group before the limit applies,
        # so total rows and distinct-value count cover the whole table.
        col = quote_ident(col_name)
        value_expr = f"to_json({col})" if data_type in JSON_TYPES else col
        cs.execute(
            f"select {value_expr}, count(*) as n, sum(count(*)) over () as total_rows, count(*) over () as distinct_values "
            f"from {source} group by {col} order by n desc, {col} nulls last limit %s",
            (args.limit,),
        )
        rows = cs.fetchall()

        scope = "random sample of " if args.sample else ""
        if not rows:
            print(f"{real_schema}.{real_name}  [{kind}]  -  {scope}0 rows, nothing to count.")
            return
        total, n_distinct = rows[0][2], rows[0][3]

        display_rows, csv_rows = [], []
        running = 0
        for value, n, _, _ in rows:
            running += n
            shown = "(null)" if value is None else fmt_value(value)
            row = [shown, fmt_count(n), fmt_pct(n, total), fmt_pct(running, total)]
            csv_rows.append(row)
            display_rows.append([truncate(shown)] + row[1:])

        print(f"{real_schema}.{real_name}.{col_name}  [{data_type}]  -  {scope}{fmt_count(total)} rows")
        print()
        print_table(["VALUE", "COUNT", "%", "CUMULATIVE_%"], display_rows)
        print(f"\n(top {len(rows):,} of {fmt_count(n_distinct)} distinct values, NULL counted as one - covers {fmt_pct(running, total)} of rows)")

        if args.output:
            write_csv(args.output, ["value", "count", "pct", "cumulative_pct"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
