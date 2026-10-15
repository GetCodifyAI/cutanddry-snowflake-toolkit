"""
Read-only: lists the tasks (scheduled SQL jobs) in the configured database
with their state, schedule, warehouse, last run and its result, how many
runs failed recently, and when they run next. Use --runs to see one
task's individual runs, including error messages.

Run history comes from INFORMATION_SCHEMA.TASK_HISTORY, which only goes
back 7 days.

Usage:
  python list_tasks.py                              # every task, past 7 days of runs
  python list_tasks.py --schema SUPPLIER_DASHBOARD
  python list_tasks.py --failing                    # only tasks with recent failures or suspended due to errors
  python list_tasks.py --hours 24                   # count runs/failures over the past day instead
  python list_tasks.py --runs SCHEMA.TASK_NAME      # one task's recent runs, with errors
  python list_tasks.py --output tasks.csv           # CSV adds condition, owner, last error, full definition
"""

import argparse
import json

from sf_conn import get_connection, current_database, resolve_schema, quoted, quote_ident, positive_int, print_table, write_csv, run_cli, fmt_value, truncate, plural, show_rows

# INFORMATION_SCHEMA.TASK_HISTORY rejects a start time 7 full days back or earlier.
MAX_HOURS = 167
SUSPEND_REASONS = {"SUSPENDED_DUE_TO_ERRORS": "suspended due to errors", "USER_SUSPENDED": "suspended by a user"}


def show_tasks(cs, db, schema=None):
    """Run SHOW TASKS and return a list of dicts keyed by lowercase column name."""
    return show_rows(cs, f"show tasks in schema {quoted(db, schema)}" if schema else f"show tasks in database {quoted(db)}")


def history_source(db, hours, error_only=False):
    # TASK_HISTORY only fills in error_code/error_message when called with
    # error_only => true, so errors have to be fetched with a separate call.
    return (f"table({quote_ident(db)}.information_schema.task_history(task_name => %s, "
            f"scheduled_time_range_start => dateadd('hour', -{hours}, current_timestamp()), result_limit => 10000"
            f"{', error_only => true' if error_only else ''}))")


def fmt_schedule(task, db):
    """'cron 0 6 * * * UTC', 'every 1 minute', or 'after SCHEMA.TASK' for tasks triggered by another task."""
    schedule = task.get("schedule")
    if schedule:
        if schedule.upper().startswith("USING CRON "):
            return "cron " + schedule[len("USING CRON "):]
        return "every " + schedule.lower()
    try:
        predecessors = json.loads(task.get("predecessors") or "[]")
    except ValueError:
        predecessors = []
    if predecessors:
        # Drop the database, and the schema too when the parent task is in the same one.
        same_schema = f"{db}.{task['schema_name']}."
        return "after " + ", ".join(p.removeprefix(same_schema).removeprefix(f"{db}.") for p in predecessors)
    return "(no schedule)"


def fmt_note(task):
    notes = []
    if task.get("state") == "suspended":
        reason = task.get("last_suspended_reason")
        notes.append(SUSPEND_REASONS.get(reason, f"suspended ({reason.lower()})" if reason else "suspended"))
    if task.get("condition"):
        notes.append("runs only when its condition is true")
    return "; ".join(notes)


def list_all(args, conn, db):
    cs = conn.cursor()
    real_schema = resolve_schema(conn, db, args.schema) if args.schema else None
    tasks = show_tasks(cs, db, real_schema)
    if not tasks:
        print(f"No tasks found in {f'schema {real_schema}' if real_schema else f'database {db}'}.")
        return

    # One TASK_HISTORY call per task, combined into a single query. A single
    # unfiltered call covers the whole account and is capped at 10,000 rows,
    # so a task that runs every minute can crowd every other task out.
    def union(error_only):
        parts, params = [], []
        for t in tasks:
            parts.append(
                "select %s as task_schema, %s as task_name, state, scheduled_time, error_message "
                f"from {history_source(db, args.hours, error_only)} where database_name = %s and schema_name = %s"
            )
            params += [t["schema_name"], t["name"], t["name"], db, t["schema_name"]]
        return " union all ".join(parts), params

    runs_sql, runs_params = union(error_only=False)
    errors_sql, errors_params = union(error_only=True)
    cs.execute(
        f"with h as ({runs_sql}), e as ({errors_sql}), "
        "last_errors as (select task_schema, task_name, max_by(error_message, scheduled_time) as last_error from e group by 1, 2) "
        "select h.task_schema, h.task_name, "
        "max(iff(state <> 'SCHEDULED', scheduled_time, null)) as last_run, "
        "max_by(state, iff(state <> 'SCHEDULED', scheduled_time, null)) as last_result, "
        "count_if(state <> 'SCHEDULED') as runs, "
        "count_if(state = 'FAILED') as failures, "
        "any_value(last_error) as last_error, "
        "min(iff(state = 'SCHEDULED', scheduled_time, null)) as next_run "
        "from h left join last_errors l on l.task_schema = h.task_schema and l.task_name = h.task_name "
        "group by 1, 2",
        tuple(runs_params + errors_params),
    )
    history = {(r[0], r[1]): r[2:] for r in cs.fetchall()}

    display_rows, csv_rows = [], []
    for t in sorted(tasks, key=lambda t: (t["schema_name"], t["name"])):
        last_run, last_result, runs, failures, last_error, next_run = history.get((t["schema_name"], t["name"]), (None, None, 0, 0, None, None))
        note = fmt_note(t)
        if args.failing and not (failures or t.get("last_suspended_reason") == "SUSPENDED_DUE_TO_ERRORS" and t.get("state") == "suspended"):
            continue
        row = [
            f"{t['schema_name']}.{t['name']}", t["state"], fmt_schedule(t, db), t.get("warehouse") or "(serverless)",
            fmt_value(last_run), last_result or "", f"{runs:,}", f"{failures:,}", fmt_value(next_run), note,
        ]
        display_rows.append(row)
        csv_rows.append(row + [t.get("condition") or "", t.get("owner") or "", last_error or "", t.get("definition") or ""])

    window = f"past {args.hours:,} hours" if args.hours != MAX_HOURS else "past 7 days"
    if not display_rows:
        print(f"No failing tasks ({window}).")
        return
    print(f"Tasks in {f'schema {real_schema}' if real_schema else f'database {db}'} - runs and failures over the {window}")
    print()
    headers = ["TASK", "STATE", "SCHEDULE", "WAREHOUSE", "LAST_RUN", "LAST_RESULT", "RUNS", "FAILED", "NEXT_RUN", "NOTE"]
    print_table(headers, display_rows)
    print(f"\n({plural(len(display_rows), 'task')} - use --runs SCHEMA.TASK to see a task's runs and errors)")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers] + ["condition", "owner", "last_error", "definition"], csv_rows)


def list_runs(args, parser, conn, db):
    if "." not in args.runs:
        parser.error("--runs needs a schema-qualified task name, e.g. DTAM.MY_TASK")
    schema, name = args.runs.split(".", 1)
    cs = conn.cursor()
    real_schema = resolve_schema(conn, db, schema)
    matches = [t for t in show_tasks(cs, db, real_schema) if t["name"].upper() == name.upper()]
    exact = [t for t in matches if t["name"] == name]
    if not matches:
        raise SystemExit(f"No task matching {schema}.{name} found in {db}. Run list_tasks.py to see all tasks.")
    if len(matches) > 1 and len(exact) != 1:
        raise SystemExit(f"Multiple tasks matching {schema}.{name}: {', '.join(t['name'] for t in matches)}. Type the exact case to pick one.")
    task = exact[0] if len(exact) == 1 else matches[0]

    cs.execute(
        "with e as (select query_id, error_message "
        f"from {history_source(db, args.hours, error_only=True)} where database_name = %s and schema_name = %s) "
        "select h.scheduled_time, h.state, h.query_start_time, h.completed_time, "
        "datediff('millisecond', h.query_start_time, h.completed_time) as ms, "
        "coalesce(e.error_message, h.error_message) as error_message, h.query_id "
        f"from {history_source(db, args.hours)} h left join e on e.query_id = h.query_id "
        "where h.database_name = %s and h.schema_name = %s "
        "order by h.scheduled_time desc limit %s",
        (task["name"], db, real_schema, task["name"], db, real_schema, args.limit),
    )
    rows = cs.fetchall()

    print(f"{real_schema}.{task['name']}  [{task['state']}]  {fmt_schedule(task, db)}")
    note = fmt_note(task)
    if note:
        print(f"  {note}")
    if task.get("condition"):
        print(f"  condition: {task['condition']}")
    print()
    window = f"past {args.hours:,} hours" if args.hours != MAX_HOURS else "past 7 days"
    if not rows:
        print(f"No runs in the {window}.")
        return

    display_rows, csv_rows = [], []
    for scheduled, state, started, completed, ms, error, query_id in rows:
        seconds = f"{ms / 1000:,.1f}" if ms is not None else ""
        error_line = " ".join((error or "").split())
        display_rows.append([fmt_value(scheduled), state, fmt_value(started), seconds, truncate(error_line)])
        csv_rows.append([fmt_value(scheduled), state, fmt_value(started), fmt_value(completed), seconds, error or "", query_id or ""])

    print_table(["SCHEDULED", "STATE", "STARTED", "SECONDS", "ERROR"], display_rows)
    print(f"\n({plural(len(rows), 'most recent run')}, {window} - use --output for full error messages and query IDs)")

    if args.output:
        write_csv(args.output, ["scheduled", "state", "started", "completed", "seconds", "error", "query_id"], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="List tasks with their schedule, state and recent run results.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--schema", help="Only list tasks in this schema (case-insensitive).")
    mode.add_argument("--runs", metavar="SCHEMA.TASK", help="Show one task's recent runs instead, with error messages.")
    parser.add_argument("--failing", action="store_true", help="Only tasks with failed runs in the window, or suspended due to errors.")
    parser.add_argument("--hours", type=positive_int, default=MAX_HOURS, help=f"How far back to look at runs, in hours (default and max {MAX_HOURS}, i.e. 7 days).")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="With --runs: max runs to show (default 20).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.hours > MAX_HOURS:
        parser.error(f"--hours can be at most {MAX_HOURS} (task history only covers the last 7 days)")
    if args.failing and args.runs:
        parser.error("--failing can't be combined with --runs")

    db = current_database()
    conn = get_connection()
    try:
        if args.runs:
            list_runs(args, parser, conn, db)
        else:
            list_all(args, conn, db)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
