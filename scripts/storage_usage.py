"""
Read-only: shows what's taking up storage - for "why is storage so big?"
questions. Each table's bytes are split into active (the live data),
time travel (old versions kept for UNDROP / AT queries), fail-safe (7 more
days Snowflake keeps after time travel, for permanent tables only) and
retained for clones. All of it is billed, so a table that's rewritten in
full every load can cost many times its live size. Dropped tables are
included while they still hold time travel or fail-safe bytes.

By default it lists the biggest tables in the configured database. --by
database totals every database in the account, --by schema every schema in
one database. --dropped keeps only dropped tables. --price adds a monthly
cost estimate.

Sizes come from SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS, which needs
access to the SNOWFLAKE database and can lag up to 90 minutes. GB and TB
are 1,024-based, as Snowflake bills them.

Usage:
  python storage_usage.py                            # biggest 20 tables in SNOWFLAKE_DATABASE
  python storage_usage.py --by database              # every database in the account
  python storage_usage.py --by schema --database CUT_AND_DRY_UNIFIED
  python storage_usage.py --database CUT_AND_DRY_UNIFIED --limit 50
  python storage_usage.py --schema JIRA
  python storage_usage.py --dropped                  # dropped tables still being billed
  python storage_usage.py --by database --price 23   # add cost at $23 per TB per month
  python storage_usage.py --output storage.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_database, resolve_schema, info_schema, positive_int, print_table, write_csv, run_cli, fmt_pct, fmt_value, plural

SOURCE = "snowflake.account_usage.table_storage_metrics"
GB = 1024 ** 3
TB = 1024 ** 4
SIZES = ("sum(active_bytes), sum(time_travel_bytes), sum(failsafe_bytes), sum(retained_for_clone_bytes), "
         "sum(active_bytes + time_travel_bytes + failsafe_bytes + retained_for_clone_bytes) as total")


def fmt_gb(n):
    """GB to one decimal; '<0.1' rather than 0.0 when there's something there."""
    return "<0.1" if 0 < n < 0.05 * GB else f"{n / GB:,.1f}"


def size_columns(active, tt, fs, clone, total, price):
    """Display values for the size columns shared by every view."""
    cols = [fmt_gb(active), fmt_gb(tt), fmt_gb(fs), fmt_gb(clone), fmt_gb(total), fmt_pct(total - active, total)]
    if price:
        cols.append(f"${total / TB * price:,.2f}")
    return cols


def size_headers(price):
    return ["ACTIVE_GB", "TIME_TRAVEL_GB", "FAILSAFE_GB", "CLONE_GB", "TOTAL_GB", "NOT_LIVE_%"] + (["MONTHLY_COST"] if price else [])


def print_footer(rows, what, price):
    """Totals line (rows end with active, tt, fs, clone, total) and how to read it."""
    active, total = sum(r[-5] for r in rows), sum(r[-1] for r in rows)
    tt, fs = sum(r[-4] for r in rows), sum(r[-3] for r in rows)
    cost = f", about ${total / TB * price:,.2f} a month" if price else ""
    print(f"\n({plural(len(rows), what)}, biggest first; {fmt_gb(total)} GB in all{cost} - {fmt_gb(active)} GB live, "
          f"{fmt_gb(tt)} GB time travel, {fmt_gb(fs)} GB fail-safe, {fmt_pct(total - active, total)} not live)")
    print("NOT_LIVE_% is the share held for time travel, fail-safe and clones. High time travel: lower DATA_RETENTION_TIME_IN_DAYS. "
          "High fail-safe on tables reloaded in full: make them TRANSIENT (no fail-safe).")
    print("From ACCOUNT_USAGE, which can lag up to 90 minutes.")


def by_database(args, cs):
    cs.execute(
        f"select table_catalog, count_if(not deleted), count_if(deleted and active_bytes + time_travel_bytes + failsafe_bytes + retained_for_clone_bytes > 0), {SIZES} "
        f"from {SOURCE} where table_catalog is not null group by 1 having total > 0 order by total desc"
    )
    rows = cs.fetchall()
    if not rows:
        print("No table storage found in the account.")
        return

    headers = ["DATABASE", "TABLES", "DROPPED"] + size_headers(args.price)
    csv_rows = [[db, f"{n:,}", f"{dropped:,}"] + size_columns(*sizes, args.price) for db, n, dropped, *sizes in rows]
    print("Storage per database, whole account\n")
    print_table(headers, csv_rows)
    print_footer(rows, "database", args.price)
    print("DROPPED counts dropped tables still holding time travel or fail-safe bytes.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def by_schema(args, cs, db):
    cs.execute(
        f"select table_schema, count_if(not deleted), count_if(deleted and active_bytes + time_travel_bytes + failsafe_bytes + retained_for_clone_bytes > 0), {SIZES} "
        f"from {SOURCE} where table_catalog = %s group by 1 having total > 0 order by total desc",
        (db,),
    )
    rows = cs.fetchall()
    if not rows:
        print(f"No table storage found in database {db}.")
        return

    headers = ["SCHEMA", "TABLES", "DROPPED"] + size_headers(args.price)
    csv_rows = [[schema, f"{n:,}", f"{dropped:,}"] + size_columns(*sizes, args.price) for schema, n, dropped, *sizes in rows]
    print(f"Storage per schema in {db}\n")
    print_table(headers, csv_rows)
    print_footer(rows, "schema", args.price)
    print("DROPPED counts dropped tables still holding time travel or fail-safe bytes.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def by_table(args, cs, db, schema):
    # Retention and table type come from INFORMATION_SCHEMA, so only for tables that still exist.
    cs.execute(
        "select m.table_schema, m.table_name, m.is_transient = 'YES', m.table_dropped, t.retention_time, "
        "m.active_bytes, m.time_travel_bytes, m.failsafe_bytes, m.retained_for_clone_bytes, "
        "m.active_bytes + m.time_travel_bytes + m.failsafe_bytes + m.retained_for_clone_bytes as total "
        f"from {SOURCE} m left join {info_schema(db, 'tables')} t "
        "  on not m.deleted and t.table_schema = m.table_schema and t.table_name = m.table_name "
        "where m.table_catalog = %(db)s and total > 0"
        + (" and m.table_schema = %(schema)s" if schema else "")
        + (" and m.deleted" if args.dropped else "")
        + " order by total desc",
        {"db": db, "schema": schema},
    )
    rows = cs.fetchall()
    scope = f"{db}.{schema}" if schema else db
    what = "dropped tables still billed" if args.dropped else "biggest tables"
    if not rows:
        print(f"No {'dropped ' if args.dropped else ''}tables holding storage found in {scope}.")
        return

    headers = ["TABLE", "TYPE", "RETENTION_DAYS", "DROPPED"] + size_headers(args.price)
    csv_rows = [[f"{sch}.{name}", "TRANSIENT" if transient else "PERMANENT", "" if retention is None else f"{retention:,}",
                 fmt_value(dropped)] + size_columns(*sizes, args.price)
                for sch, name, transient, dropped, retention, *sizes in rows]
    shown = csv_rows[:args.limit]
    print(f"Storage in {scope} - {what}\n")
    print_table(headers, shown)
    if len(rows) > len(shown):
        print(f"\n(showing the top {len(shown):,} of {len(rows):,} - raise --limit or use --output to see them all)")
    print_footer(rows, "table", args.price)
    print("A table name can appear more than once if it was dropped and recreated; DROPPED shows when. "
          "RETENTION_DAYS is blank for dropped tables and ones your role can't see.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="Show what's taking up storage: live data vs time travel, fail-safe and clones.")
    parser.add_argument("--by", choices=["database", "schema"], help="Total per database (whole account) or per schema, instead of per table.")
    parser.add_argument("--database", help="Look at this database instead of SNOWFLAKE_DATABASE (case-insensitive).")
    parser.add_argument("--schema", help="Only tables in this schema (case-insensitive).")
    parser.add_argument("--dropped", action="store_true", help="Only dropped tables that are still being billed.")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="Max tables to show (default 20). --output gets them all.")
    parser.add_argument("--price", type=float, metavar="DOLLARS", help="Storage cost per TB per month, to estimate monthly cost, e.g. 23.")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()

    if args.price is not None and args.price <= 0:
        parser.error("--price must be more than 0")
    if args.by == "database" and (args.database or args.schema):
        parser.error("--by database already covers the whole account; leave out --database and --schema")
    if args.by and (args.dropped or args.schema):
        parser.error("--dropped and --schema only work per table (without --by)")

    conn = get_connection()
    try:
        cs = conn.cursor()
        if args.by == "database":
            by_database(args, cs)
            return
        db = resolve_database(conn, args.database) if args.database else current_database()
        if args.by == "schema":
            by_schema(args, cs, db)
        else:
            schema = resolve_schema(conn, db, args.schema) if args.schema else None
            by_table(args, cs, db, schema)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
