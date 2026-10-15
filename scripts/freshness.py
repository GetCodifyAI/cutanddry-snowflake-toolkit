"""
Read-only: answers "when did data last arrive in this table?" from the
data itself. For every DATE/TIMESTAMP column (or just the one you name)
it shows the newest value, how long ago that was, and how many rows have
values in the last 24 hours and 7 days - freshest column first.

Also shows Snowflake's last_altered time for the table, which changes on
any write (insert/update/delete/DDL) even if no timestamp column moved.

Usage:
  python freshness.py SCHEMA.TABLE_OR_VIEW
  python freshness.py SCHEMA.TABLE_OR_VIEW _AIRBYTE_EXTRACTED_AT
  python freshness.py SCHEMA.TABLE_OR_VIEW --output freshness.csv

TIMESTAMP_NTZ values carry no time zone; they're assumed to be UTC (the
usual convention) - use --ntz-timezone if yours are stored in local time,
e.g. --ntz-timezone America/Los_Angeles.

Timestamps stored as text aren't detected - check those with top_values.py
or run_query.py.
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, resolve_column, split_table_arg, quoted, quote_ident, info_schema, print_table, write_csv, run_cli, fmt_count, fmt_value, DATE_TYPES



def fmt_age(seconds):
    """'4 min ago', '5.2 hours ago', '12 days ago', or 'in the future' for negative ages."""
    if seconds is None:
        return ""
    if seconds < 0:
        return "in the future"
    if seconds < 3600:
        return f"{seconds // 60:,} min ago"
    if seconds < 86400:
        return f"{seconds / 3600:,.1f} hours ago"
    return f"{seconds / 86400:,.1f} days ago"


def main():
    parser = argparse.ArgumentParser(description="Show how fresh a table's data is, from its date/timestamp columns.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("column", nargs="?", help="Only check this date/timestamp column (case-insensitive).")
    parser.add_argument("--ntz-timezone", default="UTC", metavar="TZ", help="Time zone TIMESTAMP_NTZ values are stored in (default UTC).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)

        cs.execute(
            f'select row_count, last_altered from {info_schema(db, "tables")} where table_schema = %s and table_name = %s',
            (real_schema, real_name),
        )
        row_count, last_altered = cs.fetchone()
        print(f"{real_schema}.{real_name}  [{kind}]")
        if kind == "VIEW":
            print(f"  view definition last changed: {fmt_value(last_altered)}  (says nothing about its data)")
        else:
            print(f"  rows: {fmt_count(row_count)}   last written: {fmt_value(last_altered)}  (any insert/update/delete/DDL)")
        print()

        if args.column:
            col_name, data_type = resolve_column(conn, db, real_schema, real_name, args.column)
            if data_type not in DATE_TYPES:
                raise SystemExit(f"{col_name} is {data_type}, not a DATE/TIMESTAMP column. Run without a column to check them all.")
            columns = [(col_name, data_type)]
        else:
            cs.execute(
                f'select column_name, data_type from {info_schema(db, "columns")} '
                f'where table_schema = %s and table_name = %s and data_type in ({", ".join(["%s"] * len(DATE_TYPES))}) order by ordinal_position',
                (real_schema, real_name, *sorted(DATE_TYPES)),
            )
            columns = cs.fetchall()
            if not columns:
                print("No DATE/TIMESTAMP columns to check. (Dates stored as text aren't detected - "
                      "try top_values.py or run_query.py on the column.)")
                return

        # One pass over the table for every column: newest, age, and recent row counts.
        # TIMESTAMP_NTZ has no time zone, so "now" is expressed as a wall-clock
        # time in --ntz-timezone before comparing; DATE/LTZ/TZ compare directly.
        parts, params = [], []
        for col_name, data_type in columns:
            col = quote_ident(col_name)
            now = "convert_timezone(%s, current_timestamp())::timestamp_ntz" if data_type == "TIMESTAMP_NTZ" else "current_timestamp()"
            n_now = 8 if data_type == "TIMESTAMP_NTZ" else 0
            # Everything stops at "now": future-dated rows (expiry dates, typo years,
            # placeholders) would otherwise hide when real data last arrived.
            newest = f"max(iff({col} <= {now}, {col}, null))"
            parts += [
                newest,
                f"datediff('second', {newest}, {now})",
                f"count_if({col} >= dateadd('day', -1, {now}) and {col} <= {now})",
                f"count_if({col} >= dateadd('day', -7, {now}) and {col} <= {now})",
                f"count_if({col} > {now})",
                f"count({col})",
            ]
            params += [args.ntz_timezone] * n_now
        cs.execute(f"select {', '.join(parts)} from {quoted(db, real_schema, real_name)}", tuple(params))
        values = list(cs.fetchone())

        results = []
        for col_name, data_type in columns:
            newest, age, last_day, last_week, future, non_null = values[:6]
            del values[:6]
            results.append((col_name, data_type, newest, age, last_day, last_week, future, non_null))
        # Freshest first; columns with nothing at or before now go last.
        results.sort(key=lambda r: (r[3] is None, r[3] if r[3] is not None else 0))

        rows = []
        for col_name, data_type, newest, age, last_day, last_week, future, non_null in results:
            if non_null == 0:
                rows.append([col_name, data_type, "(all NULL)", "", "", ""])
            elif newest is None:
                rows.append([col_name, data_type, "(all in the future)", "", "0", "0"])
            else:
                rows.append([col_name, data_type, fmt_value(newest), fmt_age(age), fmt_count(last_day), fmt_count(last_week)])

        print_table(["COLUMN", "TYPE", "NEWEST", "AGE", "ROWS_LAST_24H", "ROWS_LAST_7D"], rows)
        notes = [f"({len(rows):,} date/timestamp columns, freshest first)"]
        if any(r[1] == "TIMESTAMP_NTZ" for r in results):
            notes.append(f"TIMESTAMP_NTZ values have no time zone; treated as {args.ntz_timezone} (change with --ntz-timezone).")
        for r in results:
            if r[6]:
                notes.append(f"{r[0]}: {r[6]:,} rows dated after now - left out above. Fine for expiry/scheduled dates; "
                             f"otherwise likely typo years or placeholders.")
        print("\n" + "\n".join(notes))

        if args.output:
            write_csv(args.output, ["column", "type", "newest", "age", "rows_last_24h", "rows_last_7d"], rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
