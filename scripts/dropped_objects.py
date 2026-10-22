"""
Read-only: lists tables, views, schemas and databases dropped recently in
the configured database, and whether each can still be brought back with
UNDROP - and until when. Prints the UNDROP commands for the ones that can.

A dropped table or schema can be undropped until its Time Travel retention
runs out (DATA_RETENTION_TIME_IN_DAYS, 1 day on this account by default).
After that, a permanent table sits in Fail-safe for 7 more days, where only
Snowflake Support can recover it; transient tables have no Fail-safe.
Dropped views can't be undropped at all, and Snowflake doesn't keep their
definition, so a dropped view has to be recreated from its original SQL.

The same name dropped several times (e.g. a staging table recreated by a
load job) is shown once, with how many times it was dropped; UNDROP brings
back the most recent one.

Comes from ACCOUNT_USAGE (TABLES, SCHEMATA, DATABASES), which can lag
up to 2 hours, so something dropped in the last couple of hours may not show
yet.

Usage:
  python dropped_objects.py                              # dropped in SNOWFLAKE_DATABASE, past 7 days
  python dropped_objects.py --restorable                 # only ones UNDROP can still bring back
  python dropped_objects.py --all --restorable           # every database
  python dropped_objects.py --database LIPARI --schema STAGING_RAW_AWS_DMS --days 30
  python dropped_objects.py --search orders              # names containing "orders"
  python dropped_objects.py --all --output dropped.csv   # CSV adds size, owner, dropped by, UNDROP statement
"""

import argparse
import datetime

from sf_conn import get_connection, current_database, quoted, positive_int, print_table, write_csv, run_cli, fmt_count, fmt_bytes, fmt_value, truncate, plural, like_contains

# ACCOUNT_USAGE keeps a year of history.
MAX_DAYS = 365
# Permanent tables stay in Fail-safe this long after Time Travel ends.
FAIL_SAFE_DAYS = 7


def scope_filter(catalog_col, schema_col, name_col, args, db):
    """(sql, params) restricting one ACCOUNT_USAGE view to the requested database/schema/name."""
    sql, params = "", []
    if db:
        sql += f" and upper({catalog_col}) = upper(%s)"
        params.append(db)
    else:
        sql += f" and {catalog_col} <> 'SNOWFLAKE'"  # Snowflake's own system objects, not undroppable
    if args.schema and schema_col:
        sql += f" and upper({schema_col}) = upper(%s)"
        params.append(args.schema)
    if args.search:
        sql += f" and {name_col} ilike %s escape '\\\\'"
        params.append(like_contains(args.search))
    return sql, params


def fetch_drops(cs, args, db):
    """Every drop in the window as dicts, newest first: tables/views, then schemas, then databases."""
    since = (args.days,)
    drops = []

    where, params = scope_filter("t.table_catalog", "t.table_schema", "t.table_name", args, db)
    cs.execute(
        "select iff(t.table_type = 'VIEW', 'VIEW', iff(t.is_dynamic = 'YES', 'DYNAMIC TABLE', 'TABLE')), "
        "t.table_catalog, t.table_schema, t.table_name, t.deleted, t.retention_time, t.is_transient = 'YES', "
        "t.row_count, t.bytes, t.table_owner, t.last_ddl_by, s.deleted is not null "
        "from snowflake.account_usage.tables t "
        "left join snowflake.account_usage.schemata s on s.schema_id = t.table_schema_id "
        "where t.deleted > dateadd('day', -%s, current_timestamp())" + where,
        since + tuple(params),
    )
    drops += cs.fetchall()

    where, params = scope_filter("s.catalog_name", "s.schema_name", "s.schema_name", args, db)
    cs.execute(
        "select 'SCHEMA', s.catalog_name, s.schema_name, null, s.deleted, s.retention_time, s.is_transient = 'YES', "
        "null, null, s.schema_owner, null, d.deleted is not null "
        "from snowflake.account_usage.schemata s "
        "left join snowflake.account_usage.databases d on d.database_id = s.catalog_id "
        "where s.deleted > dateadd('day', -%s, current_timestamp())" + where,
        since + tuple(params),
    )
    drops += cs.fetchall()

    if not args.schema:
        where, params = scope_filter("database_name", None, "database_name", args, db)
        cs.execute(
            "select 'DATABASE', database_name, null, null, deleted, retention_time, is_transient = 'YES', "
            "null, null, database_owner, null, false "
            "from snowflake.account_usage.databases "
            "where deleted > dateadd('day', -%s, current_timestamp())" + where,
            since + tuple(params),
        )
        drops += cs.fetchall()

    keys = ["kind", "db", "schema", "name", "deleted", "retention", "transient", "rows", "bytes", "owner", "dropped_by", "parent_dropped"]
    return sorted((dict(zip(keys, r)) for r in drops), key=lambda d: d["deleted"], reverse=True)


def group_drops(drops):
    """One entry per (kind, db, schema, name): the most recent drop, plus how many times it was dropped."""
    groups = {}
    for d in drops:  # newest first, so the first one seen is the latest
        key = (d["kind"], d["db"], d["schema"], d["name"])
        if key in groups:
            groups[key]["times"] += 1
        else:
            groups[key] = dict(d, times=1)
    return list(groups.values())


def object_ref(d):
    """Quoted reference to a dropped object, e.g. "DB"."SCHEMA"."TABLE"."""
    return quoted(*[p for p in (d["db"], d["schema"], d["name"]) if p is not None])


def label(d, show_db):
    parts = [d["db"], d["schema"], d["name"]] if show_db or d["kind"] == "DATABASE" else [d["schema"], d["name"]]
    return ".".join(p for p in parts if p is not None)


def fmt_left(delta):
    """A timedelta as e.g. '5.2 hours' or '3.0 days'."""
    hours = delta.total_seconds() / 3600
    return f"{hours:,.1f} hours" if hours < 24 else f"{hours / 24:,.1f} days"


def classify(d, now):
    """Set d['status'], d['until'] (when that status ends) and d['note']."""
    if d["kind"] == "VIEW":
        d["status"], d["until"] = "can't undrop", None
        d["note"] = "views have no Time Travel and their SQL isn't kept"
        return
    retention_end = d["deleted"] + datetime.timedelta(days=d["retention"] or 0)
    if retention_end > now:
        d["status"], d["until"] = "restorable", retention_end
        d["note"] = f"{fmt_left(retention_end - now)} left"
        if d["parent_dropped"]:
            d["note"] += f"; its {'database' if d['kind'] == 'SCHEMA' else 'schema'} was dropped too - undrop that first"
        return
    fail_safe_end = retention_end + datetime.timedelta(days=FAIL_SAFE_DAYS)
    if d["kind"] in ("TABLE", "DYNAMIC TABLE") and not d["transient"] and fail_safe_end > now:
        d["status"], d["until"] = "fail-safe", fail_safe_end
        d["note"] = "only Snowflake Support can recover it"
    else:
        d["status"], d["until"] = "gone", None
        d["note"] = "transient - no Fail-safe" if d["transient"] and d["kind"] != "VIEW" else ""


def mark_name_clashes(cs, objects):
    """
    Note restorable objects whose name is taken by a live object - UNDROP
    fails until that one is renamed - and dropped views that have since been
    recreated.
    """
    objects = [d for d in objects if d["status"] == "restorable" or d["kind"] == "VIEW"]
    checks = {
        "TABLE": ("tables", "table_catalog", "table_schema", "table_name"),
        "SCHEMA": ("schemata", "catalog_name", "schema_name", None),
        "DATABASE": ("databases", "database_name", None, None),
    }
    for kind, (view, *cols) in checks.items():
        items = [d for d in objects if d["kind"].endswith(kind) or (kind == "TABLE" and d["kind"] == "VIEW")]
        if not items:
            continue
        used = [c for c in cols if c]
        match = " or ".join("(" + " and ".join(f"{c} = %s" for c in used) + ")" for _ in items)
        values = [v for d in items for v in (d["db"], d["schema"], d["name"])[:len(used)]]
        cs.execute(f"select {', '.join(used)} from snowflake.account_usage.{view} where deleted is null and ({match})", tuple(values))
        live = {tuple(r) for r in cs.fetchall()}
        for d in items:
            if (d["db"], d["schema"], d["name"])[:len(used)] not in live:
                continue
            if d["kind"] == "VIEW":
                d["note"] = "recreated since"
            else:
                d["note"] += "; name in use - rename the current one first"


def main():
    parser = argparse.ArgumentParser(description="List recently dropped tables, views, schemas and databases, and whether UNDROP can still restore them.")
    parser.add_argument("--all", action="store_true", help="Every database your role can see, not just SNOWFLAKE_DATABASE.")
    parser.add_argument("--database", help="Look at this database instead of SNOWFLAKE_DATABASE (case-insensitive; can be a dropped one).")
    parser.add_argument("--schema", help="Only objects in this schema (case-insensitive; can be a dropped one).")
    parser.add_argument("--search", metavar="TEXT", help="Only objects whose name contains this text (case-insensitive).")
    parser.add_argument("--restorable", action="store_true", help="Only objects UNDROP can still restore.")
    parser.add_argument("--days", type=positive_int, default=7, help=f"How far back to look for drops, in days (default 7, max {MAX_DAYS}).")
    parser.add_argument("--limit", "-n", type=positive_int, default=50, help="Max objects to show (default 50; --output gets them all).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.days > MAX_DAYS:
        parser.error(f"--days can be at most {MAX_DAYS} (ACCOUNT_USAGE only keeps a year)")
    if args.all and args.database:
        parser.error("--database can't be combined with --all")

    db = None if args.all else args.database or current_database()
    scope = "every database" if args.all else f"database {db}"
    if args.schema:
        scope = f"schema {args.schema}" + ("" if args.all else f" of {db}")
    window = f"past {plural(args.days, 'day')}"

    conn = get_connection()
    try:
        cs = conn.cursor()
        drops = fetch_drops(cs, args, db)
        objects = group_drops(drops)
        now = datetime.datetime.now(datetime.timezone.utc)
        for d in objects:
            classify(d, now)
        if args.restorable:
            objects = [d for d in objects if d["status"] == "restorable"]
        if not objects:
            what = "restorable objects" if args.restorable else "dropped objects"
            print(f"No {what} in {scope} ({window}" + (f", names containing '{args.search}'" if args.search else "") + ").")
            return
        restorable = [d for d in objects if d["status"] == "restorable"]
        mark_name_clashes(cs, objects)
        # Show the stored case of a typed --database/--schema (they can't be resolved up front if dropped).
        if not args.all:
            scope = scope.replace(f" of {db}", f" of {objects[0]['db']}").replace(f"database {db}", f"database {objects[0]['db']}")
        if args.schema:
            scope = scope.replace(f"schema {args.schema}", f"schema {next((d['schema'] for d in objects if d['schema']), args.schema)}")

        display_rows, csv_rows = [], []
        for d in objects:
            row = [d["kind"], fmt_value(d["deleted"]), fmt_count(d["times"]), d["status"], fmt_value(d["until"])]
            has_size = d["rows"] is not None and d["kind"] != "VIEW"
            sizes = [fmt_count(d["rows"]) if has_size else "", fmt_bytes(d["bytes"]) if has_size else ""]
            display_rows.append([label(d, args.all)] + row + [sizes[0], truncate(d["note"])])
            csv_rows.append([d["db"], d["schema"] or "", d["name"] or ""] + row + sizes + [
                d["note"], d["owner"] or "", d["dropped_by"] or "",
                f"undrop {d['kind'].lower()} {object_ref(d)};" if d["status"] == "restorable" else "",
            ])

        print(f"Dropped in {scope} - {window}" + (f", names containing '{args.search}'" if args.search else ""))
        print()
        headers = ["OBJECT", "KIND", "DROPPED", "TIMES", "STATUS", "UNTIL", "ROWS", "NOTE"]
        print_table(headers, display_rows[:args.limit])
        statuses = {}
        for d in objects:
            statuses[d["status"]] = statuses.get(d["status"], 0) + 1
        shown = f"showing {len(display_rows[:args.limit]):,} of " if len(display_rows) > args.limit else ""
        print(f"\n({shown}{plural(len(objects), 'object')}: " + ", ".join(f"{n:,} {s}" for s, n in sorted(statuses.items())) + ")")
        print("TIMES is how often that name was dropped; UNDROP restores the most recent. ACCOUNT_USAGE can lag up to 2 hours.")

        # A table in a dropped schema comes back with the schema, so only the outermost object needs undropping.
        to_run = [d for d in restorable[:args.limit] if not d["parent_dropped"]]
        if to_run:
            print("\nTo restore:")
            for d in to_run:
                clash = "  -- name in use: rename the current one first" if "name in use" in d["note"] else ""
                print(f"  undrop {d['kind'].lower()} {object_ref(d)};{clash}")

        if args.output:
            write_csv(args.output, ["database", "schema", "name", "kind", "dropped", "times_dropped", "status", "until", "rows", "size",
                                    "note", "owner", "dropped_by", "undrop_sql"], csv_rows)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
