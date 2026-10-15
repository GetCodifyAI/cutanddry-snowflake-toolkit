"""
Read-only: shows how many Snowflake credits each warehouse used - for
"why did the bill go up?" questions. By default it compares the last 30
days with the 30 days before, per warehouse, with the change and each
warehouse's share of the total. --by day/week/month shows the trend over
time instead (all warehouses together, or one with --warehouse).

Credits come from SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY, which
needs access to the SNOWFLAKE database, keeps a year of history and can
lag up to 3 hours. Days are UTC days. Pass --price with your cost per
credit to also see dollars.

Usage:
  python warehouse_usage.py                          # last 30 days vs the 30 before, per warehouse
  python warehouse_usage.py --days 7                 # last 7 days vs the 7 before
  python warehouse_usage.py --by day                 # credits per day, all warehouses
  python warehouse_usage.py --by day --warehouse APP # one warehouse's daily credits
  python warehouse_usage.py --by month --days 365
  python warehouse_usage.py --price 3.00             # add cost in dollars
  python warehouse_usage.py --output usage.csv
"""

import argparse

from sf_conn import get_connection, positive_int, print_table, write_csv, run_cli, fmt_pct, plural

# ACCOUNT_USAGE keeps a year of metering history.
MAX_DAYS = 365
BAR_WIDTH = 30
SOURCE = "snowflake.account_usage.warehouse_metering_history"


def fmt_credits(n):
    return f"{n:,.2f}"


def fmt_diff(n):
    """Signed credit difference; anything that rounds to zero shows as 0.00, not -0.00."""
    return "0.00" if round(n, 2) == 0 else f"{n:+,.2f}"


def fmt_cost(n, price):
    return f"${n * price:,.2f}"


def fmt_change(cur, prev):
    """'+12.3%', '-4.0%', '0.0%', or 'new' when there was nothing before."""
    if not prev:
        return "new" if cur else ""
    pct = fmt_pct(cur - prev, prev)
    return "+" + pct if cur > prev else pct


def compare_periods(args, cs):
    cs.execute(
        "select warehouse_name, "
        "sum(iff(start_time >= dateadd('day', -%(days)s, current_timestamp()), credits_used, 0)) as cur, "
        "sum(iff(start_time < dateadd('day', -%(days)s, current_timestamp()), credits_used, 0)) as prev, "
        "sum(iff(start_time >= dateadd('day', -%(days)s, current_timestamp()), credits_used_cloud_services, 0)) as cloud "
        f"from {SOURCE} where start_time >= dateadd('day', -2 * %(days)s, current_timestamp()) "
        + ("and upper(warehouse_name) = upper(%(wh)s) " if args.warehouse else "")
        + "group by 1 order by cur desc, prev desc",
        {"days": args.days, "wh": args.warehouse},
    )
    rows = [(wh, float(cur), float(prev), float(cloud)) for wh, cur, prev, cloud in cs.fetchall()]
    if not rows:
        print(f"No credit usage found{' for warehouse ' + args.warehouse if args.warehouse else ''} in the past {plural(2 * args.days, 'day')}.")
        return

    total_cur, total_prev = sum(r[1] for r in rows), sum(r[2] for r in rows)
    headers = ["WAREHOUSE", f"LAST_{args.days}D", f"PREVIOUS_{args.days}D", "CHANGE", "CHANGE_%", "SHARE_%", "CLOUD_SERVICES"]
    if args.price:
        headers[2:2] = [f"COST_{args.days}D"]
    display_rows, csv_rows = [], []
    for wh, cur, prev, cloud in rows + [("TOTAL", total_cur, total_prev, sum(r[3] for r in rows))]:
        row = [wh, fmt_credits(cur), fmt_credits(prev), fmt_diff(cur - prev), fmt_change(cur, prev), fmt_pct(cur, total_cur), fmt_credits(cloud)]
        if args.price:
            row[2:2] = [fmt_cost(cur, args.price)]
        display_rows.append(row)
        csv_rows.append(row)

    print(f"Credits used - last {plural(args.days, 'day')} vs the {plural(args.days, 'day')} before, per warehouse\n")
    print_table(headers, display_rows[:-1] + [["-" * len(h) for h in headers], display_rows[-1]])
    print(f"\n({plural(len(rows), 'warehouse')}, biggest first. Total {fmt_change(total_cur, total_prev)} vs the previous period."
          f" CLOUD_SERVICES is the part spent on cloud services, already included in the credits.)")
    print("From ACCOUNT_USAGE, which can lag up to 3 hours.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def trend(args, cs):
    unit = args.by
    cs.execute(
        f"select date_trunc({unit}, convert_timezone('UTC', start_time))::date as p, sum(credits_used), count(distinct warehouse_name) "
        f"from {SOURCE} where start_time >= dateadd('day', -%(days)s, current_timestamp()) "
        + ("and upper(warehouse_name) = upper(%(wh)s) " if args.warehouse else "")
        + "group by 1 order by 1",
        {"days": args.days, "wh": args.warehouse},
    )
    rows = [(p, float(n), whs) for p, n, whs in cs.fetchall()]
    scope = f"warehouse {args.warehouse.upper()}" if args.warehouse else "all warehouses"
    if not rows:
        print(f"No credit usage found for {scope} in the past {plural(args.days, 'day')}.")
        return

    peak = max(n for _, n, _ in rows) or 1
    headers = [unit.upper(), "CREDITS"] + (["COST"] if args.price else []) + ([] if args.warehouse else ["WAREHOUSES"]) + [""]
    display_rows, csv_rows = [], []
    for p, n, whs in rows:
        row = [p.strftime("%m/%d/%y"), fmt_credits(n)] + ([fmt_cost(n, args.price)] if args.price else []) + ([f"{whs:,}"] if not args.warehouse else [])
        csv_rows.append(row)
        display_rows.append(row + ["#" * round(BAR_WIDTH * n / peak)])

    total = sum(n for _, n, _ in rows)
    print(f"Credits per {unit} (UTC) - {scope}, past {plural(args.days, 'day')}\n")
    print_table(headers, display_rows)
    cost = f" ({fmt_cost(total, args.price)})" if args.price else ""
    print(f"\n({fmt_credits(total)} credits{cost} over {plural(len(rows), unit)}; the first and last {unit} may be partial. "
          "From ACCOUNT_USAGE, which can lag up to 3 hours.)")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers if h], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="Show credits used per warehouse, compared with the previous period, or as a trend over time.")
    parser.add_argument("--days", type=positive_int, default=30, help="How many days to look at (default 30). Without --by, compared with the same number of days before.")
    parser.add_argument("--by", choices=["day", "week", "month"], help="Show credits per day/week/month instead of per warehouse.")
    parser.add_argument("--warehouse", help="Only this warehouse (case-insensitive).")
    parser.add_argument("--price", type=float, metavar="DOLLARS", help="Cost per credit, to also show dollars, e.g. 3.00.")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    limit = MAX_DAYS if args.by else MAX_DAYS // 2
    if args.days > limit:
        parser.error(f"--days can be at most {limit:,}" + ("" if args.by else " (the previous period has to fit in a year of history too)"))
    if args.price is not None and args.price <= 0:
        parser.error("--price must be more than 0")

    conn = get_connection()
    try:
        cs = conn.cursor()
        if args.by:
            trend(args, cs)
        else:
            compare_periods(args, cs)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
