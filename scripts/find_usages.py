"""
Read-only: finds every view, dynamic table, task, function and procedure
in the configured database whose SQL mentions a given name - a table,
view or column - to answer "what breaks if I change or drop this?".

The name is matched as a whole word, case-insensitively, so ORDERS finds
"ORDERS", "public.orders" and PUBLIC."ORDERS" but not ORDERS_DAILY (use
--partial for that). A schema-qualified name like JIRA.ISSUES matches
only where it's written qualified - search the bare name to also catch
unqualified references.

Usage:
  python find_usages.py ISSUES
  python find_usages.py JIRA.ISSUES
  python find_usages.py CANONICALPRODUCT_ID --schema CUT_AND_DRY_PROD_SCHEMA
  python find_usages.py ORDERS --partial                 # also ORDERS_DAILY etc.
  python find_usages.py ORDERS --database OTHER_DB      # search another database
  python find_usages.py ORDERS --output usages.csv      # CSV has every matching line

Functions and procedures are only searched where your role can see their
code; the summary says how many it couldn't.

--dependencies works differently: for one SCHEMA.TABLE_OR_VIEW it lists the
objects in ANY database that depend on it - directly, or through other
views/dynamic tables - from Snowflake's own lineage record
(SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES). That includes functions
whose code your role can't see, but it can lag up to 3 hours behind, needs
access to the SNOWFLAKE database, and only knows about whole objects, not
columns.

  python find_usages.py CUT_AND_DRY_PROD_SCHEMA.COMPANY --dependencies
"""

import argparse
import re

from sf_conn import get_connection, current_database, resolve_database, resolve_schema, resolve_object, split_table_arg, quoted, info_schema, show_rows, print_table, write_csv, run_cli, plural

# How much SQL to show either side of the first match.
CONTEXT_CHARS = 30
# How many levels of views-on-views --dependencies follows.
MAX_DEPTH = 10


def body(sql):
    """
    The part of a CREATE VIEW / DYNAMIC TABLE statement after its first AS,
    so the object's own name in "create view X as" doesn't count as a use.
    """
    m = re.search(r"\bas\b", sql, re.IGNORECASE)
    return sql[m.end():] if m else sql


def find_matches(pattern, sql):
    """Return the regex matches of `pattern` in `sql`, ignoring double quotes around identifiers."""
    return list(pattern.finditer(sql.replace('"', "")))


def context(sql, match):
    """One line of SQL around a match, e.g. '...from JIRA.ISSUES where...'."""
    flat = sql.replace('"', "")
    start, end = max(match.start() - CONTEXT_CHARS, 0), match.end() + CONTEXT_CHARS
    snippet = " ".join(flat[start:end].split())
    return ("..." if start else "") + snippet + ("..." if end < len(flat) else "")


def list_dependencies(args, parser, conn, db):
    """--dependencies: everything that depends on one table/view, via ACCOUNT_USAGE.OBJECT_DEPENDENCIES."""
    schema, name = split_table_arg(parser, args.name)
    real_schema, real_name, kind = resolve_object(conn, schema, name, db)

    # Walk the dependency graph outwards: level 1 uses the object directly,
    # level 2 uses something at level 1, and so on. VIA is the chain in between.
    cs = conn.cursor()
    cs.execute(
        "with recursive deps (db, sch, obj, kind, dep_type, level, via) as ("
        "  select referencing_database, referencing_schema, referencing_object_name, referencing_object_domain,"
        "         dependency_type, 1, ''"
        "  from snowflake.account_usage.object_dependencies"
        "  where referenced_database = %s and referenced_schema = %s and referenced_object_name = %s"
        "  union all"
        "  select d.referencing_database, d.referencing_schema, d.referencing_object_name, d.referencing_object_domain,"
        "         d.dependency_type, deps.level + 1,"
        "         deps.via || iff(deps.via = '', '', ' -> ') || deps.db || '.' || deps.sch || '.' || deps.obj"
        "  from snowflake.account_usage.object_dependencies d"
        "  join deps on d.referenced_database = deps.db and d.referenced_schema = deps.sch and d.referenced_object_name = deps.obj"
        "  where deps.level < %s"
        ") "
        "select db, sch, obj, kind, min_by(dep_type, level), min(level), min_by(via, level) "
        "from deps group by 1, 2, 3, 4 order by 6, 1, 2, 3",
        (db, real_schema, real_name, MAX_DEPTH),
    )
    rows = cs.fetchall()

    print(f"Objects that depend on {db}.{real_schema}.{real_name}  [{kind}]\n")
    if not rows:
        print("None found. (Lineage can lag up to 3 hours behind new objects.)")
        return

    display_rows, csv_rows = [], []
    for obj_db, obj_schema, obj, obj_kind, dep_type, level, via in rows:
        # Objects in the same database are shown as SCHEMA.NAME, others in full.
        label = f"{obj_schema}.{obj}" if obj_db == db else f"{obj_db}.{obj_schema}.{obj}"
        direct = "direct" if level == 1 else f"via {plural(level - 1, 'object')}"
        # On screen just the object it reaches the target through; the CSV has the whole chain.
        through = via.rsplit(" -> ", 1)[-1].removeprefix(f"{db}.")
        display_rows.append([label, obj_kind, direct, dep_type, through])
        csv_rows.append([obj_db, obj_schema, obj, obj_kind, level, dep_type, via])

    print_table(["OBJECT", "KIND", "LEVEL", "DEPENDENCY", "THROUGH"], display_rows)
    n_direct = sum(1 for r in rows if r[5] == 1)
    n_other_db = sum(1 for r in rows if r[0] != db)
    print(f"\n({plural(len(rows), 'dependent object')}, {n_direct:,} direct; {n_other_db:,} in other databases)")
    print("From ACCOUNT_USAGE.OBJECT_DEPENDENCIES, which can lag up to 3 hours. BY_NAME means it breaks if the name changes.")

    if args.output:
        write_csv(args.output, ["database", "schema", "object", "kind", "level", "dependency_type", "via"], csv_rows)


def main():
    parser = argparse.ArgumentParser(description="Find views, dynamic tables, tasks, functions and procedures whose SQL mentions a name.")
    parser.add_argument("name", help="Table, view or column name to look for, optionally SCHEMA-qualified.")
    parser.add_argument("--partial", action="store_true", help="Match the name anywhere, not just as a whole word.")
    parser.add_argument("--schema", help="Only search objects in this schema (case-insensitive).")
    parser.add_argument("--database", help="Search this database instead of SNOWFLAKE_DATABASE.")
    parser.add_argument("--dependencies", action="store_true",
                        help="Instead, list objects in any database that depend on this SCHEMA.TABLE_OR_VIEW, from Snowflake's lineage record.")
    parser.add_argument("--output", "-o", help="Also write the result, with every matching line, to this CSV file.")
    args = parser.parse_args()

    if args.dependencies and (args.partial or args.schema):
        parser.error("--dependencies can't be combined with --partial or --schema")

    name = args.name.replace('"', "").strip()
    if not name:
        parser.error("name can't be empty")
    pattern = re.escape(name) if args.partial else r"(?<![\w$])" + re.escape(name) + r"(?![\w$])"
    pattern = re.compile(pattern, re.IGNORECASE)

    conn = get_connection()
    try:
        cs = conn.cursor()
        db = resolve_database(conn, args.database) if args.database else current_database()
        if args.dependencies:
            list_dependencies(args, parser, conn, db)
            return
        real_schema = resolve_schema(conn, db, args.schema) if args.schema else None
        scope = f"schema {quoted(db, real_schema)}" if real_schema else f"database {quoted(db)}"

        # (schema, name, kind, sql) for everything whose SQL we can read.
        objects, hidden = [], 0
        for r in show_rows(cs, f"show views in {scope}"):
            if r["schema_name"] != "INFORMATION_SCHEMA" and r.get("text"):
                kind = "MATERIALIZED VIEW" if str(r.get("is_materialized")).lower() == "true" else "VIEW"
                objects.append((r["schema_name"], r["name"], kind, body(r["text"])))
        for r in show_rows(cs, f"show dynamic tables in {scope}"):
            objects.append((r["schema_name"], r["name"], "DYNAMIC TABLE", body(r.get("text") or "")))
        for r in show_rows(cs, f"show tasks in {scope}"):
            sql = r.get("definition") or ""
            if r.get("condition"):
                sql += "\n" + r["condition"]
            objects.append((r["schema_name"], r["name"], "TASK", sql))

        schema_filter, params = "", ()
        if real_schema:
            schema_filter, params = " and {}_schema = %s", (real_schema,)
        for kind, view in (("FUNCTION", "functions"), ("PROCEDURE", "procedures")):
            prefix = kind.lower()
            cs.execute(
                f"select {prefix}_schema, {prefix}_name || argument_signature, {prefix}_definition "
                f"from {info_schema(db, view)} where {prefix}_schema <> 'INFORMATION_SCHEMA'" + schema_filter.format(prefix),
                params,
            )
            for schema, signature, sql in cs.fetchall():
                if sql is None:
                    hidden += 1
                else:
                    objects.append((schema, signature, kind, sql))

        display_rows, csv_rows = [], []
        for schema, obj, kind, sql in sorted(objects, key=lambda o: (o[2], o[0], o[1])):
            matches = find_matches(pattern, sql)
            if not matches:
                continue
            display_rows.append([f"{schema}.{obj}", kind, f"{len(matches):,}", context(sql, matches[0])])
            csv_rows.append([schema, obj, kind, len(matches), " | ".join(context(sql, m) for m in matches)])

        where = f"{db}.{real_schema}" if real_schema else db
        how = "anywhere" if args.partial else "as a whole word"
        print(f"Objects in {where} whose SQL mentions '{name}' ({how})\n")
        if display_rows:
            print_table(["OBJECT", "KIND", "MATCHES", "FIRST MATCH"], display_rows)
        else:
            print("None found.")

        kinds = {}
        for row in display_rows:
            kinds[row[1]] = kinds.get(row[1], 0) + 1
        breakdown = ", ".join(plural(n, k.lower()) for k, n in sorted(kinds.items()))
        print(f"\n({plural(len(display_rows), 'object')}{': ' + breakdown if breakdown else ''}; searched {plural(len(objects), 'definition')})")
        if hidden:
            print(f"{plural(hidden, 'function/procedure', 'functions/procedures')} not searched - your role can't see their code.")
        if not args.partial and not display_rows:
            print("Try --partial to match the name inside longer names, or the bare name if you gave SCHEMA.NAME.")

        if args.output:
            write_csv(args.output, ["schema", "object", "kind", "matches", "matching_sql"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
