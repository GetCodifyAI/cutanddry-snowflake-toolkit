"""
Read-only: counts a table's rows per day (or hour / week / month) by one
DATE/TIMESTAMP column, over the last N periods - oldest first, with a bar
for the trend. Periods with no rows are flagged, as are periods with far
fewer rows than usual, so you can see when loads stopped or dropped off
rather than just when the newest row arrived (that's freshness.py).

Usage:
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN              # last 30 days
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN --last 90
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN --by hour    # last 48 hours
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN --by month   # last 12 months
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN --timezone America/Los_Angeles
  python daily_counts.py SCHEMA.TABLE_OR_VIEW DATE_COLUMN --output counts.csv

Periods are counted in UTC unless --timezone says otherwise. TIMESTAMP_NTZ
values carry no time zone; they're assumed to be stored in UTC - use
--ntz-timezone if yours are stored in local time (same as freshness.py).
"""

import argparse
import statistics

from sf_conn import get_connection, current_database, resolve_object, resolve_column, split_table_arg, positive_int, quoted, quote_ident, print_table, write_csv, run_cli, fmt_count

DATE_TYPES = {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
DEFAULT_LAST = {"hour": 48, "day": 30, "week": 12, "month": 12}
MAX_PERIODS = 1000
# A complete period with fewer rows than this share of the median is flagged as low.
LOW_SHARE = 0.5
BAR_WIDTH = 30


def fmt_period(p, unit):
    """MM/DD/YY for days/weeks/months, MM/DD/YY HH:00 for hours."""
    if unit == "hour":
        return p.strftime("%m/%d/%y %H:00")
    return p.strftime("%m/%d/%y")


def main():
    parser = argparse.ArgumentParser(description="Count rows per day (or hour/week/month) by a date/timestamp column, flagging gaps and drops.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("column", help="DATE/TIMESTAMP column to count by (case-insensitive).")
    parser.add_argument("--by", choices=["hour", "day", "week", "month"], default="day", help="Period to count per (default day).")
    parser.add_argument("--last", type=positive_int, metavar="N", help="How many periods back to show, including the current one "
                        "(default 48 hours / 30 days / 12 weeks / 12 months).")
    parser.add_argument("--timezone", default="UTC", metavar="TZ", help="Time zone periods are counted in (default UTC), e.g. America/Los_Angeles.")
    parser.add_argument("--ntz-timezone", default="UTC", metavar="TZ", help="Time zone TIMESTAMP_NTZ values are stored in (default UTC).")
    parser.add_argument("--output", "-o", help="Also write the counts to this CSV file.")
    args = parser.parse_args()

    unit = args.by
    last = args.last or DEFAULT_LAST[unit]
    if last > MAX_PERIODS:
        parser.error(f"--last can be at most {MAX_PERIODS:,}")

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, kind = resolve_object(conn, schema, name, db)
        col_name, data_type = resolve_column(conn, db, real_schema, real_name, args.column)
        if data_type not in DATE_TYPES:
            raise SystemExit(f"{col_name} is {data_type}, not a DATE/TIMESTAMP column. freshness.py lists a table's date/timestamp columns.")
        if unit == "hour" and data_type == "DATE":
            raise SystemExit(f"{col_name} is a DATE column - it has no hours. Use --by day, week or month.")

        # Express the column and "now" as wall-clock values in --timezone, so
        # date_trunc() cuts periods at that zone's midnight / hour boundaries.
        col = quote_ident(col_name)
        if data_type == "DATE":
            local = col
        elif data_type == "TIMESTAMP_NTZ":
            local = f"convert_timezone(%(ntz_tz)s, %(tz)s, {col})"
        else:
            local = f"convert_timezone(%(tz)s, {col})::timestamp_ntz"
        now = "convert_timezone(%(tz)s, current_timestamp())::timestamp_ntz" + ("::date" if data_type == "DATE" else "")

        # Every period in the window is generated, so ones with no rows show up as 0
        # instead of going missing. Rows dated after now are left out (and counted below).
        cs.execute(
            f"with periods as ("
            f"  select dateadd({unit}, -(row_number() over (order by seq4()) - 1), date_trunc({unit}, {now})) as p"
            f"  from table(generator(rowcount => {last}))), "
            f"counts as ("
            f"  select date_trunc({unit}, {local}) as p, count(*) as n from {quoted(db, real_schema, real_name)}"
            f"  where {local} >= (select min(p) from periods) and {local} <= {now} group by 1), "
            f"outside as ("
            f"  select count_if({local} > {now}) as future, count_if({col} is null) as nulls,"
            f"  count_if({local} < (select min(p) from periods)) as older,"
            f"  max(iff({local} <= {now}, {local}, null)) as newest from {quoted(db, real_schema, real_name)}) "
            f"select periods.p, coalesce(counts.n, 0), outside.future, outside.nulls, outside.older, outside.newest "
            f"from periods left join counts on counts.p = periods.p cross join outside order by periods.p",
            {"tz": args.timezone, "ntz_tz": args.ntz_timezone},
        )
        rows = cs.fetchall()
        future, nulls, older, newest = rows[0][2:]
        counts = [(p, n) for p, n, *_ in rows]

        tz_note = "" if data_type == "DATE" else f", {args.timezone}"
        header = f"{real_schema}.{real_name}.{col_name}  [{data_type}]  -  rows per {unit}, last {last:,} {unit}s{tz_note}"
        if not any(n for _, n in counts):
            print(header)
            print()
            if newest is None:
                print(f"No rows with a {col_name} at or before now - nothing to count.")
            else:
                print(f"No rows in the window. The newest {col_name} is {fmt_period(newest, unit)} - "
                      f"raise --last (or use --by week/month) to see the counts up to it.")
            return

        # The current period is still filling up, so it isn't judged against the
        # others. Empty periods are left out of the median - a long outage would
        # otherwise drag it to 0 and hide every drop before it.
        complete = [n for _, n in counts[:-1] if n]
        median = round(statistics.median(complete)) if complete else 0
        peak = max(n for _, n in counts) or 1

        display_rows, csv_rows = [], []
        for i, (p, n) in enumerate(counts):
            is_current = i == len(counts) - 1
            if is_current:
                note = "so far (current)"
            elif n == 0:
                note = "NO ROWS"
            elif median and n < LOW_SHARE * median:
                note = f"low ({100 * n / median:.1f}% of median)"
            else:
                note = ""
            bar = "#" * round(BAR_WIDTH * n / peak)
            display_rows.append([fmt_period(p, unit), fmt_count(n), bar, note])
            csv_rows.append([fmt_period(p, unit), fmt_count(n), note])

        print(header)
        print()
        print_table([unit.upper(), "ROWS", "", "NOTE"], display_rows)

        total = sum(n for _, n in counts)
        # Consecutive empty periods are reported as one gap, e.g. "09/25/26 to 10/14/26 (20 days)".
        gaps, start = [], None
        for i, (p, n) in enumerate(counts[:-1]):
            if n == 0 and start is None:
                start = i
            if start is not None and (n or i == len(counts) - 2):
                end = i if n == 0 else i - 1
                gaps.append((counts[start][0], counts[end][0], end - start + 1))
                start = None
        low = sum(1 for _, n in counts[:-1] if n and median and n < LOW_SHARE * median)
        notes = [f"({fmt_count(total)} rows in the window, median {fmt_count(median)} per {unit} that had rows)"]
        if gaps:
            ranges = [fmt_period(a, unit) if k == 1 else f"{fmt_period(a, unit)} to {fmt_period(b, unit)} ({k:,} {unit}s)" for a, b, k in gaps]
            notes.append(f"No rows: {', '.join(ranges[:10])}" + (f" and {len(ranges) - 10:,} more gaps" if len(ranges) > 10 else ""))
        if low:
            notes.append(f"{low:,} {unit}s below {100 * LOW_SHARE:.1f}% of the median.")
        if older:
            notes.append(f"{fmt_count(older)} older rows not shown - raise --last to go further back.")
        if nulls:
            notes.append(f"{fmt_count(nulls)} rows have no {col_name} (NULL) and aren't counted.")
        if future:
            notes.append(f"{fmt_count(future)} rows dated after now aren't counted (expiry/scheduled dates, or typo years).")
        if data_type == "TIMESTAMP_NTZ":
            notes.append(f"TIMESTAMP_NTZ values have no time zone; treated as {args.ntz_timezone} (change with --ntz-timezone).")
        print("\n" + "\n".join(notes))

        if args.output:
            write_csv(args.output, [unit, "rows", "note"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
