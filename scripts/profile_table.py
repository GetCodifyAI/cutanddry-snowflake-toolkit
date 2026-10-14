"""
Read-only: profiles every column of a table or view - null count and %,
distinct count, min and max - in a single aggregate query. Distinct
counts are approximate (HyperLogLog, typically within ~1%) by default for
speed; use --exact for exact counts, which can be much slower on wide
tables (minutes for a few hundred columns). On very large tables, --sample profiles a random
subset of rows instead of the whole table.

Usage:
  python profile_table.py SCHEMA.TABLE_OR_VIEW
  python profile_table.py SCHEMA.TABLE_OR_VIEW --exact
  python profile_table.py SCHEMA.TABLE_OR_VIEW --sample 100000
  python profile_table.py SCHEMA.TABLE_OR_VIEW --output profile.csv
"""

import argparse
import datetime
import decimal

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, positive_int, quoted, quote_ident, print_table, write_csv, run_cli, info_schema, fmt_count

# Types Snowflake can't take MIN/MAX on (ARRAY/OBJECT fail outright; for
# VARIANT the result isn't meaningful) - distinct is still counted for these.
NO_MIN_MAX_TYPES = {"VARIANT", "OBJECT", "ARRAY"}
# Types where only the null count is profiled.
NO_STATS_TYPES = {"MAP", "GEOGRAPHY", "GEOMETRY", "VECTOR"}

# Min/max values longer than this are cut short in the terminal (the CSV keeps them whole).
MAX_DISPLAY_WIDTH = 40


def fmt_value(v):
    """Format a min/max value for display: commas on numbers, MM/DD/YY on dates."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float, decimal.Decimal)):
        return f"{v:,}"
    if isinstance(v, datetime.datetime):
        return v.strftime("%m/%d/%y %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%m/%d/%y")
    if isinstance(v, bytes):
        return v.hex()
    if v == "":
        return "(empty string)"  # otherwise indistinguishable from NULL
    return str(v).replace("\n", " ").replace("\r", " ")


def truncate(s):
    return s if len(s) <= MAX_DISPLAY_WIDTH else s[:MAX_DISPLAY_WIDTH - 3] + "..."


def fmt_pct(part, whole):
    """Percentage with one decimal place, or 'n/a' for an empty table."""
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


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
            if data_type not in NO_STATS_TYPES:
                select_parts.append(distinct_fn.format(col))
            if data_type not in NO_STATS_TYPES | NO_MIN_MAX_TYPES:
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
            if data_type not in NO_STATS_TYPES:
                # The approximate count can overshoot slightly; it can never really exceed the non-null count.
                distinct = fmt_count(min(result.pop(0), total - nulls))
            if data_type not in NO_STATS_TYPES | NO_MIN_MAX_TYPES:
                min_v, max_v = fmt_value(result.pop(0)), fmt_value(result.pop(0))
            row = [col_name, data_type, fmt_count(nulls), fmt_pct(nulls, total), distinct, min_v, max_v]
            csv_rows.append(row)
            display_rows.append(row[:5] + [truncate(min_v), truncate(max_v)])

        scope = f"random sample of {fmt_count(total)} rows" if args.sample else f"{fmt_count(total)} rows"
        print(f"{real_schema}.{real_name}  [{kind}]  -  {scope}")
        print()

        distinct_header = "DISTINCT" if args.exact else "DISTINCT (~)"
        print_table(["COLUMN", "TYPE", "NULLS", "NULL_%", distinct_header, "MIN", "MAX"], display_rows)
        print(f"\n({len(columns):,} columns)")
        if not args.exact:
            print("Distinct counts are approximate - use --exact for exact counts.")

        if args.output:
            write_csv(args.output, ["column", "type", "nulls", "null_pct", "distinct", "min", "max"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
