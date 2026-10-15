"""
Read-only: compares the rows of two tables/views matched on a key - the
next step after compare_counts.py says two copies differ. Reports rows
only in A, rows only in B, and rows whose key is in both but whose values
differ, with which columns differ and how often.

Either table can be SCHEMA.TABLE (in SNOWFLAKE_DATABASE) or
DATABASE.SCHEMA.TABLE. Columns with the same name in both are compared;
the summary lists any that exist on one side only. When a column's type
differs between the two (e.g. TEXT in one, NUMBER in the other) values
are compared as text.

Usage:
  python diff_rows.py SCHEMA.TABLE_A SCHEMA.TABLE_B --key ID
  python diff_rows.py SCHEMA.TABLE_A OTHER_DB.SCHEMA.TABLE_B --key ORDER_ID LINE_NO
  python diff_rows.py SCHEMA.TABLE_A SCHEMA.TABLE_B --key NODE_ID:ID      # key named differently in B
  python diff_rows.py SCHEMA.TABLE_A SCHEMA.TABLE_B --key ID --limit 50
  python diff_rows.py SCHEMA.TABLE_A SCHEMA.TABLE_B --key ID --output diffs.csv   # CSV gets every difference

The key must be unique on both sides (check with find_duplicates.py).
Rows with a NULL key can't be matched and show as only in A / only in B.
"""

import argparse

from sf_conn import get_connection, resolve_table_target, resolve_column, info_schema, quote_ident, quoted, positive_int, print_table, write_csv, run_cli, fmt_count, fmt_pct, fmt_value, truncate, plural, UNGROUPABLE_TYPES


def fetch_columns(conn, db, schema, table):
    """Return {column_name: data_type} in column order."""
    cs = conn.cursor()
    cs.execute(
        f'select column_name, data_type from {info_schema(db, "columns")} '
        "where table_schema = %s and table_name = %s order by ordinal_position",
        (schema, table),
    )
    return dict(cs.fetchall())


def same_value(a_expr, b_expr, a_type, b_type):
    """SQL that's true when two values match (NULL matches NULL); compared as text when the types differ."""
    if a_type == b_type:
        return f"equal_null({a_expr}, {b_expr})"
    return f"equal_null(to_varchar({a_expr}), to_varchar({b_expr}))"


def same_key(a_expr, b_expr, a_type, b_type):
    """SQL join condition for one key column; compared as text when the types differ."""
    if a_type == b_type:
        return f"{a_expr} = {b_expr}"
    return f"to_varchar({a_expr}) = to_varchar({b_expr})"


def main():
    parser = argparse.ArgumentParser(description="Compare two tables' rows matched on a key: rows only in one, and rows whose values differ.")
    parser.add_argument("table_a", help="First table/view: SCHEMA.TABLE or DATABASE.SCHEMA.TABLE.")
    parser.add_argument("table_b", help="Second table/view: SCHEMA.TABLE or DATABASE.SCHEMA.TABLE.")
    parser.add_argument("--key", "-k", nargs="+", required=True, metavar="COL",
                        help="Key column(s) to match rows on, case-insensitive. Use A_COL:B_COL if it's named differently in B.")
    parser.add_argument("--limit", "-n", type=positive_int, default=20, help="How many differing rows to list (default 20). --output gets them all.")
    parser.add_argument("--output", "-o", help="Also write every differing row to this CSV file.")
    args = parser.parse_args()

    conn = get_connection()
    try:
        cs = conn.cursor()
        db_a, schema_a, name_a, _ = resolve_table_target(conn, parser, args.table_a)
        db_b, schema_b, name_b, _ = resolve_table_target(conn, parser, args.table_b)
        if (db_a, schema_a, name_a) == (db_b, schema_b, name_b):
            parser.error("both arguments are the same table")
        cross_db = db_a != db_b
        label_a = f"{db_a}.{schema_a}.{name_a}" if cross_db else f"{schema_a}.{name_a}"
        label_b = f"{db_b}.{schema_b}.{name_b}" if cross_db else f"{schema_b}.{name_b}"

        # Keys as (column in A, column in B).
        keys = []
        for k in args.key:
            col_a, _, col_b = k.partition(":")
            real_a, _ = resolve_column(conn, db_a, schema_a, name_a, col_a)
            real_b, _ = resolve_column(conn, db_b, schema_b, name_b, col_b or col_a)
            keys.append((real_a, real_b))

        cols_a = fetch_columns(conn, db_a, schema_a, name_a)
        cols_b = fetch_columns(conn, db_b, schema_b, name_b)
        key_cols_a, key_cols_b = {k[0] for k in keys}, {k[1] for k in keys}
        compared = [c for c in cols_a if c in cols_b and c not in key_cols_a and c not in key_cols_b
                    and cols_a[c] not in UNGROUPABLE_TYPES and cols_b[c] not in UNGROUPABLE_TYPES]
        only_a = [c for c in cols_a if c not in cols_b and c not in key_cols_a]
        only_b = [c for c in cols_b if c not in cols_a and c not in key_cols_b]
        skipped = [c for c in cols_a if c in cols_b and c not in compared and c not in key_cols_a and c not in key_cols_b]

        src_a, src_b = quoted(db_a, schema_a, name_a), quoted(db_b, schema_b, name_b)

        # A duplicated key would multiply rows in the join and give nonsense counts.
        for src, label, key_cols in ((src_a, label_a, [k[0] for k in keys]), (src_b, label_b, [k[1] for k in keys])):
            cols = ", ".join(quote_ident(c) for c in key_cols)
            cs.execute(f"select count(*) from (select {cols} from {src} group by {cols} having count(*) > 1)")
            dupes = cs.fetchone()[0]
            if dupes:
                raise SystemExit(f"The key ({', '.join(key_cols)}) isn't unique in {label}: {plural(dupes, 'value repeats', 'values repeat')}. "
                                 "Pick a unique key (find_duplicates.py shows the repeats).")

        # One full outer join; d_i is true where compared column i differs.
        a_cols = ", ".join(quote_ident(c) for c in [k[0] for k in keys] + compared)
        b_cols = ", ".join(quote_ident(c) for c in [k[1] for k in keys] + compared)
        join_on = " and ".join(same_key(f"a.{quote_ident(ka)}", f"b.{quote_ident(kb)}", cols_a[ka], cols_b[kb]) for ka, kb in keys)
        diff_flags = [f"not {same_value(f'a.{quote_ident(c)}', f'b.{quote_ident(c)}', cols_a[c], cols_b[c])} as d_{i}" for i, c in enumerate(compared)]
        any_diff = " or ".join(f"d_{i}" for i in range(len(compared))) or "false"
        key_select = ", ".join(f"a.{quote_ident(ka)} as ka_{i}, b.{quote_ident(kb)} as kb_{i}" for i, (ka, kb) in enumerate(keys))
        joined = (
            f"with j as (select {key_select}, a.__in_a is not null as in_a, b.__in_b is not null as in_b"
            + "".join(", " + f for f in diff_flags)
            + f" from (select {a_cols}, true as __in_a from {src_a}) a"
            f" full outer join (select {b_cols}, true as __in_b from {src_b}) b on {join_on}) "
        )

        cs.execute(
            joined + "select count_if(in_a), count_if(in_b), count_if(in_a and not in_b), count_if(in_b and not in_a), "
            f"count_if(in_a and in_b and ({any_diff}))"
            + "".join(f", count_if(in_a and in_b and d_{i})" for i in range(len(compared)))
            + " from j"
        )
        rows_a, rows_b, n_only_a, n_only_b, n_diff, *col_diffs = cs.fetchone()
        matched = rows_a - n_only_a

        # Sort by key - natively when both sides share its type, else as text.
        order = ", ".join(
            f"coalesce(ka_{i}, kb_{i})" if cols_a[ka] == cols_b[kb] else f"coalesce(to_varchar(ka_{i}), to_varchar(kb_{i}))"
            for i, (ka, kb) in enumerate(keys)
        )
        cs.execute(
            joined + f"select {', '.join(f'ka_{i}, kb_{i}' for i in range(len(keys)))}, "
            "case when not in_b then 'ONLY_IN_A' when not in_a then 'ONLY_IN_B' else 'DIFFERENT' end as status, "
            "array_to_string(array_construct_compact("
            + (", ".join(f"iff(in_a and in_b and d_{i}, %s, null)" for i in range(len(compared))) or "null")
            + "), ', ') as columns "
            f"from j where not (in_a and in_b) or {any_diff} order by status, {order}",
            tuple(compared),
        )
        rows = cs.fetchall() if args.output else cs.fetchmany(args.limit)

        print(f"Rows: {label_a} (A) vs {label_b} (B), matched on {', '.join(a if a == b else f'{a}:{b}' for a, b in keys)}\n")
        print(f"  A: {plural(rows_a, 'row')}   B: {plural(rows_b, 'row')}")
        print(f"  in both: {fmt_count(matched)} - {fmt_count(matched - n_diff)} identical, {fmt_count(n_diff)} with different values")
        print(f"  only in A: {fmt_count(n_only_a)}   only in B: {fmt_count(n_only_b)}")
        print()

        per_column = sorted(((n, c) for n, c in zip(col_diffs, compared) if n), reverse=True)
        if per_column:
            print_table(["COLUMN", "ROWS_DIFFERENT", "%_OF_MATCHED"], [[c, fmt_count(n), fmt_pct(n, matched)] for n, c in per_column])
            print()

        display_rows, csv_rows = [], []
        for r in rows:
            key_vals = [r[2 * i] if r[2 * i] is not None else r[2 * i + 1] for i in range(len(keys))]
            status, cols = r[-2], r[-1] or ""
            # Keys go into the CSV raw (no comma grouping), so they can be looked up.
            csv_rows.append(key_vals + [status, cols])
            display_rows.append([truncate(fmt_value(v)) for v in key_vals] + [status, truncate(cols)])
        if display_rows:
            shown = display_rows[:args.limit]
            print_table([k[0] for k in keys] + ["STATUS", "DIFFERENT_COLUMNS"], shown)
            total = n_only_a + n_only_b + n_diff
            more = f"first {len(shown):,} of {plural(total, 'differing row')}" if total > len(shown) else plural(total, "differing row")
            print(f"\n({more} - use --output for all of them)" if total > len(shown) else f"\n({more})")
        else:
            print("No differences - every row matches.")

        notes = [f"{plural(len(compared), 'column')} compared"]
        if only_a:
            notes.append(f"only in A, not compared: {', '.join(only_a)}")
        if only_b:
            notes.append(f"only in B, not compared: {', '.join(only_b)}")
        if skipped:
            notes.append(f"can't be compared ({'/'.join(sorted(UNGROUPABLE_TYPES))}): {', '.join(skipped)}")
        text_cols = [c for c in compared if cols_a[c] != cols_b[c]] + [ka for ka, kb in keys if cols_a[ka] != cols_b[kb]]
        if text_cols:
            notes.append(f"compared as text (types differ): {', '.join(text_cols)}")
        print("; ".join(notes) + ".")

        if args.output:
            write_csv(args.output, [k[0] for k in keys] + ["status", "different_columns"], csv_rows)

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
