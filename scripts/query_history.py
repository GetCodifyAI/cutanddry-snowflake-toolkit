"""
Read-only: lists recent queries from INFORMATION_SCHEMA.QUERY_HISTORY with
start time, user, warehouse, status, duration, rows and query text - for
"why was that slow?" / "what failed?" / "what ran on this warehouse?"
questions. Covers at most the last 7 days.

By default shows your own queries from the last 24 hours. --user shows
another user's, --all-users shows everyone your role can see (much
slower - roughly 30 seconds per day of history), and --warehouse limits
to one warehouse (combine with --all-users for everyone on it).

Usage:
  python query_history.py                          # your last 20 queries, past 24 hours
  python query_history.py --failed                 # only failed queries
  python query_history.py --slowest --hours 72     # slowest first, past 3 days
  python query_history.py --min-seconds 60         # only queries that took a minute or more
  python query_history.py --search ORDERS          # query text contains "ORDERS"
  python query_history.py --user SOME_USER
  python query_history.py --warehouse QUERY --all-users
  python query_history.py --output history.csv     # CSV includes full text, query id, errors
"""

import argparse

from sf_conn import get_connection, current_database, quote_ident, positive_int, print_table, write_csv, run_cli, fmt_count, fmt_bytes, fmt_value, truncate, like_contains

# Queries made by this script are tagged so they can be left out of its own results.
QUERY_TAG = "query_history.py"
# INFORMATION_SCHEMA.QUERY_HISTORY rejects a start time 7 full days back or earlier.
MAX_HOURS = 167
# The most rows the QUERY_HISTORY table functions will return.
RESULT_LIMIT = 10000


def fmt_seconds(ms):
    return f"{ms / 1000:,.1f}" if ms is not None else "n/a"


def main():
    parser = argparse.ArgumentParser(description="List recent queries from Snowflake query history (last 7 days at most).")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--user", help="Show this user's queries instead of your own.")
    scope.add_argument("--all-users", action="store_true", help="Show every user's queries your role can see (much slower unless combined with --warehouse).")
    parser.add_argument("--warehouse", help="Only queries run on this warehouse.")
    parser.add_argument("--hours", type=positive_int, default=24, help=f"How far back to look, in hours (default 24, max {MAX_HOURS}).")
    parser.add_argument("--failed", action="store_true", help="Only failed queries (adds an ERROR column).")
    parser.add_argument("--min-seconds", type=float, metavar="S", help="Only queries that took at least S seconds.")
    parser.add_argument("--search", metavar="TEXT", help="Only queries whose text contains TEXT (case-insensitive).")
    parser.add_argument("--slowest", action="store_true", help="Sort by duration, longest first, instead of most recent first.")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="Max queries to show (default 20).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.hours > MAX_HOURS:
        parser.error(f"--hours can be at most {MAX_HOURS} (query history only covers the last 7 days)")

    db = current_database()

    # Pick the table function that filters server-side: the per-user and
    # per-warehouse variants take about a second, the unfiltered one is slow.
    fn_args = [f"end_time_range_start => dateadd('hour', -{args.hours}, current_timestamp())", f"result_limit => {RESULT_LIMIT}"]
    fn_params, where, params = [], [], []
    if args.all_users and args.warehouse:
        fn, fn_args = "query_history_by_warehouse", ["warehouse_name => %s"] + fn_args
        fn_params.append(args.warehouse)
    elif args.all_users:
        fn = "query_history"
    else:
        fn = "query_history_by_user"
        if args.user:
            fn_args = ["user_name => %s"] + fn_args
            fn_params.append(args.user)
        if args.warehouse:
            where.append("upper(warehouse_name) = upper(%s)")
            params.append(args.warehouse)

    if args.failed:
        where.append("execution_status like 'FAILED%%'")
    if args.min_seconds is not None:
        where.append("elapsed_ms >= %s")
        params.append(args.min_seconds * 1000)
    if args.search:
        where.append("query_text ilike %s escape '\\\\'")
        params.append(like_contains(args.search))

    order = "elapsed_ms desc nulls last" if args.slowest else "start_time desc"
    query = (
        # total_elapsed_time isn't meaningful until a query finishes, so
        # running queries are timed from their start instead.
        "select start_time, user_name, warehouse_name, execution_status, "
        "iff(execution_status = 'RUNNING', datediff('millisecond', start_time, current_timestamp()), total_elapsed_time) as elapsed_ms, "
        "rows_produced, bytes_scanned, query_type, role_name, query_id, error_message, query_text "
        f"from table({quote_ident(db)}.information_schema.{fn}({', '.join(fn_args)})) "
        "where coalesce(query_tag, '') <> %s and not coalesce(is_client_generated_statement, false) "
        + "".join(f"and {w} " for w in where)
        + f"order by {order} limit %s"
    )

    conn = get_connection(session_parameters={"QUERY_TAG": QUERY_TAG})
    try:
        cs = conn.cursor()
        cs.execute(query, tuple(fn_params) + (QUERY_TAG,) + tuple(params) + (args.limit,))
        rows = cs.fetchall()

        who = f"user {args.user}" if args.user else "all users" if args.all_users else "your queries"
        on = f" on warehouse {args.warehouse}" if args.warehouse else ""
        if not rows:
            print(f"No matching queries ({who}{on}, past {args.hours:,} hours).")
            return

        headers = ["START", "USER", "WAREHOUSE", "STATUS", "SECONDS", "ROWS", "QUERY"]
        if args.failed:
            headers.append("ERROR")
        display_rows, csv_rows = [], []
        for start, user, wh, status, elapsed_ms, rows_out, scanned, qtype, role, qid, error, text in rows:
            status = status.replace("FAILED_WITH_ERROR", "FAILED")
            one_line = " ".join((text or "").split()) or "(no query text)"
            display = [fmt_value(start), user, wh or "", status, fmt_seconds(elapsed_ms), fmt_count(rows_out), truncate(one_line)]
            if args.failed:
                display.append(truncate(" ".join((error or "").split())))
            display_rows.append(display)
            csv_rows.append([fmt_value(start), user, role, wh or "", status, qtype, fmt_seconds(elapsed_ms),
                             fmt_count(rows_out), fmt_bytes(scanned), qid, error or "", text or ""])

        sort_note = "longest first" if args.slowest else "most recent first"
        print(f"Query history - {who}{on}, past {args.hours:,} hours, {sort_note}")
        print()
        print_table(headers, display_rows)
        print(f"\n({len(rows):,} queries - use --output to get full query text and query IDs)")

        if args.output:
            write_csv(args.output, ["start", "user", "role", "warehouse", "status", "query_type", "seconds", "rows",
                                    "bytes_scanned", "query_id", "error", "query_text"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
