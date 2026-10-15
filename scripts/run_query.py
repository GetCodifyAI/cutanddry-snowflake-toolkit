"""
Read-only: runs one SQL query - given inline or from a .sql file - and
prints the result. Only a single SELECT / WITH / SHOW / DESCRIBE / EXPLAIN
statement is accepted; anything else (INSERT, UPDATE, CREATE, DROP, ...)
or more than one statement is refused before it reaches Snowflake. As a
second safety net the query runs inside a transaction that is always
rolled back.

These checks stop accidental writes, but a SELECT can still call a system
function with side effects (e.g. SYSTEM$CANCEL_QUERY) - a role granted
only SELECT/USAGE is what really keeps this read-only.

Usage:
  python run_query.py "select status, count(*) from PUBLIC.ORDERS group by 1"
  python run_query.py --file report.sql
  python run_query.py --file report.sql --limit 200
  python run_query.py --file report.sql --output result.csv   # CSV gets every row

Unqualified table names resolve against SNOWFLAKE_DATABASE (and
SNOWFLAKE_SCHEMA if set) from .env.
"""

import argparse
import io

# sf_conn must be imported before anything from snowflake.connector - it applies an ssl compatibility fix first.
from sf_conn import get_connection, positive_int, print_table, write_csv, run_cli, fmt_cell, json_columns, truncate, plural
from snowflake.connector.util_text import split_statements

ALLOWED_FIRST_WORDS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


def check_read_only(parser, sql):
    """Return the single statement in `sql`, or exit via parser.error() if it isn't one read-only statement."""
    statements = [s for s, _ in split_statements(io.StringIO(sql), remove_comments=True)]
    statements = [s.strip().rstrip(";").strip() for s in statements]
    statements = [s for s in statements if s]
    if not statements:
        parser.error("no SQL statement found")
    if len(statements) > 1:
        parser.error(f"found {len(statements)} statements - run_query.py runs exactly one at a time")
    stmt = statements[0]
    first_word = stmt.lstrip("(").split(None, 1)[0].upper()
    if first_word not in ALLOWED_FIRST_WORDS:
        parser.error(
            f"only read-only queries are allowed (statement starts with {first_word}); "
            f"must start with one of: {', '.join(sorted(ALLOWED_FIRST_WORDS))}"
        )
    return stmt


def main():
    parser = argparse.ArgumentParser(description="Run one read-only SQL query (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("sql", nargs="?", help="The query, in quotes.")
    source.add_argument("--file", "-f", help="Read the query from this .sql file instead.")
    parser.add_argument("--limit", "-n", type=positive_int, default=50, help="Max rows to print (default 50). --output still gets every row.")
    parser.add_argument("--output", "-o", help="Also write the full result (every row, unformatted) to this CSV file.")
    args = parser.parse_args()

    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                sql = f.read()
        except OSError as e:
            parser.error(f"can't read {args.file}: {e.strerror}")
    else:
        sql = args.sql
    stmt = check_read_only(parser, sql)

    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute("begin")
        try:
            cs.execute(stmt)
            cols = [d[0] for d in cs.description]
            json_cols = json_columns(cs.description)
            # Without --output, only fetch what will be printed (plus one row to tell whether there's more).
            rows = cs.fetchall() if args.output else cs.fetchmany(args.limit + 1)
        finally:
            cs.execute("rollback")

        shown = rows[:args.limit]
        print_table(cols, [[truncate(fmt_cell(v, is_json)) for v, is_json in zip(row, json_cols)] for row in shown])
        if len(rows) > args.limit and args.output:
            print(f"\n(showing first {len(shown):,} of {plural(len(rows), 'row')} - all of them go to the CSV)")
        elif len(rows) > args.limit:
            print(f"\n(showing first {plural(len(shown), 'row')}, more available - raise --limit or use --output to get them all)")
        else:
            print(f"\n({plural(len(rows), 'row')})")

        if args.output:
            write_csv(args.output, cols, rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
