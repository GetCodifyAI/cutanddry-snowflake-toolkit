"""
Read-only: compares row counts between two schemas, for every table/view
name present in both - handy for checking that a copy, migration or new
version of a schema (e.g. DTAM_V2 vs DTAM_V3) holds the same data. Tables
use Snowflake's stored row count (exact, instant); views have none, so
they're counted with COUNT(*), which can take a while on big views.

Either schema can be DATABASE.SCHEMA to compare across databases;
otherwise SNOWFLAKE_DATABASE from .env is used. For tables/views that
exist in only one schema, or column differences, use diff_schema.py.

Usage:
  python compare_counts.py SCHEMA_A SCHEMA_B
  python compare_counts.py SCHEMA_A OTHER_DB.SCHEMA_B
  python compare_counts.py SCHEMA_A SCHEMA_B --diff-only     # only tables whose counts differ
  python compare_counts.py SCHEMA_A SCHEMA_B --skip-views    # don't run COUNT(*) on views
  python compare_counts.py SCHEMA_A SCHEMA_B --output counts.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_schema, quoted, info_schema, print_table, write_csv, run_cli, kind_from_type, fmt_count, fmt_pct, truncate

import snowflake.connector.errors as sf_errors


def resolve_database(conn, name):
    """Look up a database by name, tolerant of case. Returns the exact stored name."""
    cs = conn.cursor()
    cs.execute(
        f'select database_name from {info_schema(current_database(), "databases")} where upper(database_name) = upper(%s)',
        (name,),
    )
    rows = cs.fetchall()
    if not rows:
        raise SystemExit(f"No database matching '{name}' found (or your role can't see it).")
    exact = [r[0] for r in rows if r[0] == name]
    if len(rows) > 1 and len(exact) != 1:
        raise SystemExit(f"Multiple databases matching '{name}': {', '.join(r[0] for r in rows)}. Type the exact case to pick one.")
    return exact[0] if len(exact) == 1 else rows[0][0]


def resolve_target(conn, value):
    """Turn 'SCHEMA' or 'DATABASE.SCHEMA' into (real_db, real_schema)."""
    if "." in value:
        db_name, schema_name = value.split(".", 1)
        db = resolve_database(conn, db_name)
    else:
        db, schema_name = current_database(), value
    return db, resolve_schema(conn, db, schema_name)


def fetch_objects(conn, db, schema):
    """Return {name: (kind, stored_row_count)} for a schema's tables and views."""
    cs = conn.cursor()
    cs.execute(
        f'select table_name, table_type, row_count from {info_schema(db, "tables")} where table_schema = %s',
        (schema,),
    )
    return {name: (kind_from_type(ttype), row_count) for name, ttype, row_count in cs.fetchall()}


def count_rows(conn, targets):
    """
    COUNT(*) each (db, schema, name) in `targets`, in one UNION ALL query.
    Returns {target: count}, or {target: 'error: ...'} for any object that
    can't be counted (e.g. a view whose underlying table was dropped).
    """
    if not targets:
        return {}
    cs = conn.cursor()
    parts = [f"select {i} as i, count(*) as n from {quoted(*t)}" for i, t in enumerate(targets)]
    try:
        cs.execute(" union all ".join(parts))
        return {targets[i]: n for i, n in cs.fetchall()}
    except sf_errors.ProgrammingError:
        # One bad object fails the whole query - count each separately to find it.
        results = {}
        for t in targets:
            try:
                cs.execute(f"select count(*) from {quoted(*t)}")
                results[t] = cs.fetchone()[0]
            except sf_errors.ProgrammingError as e:
                results[t] = "error: " + " ".join(str(e.msg or e).split())
        return results


def main():
    parser = argparse.ArgumentParser(description="Compare row counts of same-named tables/views in two schemas.")
    parser.add_argument("schema_a", help="First schema (SCHEMA or DATABASE.SCHEMA).")
    parser.add_argument("schema_b", help="Second schema (SCHEMA or DATABASE.SCHEMA).")
    parser.add_argument("--diff-only", action="store_true", help="Only show tables/views whose counts differ.")
    parser.add_argument("--skip-views", action="store_true", help="Don't COUNT(*) views (they show as 'skipped').")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    conn = get_connection()
    try:
        db_a, schema_a = resolve_target(conn, args.schema_a)
        db_b, schema_b = resolve_target(conn, args.schema_b)
        cross_db = db_a != db_b
        label_a = f"{db_a}.{schema_a}" if cross_db else schema_a
        label_b = f"{db_b}.{schema_b}" if cross_db else schema_b
        if (db_a, schema_a) == (db_b, schema_b):
            parser.error("both arguments are the same schema")

        objects_a = fetch_objects(conn, db_a, schema_a)
        objects_b = fetch_objects(conn, db_b, schema_b)
        common = sorted(set(objects_a) & set(objects_b))
        only_a = len(set(objects_a) - set(objects_b))
        only_b = len(set(objects_b) - set(objects_a))

        if not common:
            print(f"No tables/views with the same name in {label_a} and {label_b} "
                  f"({len(objects_a):,} vs {len(objects_b):,} objects). Try diff_schema.py.")
            return

        # Stored row_count is exact for tables; views (row_count NULL) need COUNT(*).
        to_count = []
        for name in common:
            for db, schema, objects in ((db_a, schema_a, objects_a), (db_b, schema_b, objects_b)):
                if objects[name][1] is None and not args.skip_views:
                    to_count.append((db, schema, name))
        if to_count:
            print(f"Counting rows in {len(to_count):,} views with COUNT(*)...\n", flush=True)
        counted = count_rows(conn, to_count)

        def count_for(db, schema, objects, name):
            stored = objects[name][1]
            if stored is not None:
                return stored
            return counted.get((db, schema, name), "skipped")

        display_rows, csv_rows = [], []
        mismatches = errors = skipped = 0
        for name in common:
            kind_a, kind_b = objects_a[name][0], objects_b[name][0]
            n_a = count_for(db_a, schema_a, objects_a, name)
            n_b = count_for(db_b, schema_b, objects_b, name)
            if isinstance(n_a, int) and isinstance(n_b, int):
                diff = n_b - n_a
                mismatches += diff != 0
                diff_s = f"{diff:+,}" if diff else "0"
                pct_s = "" if not diff else (f"{fmt_pct(diff, n_a)}" if n_a else "new rows")
                if pct_s.endswith("%") and diff > 0:
                    pct_s = "+" + pct_s
                status = "match" if not diff else "DIFFERENT"
            else:
                if any(isinstance(n, str) and n.startswith("error") for n in (n_a, n_b)):
                    errors += 1
                else:
                    skipped += 1
                diff_s, pct_s, status = "", "", "not compared"
            if args.diff_only and status == "match":
                continue
            kind = kind_a if kind_a == kind_b else f"{kind_a}/{kind_b}"
            row = [name, kind, fmt_count(n_a) if isinstance(n_a, int) else n_a,
                   fmt_count(n_b) if isinstance(n_b, int) else n_b, diff_s, pct_s, status]
            csv_rows.append(row)
            # Error messages are long - cut them short on screen, keep them whole in the CSV.
            display_rows.append(row[:2] + [truncate(row[2]), truncate(row[3])] + row[4:])

        print(f"Row counts: {label_a} vs {label_b}\n")
        if display_rows:
            print_table(["TABLE", "KIND", f"{label_a}_ROWS", f"{label_b}_ROWS", "DIFF", "DIFF_%", "STATUS"], display_rows)
        else:
            print("All row counts match.")

        summary = f"{len(common):,} tables/views in both, {mismatches:,} with different counts"
        if errors:
            summary += f", {errors:,} couldn't be counted"
        if skipped:
            summary += f", {skipped:,} views not compared (--skip-views)"
        if only_a or only_b:
            summary += f"; {only_a:,} only in {label_a}, {only_b:,} only in {label_b}"
            if not cross_db:
                summary += " (see diff_schema.py)"
        print(f"\n({summary})")

        if args.output:
            write_csv(args.output, ["table", "kind", f"{label_a}_rows", f"{label_b}_rows", "diff", "diff_pct", "status"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
