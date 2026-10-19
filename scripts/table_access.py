"""
Read-only: shows who has been reading and writing a table/view - for "is
anyone still using this, and can I change or drop it?". For one table it
lists each user with how many queries read it or wrote to it, when they
last did, and with which roles. Without a table it lists every table/view
in the database (or one schema) with the same counts, fewest reads first;
--unused keeps only the ones nobody read.

Usage comes from SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY: a successful query
counts when its SQL names the table - DATABASE.SCHEMA.TABLE anywhere,
SCHEMA.TABLE when it ran in that database, or the bare name when it ran in
that schema. Text inside quotes (e.g. GET_DDL('TABLE', '...') or
INFORMATION_SCHEMA lookups) doesn't count. A query is a write when the
table is the target of an INSERT/MERGE/UPDATE/DELETE/COPY/TRUNCATE/CREATE
TABLE AS, and a read otherwise; DESCRIBE, SHOW, GRANT and other DDL are
ignored. Reads that go through a view or dynamic table are counted against
that view, not the table underneath (find_usages.py --dependencies lists
those). Needs access to the SNOWFLAKE database, keeps a year of history
and can lag up to 45 minutes.

Usage:
  python table_access.py JIRA.ISSUES                       # who used it in the last 30 days
  python table_access.py OTHER_DB.SCHEMA.TABLE --days 90
  python table_access.py                                   # every table/view, fewest reads first
  python table_access.py --schema JIRA --unused            # tables nobody read in 30 days
  python table_access.py --unused --days 180 --output unused.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_table_target, resolve_schema, info_schema, kind_from_type, positive_int, print_table, write_csv, run_cli, fmt_count, fmt_bytes, fmt_value, plural, truncate

# ACCOUNT_USAGE keeps a year of query history.
MAX_DAYS = 365
SOURCE = "snowflake.account_usage.query_history"
READ_TYPES = ["SELECT", "UNLOAD"]
WRITE_TYPES = ["INSERT", "MERGE", "UPDATE", "DELETE", "COPY", "TRUNCATE_TABLE", "CREATE_TABLE_AS_SELECT"]
# One table reference in SQL: NAME, SCHEMA.NAME or DATABASE.SCHEMA.NAME.
NAME_RE = r"[A-Z0-9_$]+(\\.[A-Z0-9_$]+){0,2}"
# SCHEMA.NAME or DATABASE.SCHEMA.NAME anywhere in the SQL.
QUALIFIED_RE = r"[A-Z0-9_$]+(\\.[A-Z0-9_$]+){1,2}"
# A bare NAME only counts right after one of these, so a column or keyword
# that happens to share a table's name (ORDER BY, a STATUS column) doesn't.
BARE_RE = r"(^|[^A-Z0-9_$])(FROM|JOIN|INTO|UPDATE|TABLE|USING)\\s+([A-Z0-9_$]+)([^A-Z0-9_$.]|$)"
# Keywords followed by the target of a write.
WRITE_TARGET = (
    r"(^|[^A-Z0-9_$])(INSERT\\s+(OVERWRITE\\s+)?(ALL\\s+)?INTO|MERGE\\s+INTO|COPY\\s+INTO|UPDATE|DELETE\\s+FROM"
    r"|TRUNCATE\\s+(TABLE\\s+)?(IF\\s+EXISTS\\s+)?|TABLE\\s+(IF\\s+NOT\\s+EXISTS\\s+)?)\\s+"
)
# regexp_substr group number of the target name that follows WRITE_TARGET.
TARGET_GROUP = WRITE_TARGET.count("(") + 1


def sql_list(values):
    return ", ".join(f"'{v}'" for v in values)


def resolve_ref(tok, prefix):
    """SQL for the db/sch/name of a table reference, filling in the query's own database/schema where it's left out."""
    parts = f"(regexp_count({tok}, '\\\\.') + 1)"
    return (
        f"iff({parts} = 3, split_part({tok}, '.', 1), ctx_db) as {prefix}db, "
        f"case {parts} when 3 then split_part({tok}, '.', 2) when 2 then split_part({tok}, '.', 1) else ctx_schema end as {prefix}sch, "
        f"split_part({tok}, '.', -1) as {prefix}name"
    )


def refs_cte(prefilter):
    """
    q: successful reads/writes in the window, with quotes stripped from
    identifiers and string literals blanked out so only real references are
    left. refs: each distinct table every query names, resolved to
    database/schema/name, and whether it's that query's write target.
    """
    return (
        "q as ("
        "  select query_id, start_time, user_name, role_name, query_type,"
        "         upper(regexp_replace(replace(query_text, '\"', ''), '''[^'']*''', '''''')) as txt,"
        "         upper(database_name) as ctx_db, upper(schema_name) as ctx_schema"
        f"  from {SOURCE}"
        "  where start_time >= dateadd('day', -%(days)s, current_timestamp())"
        "    and execution_status = 'SUCCESS'"
        f"    and query_type in ({sql_list(READ_TYPES + WRITE_TYPES)})"
        f"    and ({prefilter})"
        "), qt as ("
        f"  select q.*, regexp_substr(txt, '{WRITE_TARGET}({NAME_RE})', 1, 1, 'es', {TARGET_GROUP}) as target from q"
        "), qr as ("
        f"  select qt.*, f.value::string as tok, {resolve_ref('target', 't_')}"
        f"  from qt, lateral flatten(array_cat(regexp_substr_all(txt, '{QUALIFIED_RE}'),"
        f"                                    regexp_substr_all(txt, '{BARE_RE}', 1, 1, 'es', 3))) f"
        "), refs as ("
        f"  select distinct query_id, start_time, user_name, role_name, {resolve_ref('tok', '')},"
        f"         query_type in ({sql_list(WRITE_TYPES)}) and target is not null"
        "           and t_db = db and t_sch = sch and t_name = name as is_write"
        "  from qr"
        ")"
    )


def one_table(args, parser, conn):
    db, schema, name, kind = resolve_table_target(conn, parser, args.table)
    cs = conn.cursor()
    cs.execute(
        "with " + refs_cte("contains(upper(query_text), upper(%(name)s))") + " "
        "select user_name, count_if(not is_write), max(iff(not is_write, start_time, null)), "
        "count_if(is_write), max(iff(is_write, start_time, null)), listagg(distinct role_name, ', ') within group (order by role_name) "
        "from refs where db = upper(%(db)s) and sch = upper(%(sch)s) and name = upper(%(name)s) "
        "group by 1 order by 2 desc, 4 desc, 1",
        {"db": db, "sch": schema, "name": name, "days": args.days},
    )
    rows = cs.fetchall()

    print(f"Who used {db}.{schema}.{name}  [{kind}] - past {plural(args.days, 'day')}\n")
    if not rows:
        print("No queries read or wrote it in that window.")
        if kind == "TABLE":
            print("Reads through views aren't counted here - check `python find_usages.py "
                  f"{schema}.{name} --dependencies` for views and dynamic tables built on it.")
        return

    headers = ["USER", "READS", "LAST_READ", "WRITES", "LAST_WRITE", "ROLES"]
    csv_rows = [[user, fmt_count(reads), fmt_value(last_read), fmt_count(writes), fmt_value(last_write), roles]
                for user, reads, last_read, writes, last_write, roles in rows]
    print_table(headers, [r[:5] + [truncate(r[5])] for r in csv_rows])

    reads, writes = sum(r[1] for r in rows), sum(r[3] for r in rows)
    last_read = max((r[2] for r in rows if r[2]), default=None)
    last_write = max((r[4] for r in rows if r[4]), default=None)
    print(f"\n({plural(len(rows), 'user')}; {plural(reads, 'read')}" + (f", last {fmt_value(last_read)}" if last_read else "")
          + f"; {plural(writes, 'write')}" + (f", last {fmt_value(last_write)}" if last_write else "") + ")")
    print("From ACCOUNT_USAGE.QUERY_HISTORY, which can lag up to 45 minutes. Reads through views count against the view.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def all_tables(args, conn):
    db = current_database()
    schema = resolve_schema(conn, db, args.schema) if args.schema else None
    # Only queries that could name a table here: run in this database, or naming it.
    prefilter = "upper(database_name) = upper(%(db)s) or contains(upper(query_text), upper(%(db)s) || '.')"
    cs = conn.cursor()
    cs.execute(
        "with t as ("
        "  select table_schema, table_name, table_type, row_count, bytes, last_altered"
        f"  from {info_schema(db, 'tables')} where table_schema <> 'INFORMATION_SCHEMA'"
        + (" and table_schema = %(schema)s" if schema else "")
        + "), " + refs_cte(prefilter) + " "
        "select t.table_schema, t.table_name, t.table_type, t.row_count, t.bytes, t.last_altered, "
        "count_if(not r.is_write), max(iff(not r.is_write, r.start_time, null)), "
        "count_if(r.is_write), max(iff(r.is_write, r.start_time, null)) "
        "from t left join refs r on r.db = upper(%(db)s) and r.sch = upper(t.table_schema) and r.name = upper(t.table_name) "
        "group by 1, 2, 3, 4, 5, 6 order by 7, 9, 1, 2",
        {"db": db, "schema": schema, "days": args.days},
    )
    rows = cs.fetchall()
    scope = f"schema {schema}" if schema else f"database {db}"
    if not rows:
        print(f"No tables or views found in {scope}.")
        return

    unused = [r for r in rows if not r[6]]
    shown = unused if args.unused else rows
    title = "not read by any query" if args.unused else "reads and writes"
    print(f"Tables/views in {scope} - {title}, past {plural(args.days, 'day')}\n")
    if not shown:
        print("Every table/view was read at least once in that window.")
        return

    headers = ["TABLE", "KIND", "ROWS", "SIZE", "LAST_ALTERED", "READS", "LAST_READ", "WRITES", "LAST_WRITE"]
    csv_rows = []
    for sch, name, ttype, row_count, bytes_, altered, reads, last_read, writes, last_write in shown:
        csv_rows.append([f"{sch}.{name}", kind_from_type(ttype), fmt_count(row_count), fmt_bytes(bytes_), fmt_value(altered),
                         fmt_count(reads), fmt_value(last_read), fmt_count(writes), fmt_value(last_write)])
    if args.unused:
        # READS is always 0 here, so leave it off the screen.
        print_table([h for h in headers if h not in ("READS", "LAST_READ")], [r[:5] + r[7:] for r in csv_rows])
    else:
        print_table(headers, csv_rows)

    written_only = sum(1 for r in unused if r[8])
    print(f"\n({plural(len(rows), 'table/view', 'tables/views')} in {scope}; {len(unused):,} not read"
          + (f", {written_only:,} of them still being written to" if written_only else "") + ")")
    print("From ACCOUNT_USAGE.QUERY_HISTORY, which can lag up to 45 minutes. Reads through views count against the view,"
          " so check find_usages.py --dependencies before dropping a table.")

    if args.output:
        write_csv(args.output, [h.lower() for h in headers], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="Show who has been reading and writing a table/view, or which tables nobody reads.")
    parser.add_argument("table", nargs="?", help="SCHEMA.TABLE or DATABASE.SCHEMA.TABLE. Leave out to list every table/view.")
    parser.add_argument("--days", type=positive_int, default=30, help=f"How many days of history to look at (default 30, max {MAX_DAYS}).")
    parser.add_argument("--schema", help="Without a table: only tables/views in this schema (case-insensitive).")
    parser.add_argument("--unused", action="store_true", help="Without a table: only tables/views no query read.")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    if args.days > MAX_DAYS:
        parser.error(f"--days can be at most {MAX_DAYS:,}")
    if args.table and (args.schema or args.unused):
        parser.error("--schema and --unused only work without a table")

    conn = get_connection()
    try:
        if args.table:
            one_table(args, parser, conn)
        else:
            all_tables(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
