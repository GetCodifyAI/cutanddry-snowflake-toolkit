"""
Read-only: lists the Snowpipes (pipes that auto-load files from a stage) in
the configured database with their state, files waiting to load, and how
many files loaded, partially loaded or failed recently, plus the latest
error. Use --loads to see one pipe's individual files, including errors.

State comes from SYSTEM$PIPE_STATUS. Load counts come from
ACCOUNT_USAGE.COPY_HISTORY, which can lag up to 2 hours and keeps a year.

Usage:
  python pipe_status.py                                  # pipes in SNOWFLAKE_DATABASE, past 7 days of loads
  python pipe_status.py --all                            # pipes in every database your role can see
  python pipe_status.py --database LIPARI --schema STAGING_RAW_AWS_DMS
  python pipe_status.py --all --failing                  # only pipes not running, or with failed/partial loads
  python pipe_status.py --days 30
  python pipe_status.py --loads SCHEMA.PIPE              # one pipe's recent files, with errors
  python pipe_status.py --loads DATABASE.SCHEMA.PIPE --failing
  python pipe_status.py --all --output pipes.csv         # CSV adds owner, notification channel, last error, definition
"""

import argparse
import json

from sf_conn import get_connection, current_database, resolve_database, resolve_schema, quoted, positive_int, print_table, write_csv, run_cli, fmt_count, fmt_value, truncate, plural, show_rows
import snowflake.connector.errors as sf_errors

# ACCOUNT_USAGE.COPY_HISTORY keeps a year of loads.
MAX_DAYS = 365


def pipe_state(cs, db, schema, name):
    """SYSTEM$PIPE_STATUS as a dict, or {} if the role isn't allowed to call it on this pipe."""
    try:
        cs.execute("select system$pipe_status(%s)", (quoted(db, schema, name),))
        return json.loads(cs.fetchone()[0])
    except sf_errors.ProgrammingError:
        return {}


def fmt_iso(cs, value):
    """Format an ISO timestamp from SYSTEM$PIPE_STATUS (always UTC) in the session's timezone, like COPY_HISTORY times."""
    cs.execute("select try_to_timestamp_ltz(%s)", (value,))
    return fmt_value(cs.fetchone()[0]) or value


def fmt_note(pipe, state):
    notes = []
    if pipe.get("invalid_reason"):
        notes.append(f"invalid: {pipe['invalid_reason']}")
    if state.get("error"):
        notes.append(state["error"])
    if not state:
        notes.append("your role can't see its status")
    return "; ".join(notes)


def load_history(cs, pipes, days):
    """Per-pipe load counts and last error from COPY_HISTORY, keyed by (db, schema, pipe)."""
    keys = sorted({(p["database_name"], p["schema_name"], p["name"]) for p in pipes})
    match = " or ".join("(pipe_catalog_name = %s and pipe_schema_name = %s and pipe_name = %s)" for _ in keys)
    cs.execute(
        "select pipe_catalog_name, pipe_schema_name, pipe_name, count(*), sum(row_count), "
        "count_if(status ilike 'partially loaded'), count_if(status ilike 'load failed'), max(last_load_time), "
        "max_by(first_error_message, iff(first_error_message is not null, last_load_time, null)) "
        "from snowflake.account_usage.copy_history "
        f"where last_load_time > dateadd('day', -%s, current_timestamp()) and ({match}) "
        "group by 1, 2, 3",
        tuple([days] + [v for k in keys for v in k]),
    )
    return {(r[0], r[1], r[2]): r[3:] for r in cs.fetchall()}


def list_all(args, conn):
    cs = conn.cursor()
    if args.all:
        db, scope = None, "every database"
        pipes = show_rows(cs, "show pipes in account")
    else:
        db = resolve_database(conn, args.database) if args.database else current_database()
        if args.schema:
            real_schema = resolve_schema(conn, db, args.schema)
            scope = f"schema {db}.{real_schema}"
            pipes = show_rows(cs, f"show pipes in schema {quoted(db, real_schema)}")
        else:
            scope = f"database {db}"
            pipes = show_rows(cs, f"show pipes in database {quoted(db)}")
    if not pipes:
        print(f"No pipes found in {scope}.")
        return

    history = load_history(cs, pipes, args.days)
    window = f"past {plural(args.days, 'day')}"

    display_rows, csv_rows = [], []
    for p in sorted(pipes, key=lambda p: (p["database_name"], p["schema_name"], p["name"])):
        key = (p["database_name"], p["schema_name"], p["name"])
        state = pipe_state(cs, *key)
        files, rows, partial, failed, last_load, last_error = history.get(key, (0, 0, 0, 0, None, None))
        execution = (state.get("executionState") or "unknown").lower()
        note = fmt_note(p, state)
        if not files and not note:
            note = f"no files loaded in the {window}"
        if args.failing and execution == "running" and not (partial or failed or p.get("invalid_reason") or state.get("error")):
            continue
        label = ".".join(key) if args.all else f"{p['schema_name']}.{p['name']}"
        pending = state.get("pendingFileCount")
        row = [
            label, execution, "" if pending is None else fmt_count(pending), fmt_value(last_load),
            fmt_count(files), fmt_count(rows or 0), fmt_count(partial), fmt_count(failed),
        ]
        error_line = " ".join((last_error or "").split())
        display_rows.append(row + [truncate(error_line), truncate(note)])
        csv_rows.append(row + [last_error or "", note, p.get("owner") or "", p.get("notification_channel") or "", p.get("definition") or ""])

    if not display_rows:
        print(f"No failing pipes in {scope} ({window}).")
        return
    print(f"Pipes in {scope} - loads over the {window}")
    print()
    headers = ["PIPE", "STATE", "PENDING", "LAST_LOAD", "FILES", "ROWS", "PARTIAL", "FAILED", "LAST_ERROR", "NOTE"]
    print_table(headers, display_rows)
    print(f"\n({plural(len(display_rows), 'pipe')} - use --loads SCHEMA.PIPE to see a pipe's files and errors)")
    print("Load counts are from ACCOUNT_USAGE.COPY_HISTORY, which can lag up to 2 hours.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers] + ["owner", "notification_channel", "definition"], csv_rows)


def list_loads(args, parser, conn):
    parts = args.loads.split(".")
    if len(parts) == 2:
        db, (schema, name) = (resolve_database(conn, args.database) if args.database else current_database()), parts
    elif len(parts) == 3:
        db, schema, name = resolve_database(conn, parts[0]), parts[1], parts[2]
    else:
        parser.error("--loads needs SCHEMA.PIPE or DATABASE.SCHEMA.PIPE, e.g. BIRITE.BIRITE_INVOICE_PIPE")
    cs = conn.cursor()
    real_schema = resolve_schema(conn, db, schema)
    matches = [p for p in show_rows(cs, f"show pipes in schema {quoted(db, real_schema)}") if p["name"].upper() == name.upper()]
    exact = [p for p in matches if p["name"] == name]
    if not matches:
        raise SystemExit(f"No pipe matching {schema}.{name} found in {db}. Run pipe_status.py to see all pipes.")
    if len(matches) > 1 and len(exact) != 1:
        raise SystemExit(f"Multiple pipes matching {schema}.{name}: {', '.join(p['name'] for p in matches)}. Type the exact case to pick one.")
    pipe = exact[0] if len(exact) == 1 else matches[0]

    state = pipe_state(cs, db, real_schema, pipe["name"])
    cs.execute(
        "select last_load_time, status, file_name, row_count, row_parsed, error_count, first_error_message, table_name "
        "from snowflake.account_usage.copy_history "
        "where pipe_catalog_name = %s and pipe_schema_name = %s and pipe_name = %s "
        "and last_load_time > dateadd('day', -%s, current_timestamp()) "
        + ("and status not ilike 'loaded' " if args.failing else "")
        + "order by last_load_time desc limit %s",
        (db, real_schema, pipe["name"], args.days, args.limit),
    )
    rows = cs.fetchall()

    print(f"{db}.{real_schema}.{pipe['name']}  [{(state.get('executionState') or 'unknown').lower()}]")
    if state:
        print(f"  pending files: {fmt_count(state.get('pendingFileCount') or 0)}"
              + (f", last file ingested {fmt_iso(cs, state['lastIngestedTimestamp'])}" if state.get("lastIngestedTimestamp") else "")
              + (f", last notification received {fmt_iso(cs, state['lastReceivedMessageTimestamp'])}" if state.get("lastReceivedMessageTimestamp") else ""))
    note = fmt_note(pipe, state)
    if note:
        print(f"  {note}")
    print()
    window = f"past {plural(args.days, 'day')}"
    if not rows:
        print(f"No {'failed or partially loaded ' if args.failing else ''}files in the {window}.")
        return

    display_rows, csv_rows = [], []
    for loaded, status, file_name, row_count, row_parsed, error_count, error, table in rows:
        error_line = " ".join((error or "").split())
        display_rows.append([fmt_value(loaded), status, truncate(file_name or ""), fmt_count(row_count or 0), fmt_count(error_count or 0), truncate(error_line)])
        csv_rows.append([fmt_value(loaded), status, file_name or "", table or "", fmt_count(row_count or 0), fmt_count(row_parsed or 0), fmt_count(error_count or 0), error or ""])

    print_table(["LOADED", "STATUS", "FILE", "ROWS", "ERRORS", "FIRST_ERROR"], display_rows)
    print(f"\n({plural(len(rows), 'most recent file')}, {window} - use --output for full file names and errors)")
    print("From ACCOUNT_USAGE.COPY_HISTORY, which can lag up to 2 hours.")

    if args.output:
        write_csv(args.output, ["loaded", "status", "file", "table", "rows", "rows_parsed", "errors", "first_error"], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="List Snowpipes with their state and recent load results.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="Pipes in every database your role can see, not just SNOWFLAKE_DATABASE.")
    mode.add_argument("--schema", help="Only pipes in this schema (case-insensitive).")
    mode.add_argument("--loads", metavar="SCHEMA.PIPE", help="Show one pipe's recent files instead, with errors. DATABASE.SCHEMA.PIPE also works.")
    parser.add_argument("--database", help="Look at this database instead of SNOWFLAKE_DATABASE (case-insensitive).")
    parser.add_argument("--failing", action="store_true", help="Only pipes not running or with failed/partial loads; with --loads, only failed/partial files.")
    parser.add_argument("--days", type=positive_int, default=7, help=f"How far back to look at loads, in days (default 7, max {MAX_DAYS}).")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="With --loads: max files to show (default 20).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.days > MAX_DAYS:
        parser.error(f"--days can be at most {MAX_DAYS} (COPY_HISTORY only keeps a year)")
    if args.all and args.database:
        parser.error("--database can't be combined with --all")

    conn = get_connection()
    try:
        if args.loads:
            list_loads(args, parser, conn)
        else:
            list_all(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
