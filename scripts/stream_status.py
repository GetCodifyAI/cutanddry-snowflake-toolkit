"""
Read-only: lists the streams (change trackers on a table or view) in the
configured database, with whether each one is stale, when it will go stale,
and whether it has changes waiting to be consumed.

A stream goes stale if nothing consumes it (a DML statement that reads it)
before its STALE_AFTER time. A stale stream has lost its place: the changes
it was tracking are gone, and it has to be recreated. Snowflake raises no
error when this happens, so this script flags streams that are stale or
will be within --days days.

Usage:
  python stream_status.py                                # streams in SNOWFLAKE_DATABASE
  python stream_status.py --all                          # streams in every database your role can see
  python stream_status.py --database INTEGRATION_HUB --schema STAGING_NEW
  python stream_status.py --all --failing                # only stale, invalid, or going stale within 7 days
  python stream_status.py --all --failing --days 14
  python stream_status.py --all --output streams.csv     # CSV adds owner, base tables, created, comment
"""

import argparse
import datetime

from sf_conn import get_connection, current_database, resolve_database, resolve_schema, quoted, positive_int, print_table, write_csv, run_cli, fmt_value, truncate, plural, show_rows
import snowflake.connector.errors as sf_errors


def has_data(cs, db, schema, name):
    """SYSTEM$STREAM_HAS_DATA as 'yes'/'no', or '' if it can't be checked (e.g. the stream is stale)."""
    try:
        cs.execute("select system$stream_has_data(%s)", (quoted(db, schema, name),))
        return "yes" if cs.fetchone()[0] else "no"
    except sf_errors.ProgrammingError:
        return ""


def fmt_days(delta):
    """A timedelta as e.g. '3.2 days' or '5.0 hours'."""
    hours = delta.total_seconds() / 3600
    return f"{hours:,.1f} hours" if hours < 24 else f"{hours / 24:,.1f} days"


def stream_state(s, now, warn_days):
    """(state, note) for one SHOW STREAMS row: state is 'stale', 'invalid', 'stale soon' or 'ok'."""
    invalid = s.get("invalid_reason")
    if invalid and invalid != "N/A":
        return "invalid", invalid
    stale_after = s.get("stale_after")
    if str(s.get("stale")).lower() == "true" or (stale_after and stale_after <= now):
        note = f"went stale {fmt_days(now - stale_after)} ago" if stale_after and stale_after <= now else "stale"
        return "stale", note + " - recreate it to resume tracking changes"
    if stale_after and stale_after <= now + datetime.timedelta(days=warn_days):
        return "stale soon", f"goes stale in {fmt_days(stale_after - now)} unless consumed"
    return "ok", ""


def main():
    parser = argparse.ArgumentParser(description="List streams with whether they're stale, when they go stale, and whether they have changes waiting.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="Streams in every database your role can see, not just SNOWFLAKE_DATABASE.")
    mode.add_argument("--schema", help="Only streams in this schema (case-insensitive).")
    parser.add_argument("--database", help="Look at this database instead of SNOWFLAKE_DATABASE (case-insensitive).")
    parser.add_argument("--failing", action="store_true", help="Only streams that are stale, invalid, or go stale within --days days.")
    parser.add_argument("--days", type=positive_int, default=7, help="Flag streams that go stale within this many days (default 7).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.all and args.database:
        parser.error("--database can't be combined with --all")

    conn = get_connection()
    try:
        cs = conn.cursor()
        if args.all:
            scope = "every database"
            streams = show_rows(cs, "show streams in account")
        else:
            db = resolve_database(conn, args.database) if args.database else current_database()
            if args.schema:
                real_schema = resolve_schema(conn, db, args.schema)
                scope = f"schema {db}.{real_schema}"
                streams = show_rows(cs, f"show streams in schema {quoted(db, real_schema)}")
            else:
                scope = f"database {db}"
                streams = show_rows(cs, f"show streams in database {quoted(db)}")
        if not streams:
            print(f"No streams found in {scope}.")
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        display_rows, csv_rows = [], []
        counts = {}
        for s in sorted(streams, key=lambda s: (s["database_name"], s["schema_name"], s["name"])):
            state, note = stream_state(s, now, args.days)
            counts[state] = counts.get(state, 0) + 1
            if args.failing and state == "ok":
                continue
            key = (s["database_name"], s["schema_name"], s["name"])
            waiting = "" if state in ("stale", "invalid") else has_data(cs, *key)
            label = ".".join(key) if args.all else f"{s['schema_name']}.{s['name']}"
            row = [label, s.get("table_name") or "", (s.get("mode") or "").lower(), state, fmt_value(s.get("stale_after")), waiting]
            display_rows.append([row[0], truncate(row[1])] + row[2:] + [truncate(note)])
            csv_rows.append(row + [note, s.get("source_type") or "", s.get("owner") or "", s.get("base_tables") or "",
                                   fmt_value(s.get("created_on")), s.get("comment") or ""])

        if not display_rows:
            print(f"No stale or at-risk streams in {scope} (looking {plural(args.days, 'day')} ahead).")
            return
        print(f"Streams in {scope} - flagging any that go stale within {plural(args.days, 'day')}")
        print()
        headers = ["STREAM", "SOURCE", "MODE", "STATE", "STALE_AFTER", "HAS_DATA"]
        print_table(headers + ["NOTE"], display_rows)
        summary = ", ".join(f"{n:,} {state}" for state, n in sorted(counts.items()))
        print(f"\n({plural(len(streams), 'stream')}: {summary})")
        print("A stream stays fresh only while something consumes it; find what reads one with find_usages.py STREAM_NAME.")

        if args.output:
            write_csv(args.output, [h.lower() for h in headers] + ["note", "source_type", "owner", "base_tables", "created", "comment"], csv_rows)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
