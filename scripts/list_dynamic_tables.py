"""
Read-only: lists dynamic tables (a distinct Snowflake object type - a table
whose contents are kept in sync on a schedule via `SHOW DYNAMIC TABLES`,
separate from plain tables/views) in the configured database, showing target
lag, warehouse, refresh mode, scheduling state, row count, and size.
Optionally scoped to one schema.

--refreshes shows one dynamic table's recent refreshes instead: when each
ran, whether it succeeded, full or incremental, how long it waited in the
warehouse queue vs actually ran, rows inserted/deleted, and any error.
Refresh history comes from INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY,
which only goes back about 7 days. --failing lists only the dynamic
tables with a failed refresh in that window, with the latest error.

Usage:
  python list_dynamic_tables.py                       # whole database
  python list_dynamic_tables.py --schema SOME_SCHEMA   # just one schema
  python list_dynamic_tables.py --failing                # only ones with failed refreshes
  python list_dynamic_tables.py --refreshes SCHEMA.DYNAMIC_TABLE
  python list_dynamic_tables.py --refreshes SCHEMA.DYNAMIC_TABLE --limit 50
  python list_dynamic_tables.py --output dynamic_tables.csv
"""

import argparse
import json

from sf_conn import get_connection, current_database, print_table, write_csv, run_cli, fmt_count, fmt_bytes, resolve_schema, quoted, quote_ident, fmt_value, plural, show_rows, positive_int, split_table_arg, truncate


def fmt_seconds(ms):
    return f"{ms / 1000:,.1f}" if ms is not None else ""


def list_all(args, conn, db):
    cs = conn.cursor()
    if args.schema:
        real_schema = resolve_schema(conn, db, args.schema)
        rows = show_rows(cs, f"show dynamic tables in schema {quoted(db, real_schema)}")
    else:
        rows = show_rows(cs, f"show dynamic tables in database {quoted(db)}")

    if not rows:
        scope = f"schema '{args.schema}'" if args.schema else f"database {db}"
        print(f"No dynamic tables found in {scope}.")
        return

    failures = {}
    if args.failing:
        # Without a name the history covers every database in the account, so filter to this one.
        cs.execute(
            "select schema_name, name, count(*), max(refresh_start_time), max_by(state_message, refresh_start_time) "
            f"from table({quote_ident(db)}.information_schema.dynamic_table_refresh_history(error_only => true, result_limit => 10000)) "
            "where database_name = %s and state = 'FAILED' group by 1, 2",
            (db,),
        )
        failures = {(r[0], r[1]): r[2:] for r in cs.fetchall()}
        rows = [r for r in rows if (r.get("schema_name"), r.get("name")) in failures]
        if not rows:
            print("No dynamic tables with failed refreshes in the past 7 days.")
            return

    display_rows, csv_rows = [], []
    for row in rows:
        schema_name = row.get("schema_name")
        name = row.get("name")
        table_display = f"{schema_name}.{name}" if schema_name else name
        display_rows.append([
            table_display,
            row.get("target_lag") or "",
            row.get("warehouse") or "",
            row.get("refresh_mode") or "",
            row.get("scheduling_state") or "",
            fmt_count(row.get("rows")),
            fmt_bytes(row.get("bytes")),
            fmt_value(row.get("data_timestamp")),
        ])
        csv_rows.append(list(display_rows[-1]))
        if args.failing:
            n_failed, last_failed, last_error = failures[(schema_name, name)]
            error_line = " ".join((last_error or "").split())
            display_rows[-1] += [f"{n_failed:,}", fmt_value(last_failed), truncate(error_line)]
            csv_rows[-1] += [fmt_count(n_failed), fmt_value(last_failed), last_error or ""]

    headers = ["TABLE", "TARGET_LAG", "WAREHOUSE", "REFRESH_MODE", "STATE", "ROWS", "SIZE", "DATA_TIMESTAMP"]
    if args.failing:
        headers += ["FAILED_7D", "LAST_FAILED", "LAST_ERROR"]
    print_table(headers, display_rows)
    what = "with failed refreshes in the past 7 days" if args.failing else ""
    print(f"\n({plural(len(rows), 'dynamic table')}{' ' + what if what else ''} - use --refreshes SCHEMA.TABLE to see one's recent refreshes)")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def list_refreshes(args, parser, conn, db):
    schema, name = split_table_arg(parser, args.refreshes)
    cs = conn.cursor()
    real_schema = resolve_schema(conn, db, schema)
    matches = [t for t in show_rows(cs, f"show dynamic tables in schema {quoted(db, real_schema)}") if t["name"].upper() == name.upper()]
    exact = [t for t in matches if t["name"] == name]
    if not matches:
        raise SystemExit(f"No dynamic table matching {schema}.{name} in {db}. Run list_dynamic_tables.py to see them all.")
    if len(matches) > 1 and len(exact) != 1:
        raise SystemExit(f"Multiple dynamic tables matching {schema}.{name}: {', '.join(t['name'] for t in matches)}. Type the exact case to pick one.")
    dt = exact[0] if len(exact) == 1 else matches[0]

    # Newest first; a refresh that hasn't started yet has no start time and sorts on top.
    cs.execute(
        "select data_timestamp, state, refresh_action, refresh_trigger, refresh_start_time, "
        "datediff('millisecond', refresh_start_time, refresh_end_time) as ms, statistics, state_message, query_id "
        f"from table({quote_ident(db)}.information_schema.dynamic_table_refresh_history(name => %s, result_limit => 10000)) "
        "order by data_timestamp desc limit %s",
        (quoted(db, real_schema, dt["name"]), args.limit),
    )
    rows = cs.fetchall()

    print(f"{real_schema}.{dt['name']}  [{dt.get('scheduling_state', '')}]  target lag {dt.get('target_lag', '')}, "
          f"{(dt.get('refresh_mode') or '').lower()} refresh, warehouse {dt.get('warehouse') or '(none)'}")
    print()
    if not rows:
        print("No refreshes in the past 7 days.")
        return

    display_rows, csv_rows = [], []
    for data_ts, state, action, trigger, started, total_ms, stats, error, query_id in rows:
        # Statistics split the refresh into time queued for the warehouse and time actually running.
        stats = json.loads(stats) if stats else {}
        queued, ran = stats.get("queuedTimeMs"), stats.get("executionTimeMs")
        inserted, deleted = stats.get("numInsertedRows"), stats.get("numDeletedRows")
        error_line = " ".join((error or "").split())
        row = [fmt_value(data_ts), state, action or "", trigger or "", fmt_value(started), fmt_seconds(queued), fmt_seconds(ran),
               fmt_count(inserted) if inserted is not None else "", fmt_count(deleted) if deleted is not None else ""]
        display_rows.append(row + [truncate(error_line)])
        csv_rows.append(row + [fmt_seconds(total_ms), error or "", query_id or ""])

    print_table(["DATA_TIMESTAMP", "STATE", "ACTION", "TRIGGER", "STARTED", "QUEUED_S", "RUN_S", "INSERTED", "DELETED", "ERROR"], display_rows)
    failed = sum(1 for r in rows if r[1] == "FAILED")
    print(f"\n({plural(len(rows), 'most recent refresh', 'most recent refreshes')}, {failed:,} failed - use --output for full errors and query IDs)")
    print("QUEUED_S is time spent waiting for the warehouse; a high value means the warehouse is busy, not that the refresh is slow.")

    if args.output:
        write_csv(args.output, ["data_timestamp", "state", "action", "trigger", "started", "queued_s", "run_s", "inserted", "deleted",
                                "total_s", "error", "query_id"], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="List dynamic tables and their refresh settings, or one table's recent refreshes.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--schema", help="Only list dynamic tables in this schema (case-insensitive).")
    mode.add_argument("--refreshes", metavar="SCHEMA.TABLE", help="Show one dynamic table's recent refreshes instead, with errors.")
    parser.add_argument("--failing", action="store_true", help="Only dynamic tables with a failed refresh in the past 7 days, with the latest error.")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="With --refreshes: max refreshes to show (default 20).")
    parser.add_argument("--output", "-o", help="Also write the full result to this CSV file.")
    args = parser.parse_args()
    if args.failing and args.refreshes:
        parser.error("--failing can't be combined with --refreshes")

    db = current_database()

    conn = get_connection()
    try:
        if args.refreshes:
            list_refreshes(args, parser, conn, db)
        else:
            list_all(args, conn, db)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
