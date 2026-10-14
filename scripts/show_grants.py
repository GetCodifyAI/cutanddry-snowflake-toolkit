"""
Read-only: shows who has access to a table or view - one row per role
with the privileges it holds (OWNERSHIP, SELECT, ...). With --users, it
follows role membership (including roles granted to other roles) to list
the actual users who get access, and through which roles.

Note this lists grants on the object itself; to actually query it a role
also needs USAGE on its database and schema.

Usage:
  python show_grants.py SCHEMA.TABLE_OR_VIEW
  python show_grants.py SCHEMA.TABLE_OR_VIEW --users
  python show_grants.py SCHEMA.TABLE_OR_VIEW --privilege SELECT --users   # who can read it
  python show_grants.py SCHEMA.TABLE_OR_VIEW --output grants.csv
"""

import argparse

from sf_conn import get_connection, current_database, resolve_object, split_table_arg, quoted, quote_ident, print_table, write_csv, run_cli, fmt_value, truncate

import snowflake.connector.errors as sf_errors


def privilege_order(p):
    return (p != "OWNERSHIP", p)


def role_members(cs, role, cache):
    """
    Return (users, roles) the role is granted to, via SHOW GRANTS OF ROLE.
    Returns None if this role's membership isn't visible to the current role.
    """
    if role not in cache:
        try:
            cs.execute(f"show grants of role {quote_ident(role)}")
            columns = [c[0].lower() for c in cs.description]
            users, roles = set(), set()
            for row in cs.fetchall():
                r = dict(zip(columns, row))
                (users if r["granted_to"] == "USER" else roles if r["granted_to"] == "ROLE" else set()).add(r["grantee_name"])
            cache[role] = (users, roles)
        except sf_errors.ProgrammingError:
            cache[role] = None
    return cache[role]


def expand_to_users(cs, role_privileges):
    """
    Walk role membership from each role holding a privilege. Returns
    ({user: {"privileges": set, "paths": set}}, set of roles whose members couldn't be seen).
    A path like "ANALYST -> ALL_READ_ONLY" means the user holds ANALYST,
    which was granted ALL_READ_ONLY, which holds the privilege.
    """
    cache, users, hidden = {}, {}, set()
    for start_role, privileges in role_privileges.items():
        # Breadth-first up the role hierarchy; `chain` is the path from the user's role down to start_role.
        queue, seen = [(start_role, [start_role])], {start_role}
        while queue:
            role, chain = queue.pop(0)
            members = role_members(cs, role, cache)
            if members is None:
                hidden.add(role)
                continue
            member_users, member_roles = members
            for user in member_users:
                entry = users.setdefault(user, {"privileges": set(), "paths": set()})
                entry["privileges"] |= privileges
                entry["paths"].add(" -> ".join(reversed(chain)))
            for parent in member_roles:
                if parent not in seen:
                    seen.add(parent)
                    queue.append((parent, chain + [parent]))
    return users, hidden


def main():
    parser = argparse.ArgumentParser(description="Show which roles (and optionally users) have access to a table or view.")
    parser.add_argument("table", help="Schema-qualified table/view name, e.g. PUBLIC.ORDERS")
    parser.add_argument("--users", action="store_true", help="List the users who get access, following role membership.")
    parser.add_argument("--privilege", help="Only this privilege, e.g. SELECT or OWNERSHIP (case-insensitive).")
    parser.add_argument("--output", "-o", help="Also write the result to this CSV file.")
    args = parser.parse_args()

    schema, name = split_table_arg(parser, args.table)
    db = current_database()

    conn = get_connection()
    try:
        cs = conn.cursor()
        real_schema, real_name, _ = resolve_object(conn, schema, name, db)

        # SHOW GRANTS ON TABLE works for views and dynamic tables too.
        cs.execute(f"show grants on table {quoted(db, real_schema, real_name)}")
        columns = [c[0].lower() for c in cs.description]
        grants = [dict(zip(columns, row)) for row in cs.fetchall()]
        object_type = grants[0]["granted_on"].replace("_", " ") if grants else "OBJECT"
        if args.privilege:
            grants = [g for g in grants if g["privilege"].upper() == args.privilege.upper()]

        print(f"{real_schema}.{real_name}  [{object_type}]")
        print()
        if not grants:
            print(f"No {args.privilege.upper() + ' ' if args.privilege else ''}grants found.")
            return

        # One row per grantee: privileges combined, earliest grant date.
        by_role = {}
        for g in grants:
            key = (g["granted_to"], g["grantee_name"])
            entry = by_role.setdefault(key, {"privileges": set(), "grant_option": set(), "granted_by": set(), "since": g["created_on"]})
            entry["privileges"].add(g["privilege"])
            if str(g["grant_option"]).lower() == "true":
                entry["grant_option"].add(g["privilege"])
            entry["granted_by"].add(g["granted_by"] or "")
            entry["since"] = min(entry["since"], g["created_on"])

        if not args.users:
            rows = []
            for (granted_to, grantee), e in sorted(by_role.items(), key=lambda kv: (min(privilege_order(p) for p in kv[1]["privileges"]), kv[0][1])):
                rows.append([
                    grantee,
                    granted_to if granted_to != "ROLE" else "",
                    ", ".join(sorted(e["privileges"], key=privilege_order)),
                    ", ".join(sorted(e["grant_option"], key=privilege_order)),
                    ", ".join(sorted(e["granted_by"])),
                    fmt_value(e["since"]),
                ])
            print_table(["ROLE", "GRANTEE_TYPE", "PRIVILEGES", "CAN_REGRANT", "GRANTED_BY", "SINCE"], rows)
            print(f"\n({len(rows):,} roles - use --users to see which users get access)")
            if args.output:
                write_csv(args.output, ["role", "grantee_type", "privileges", "can_regrant", "granted_by", "since"], rows)
            return

        role_privileges = {grantee: e["privileges"] for (granted_to, grantee), e in by_role.items() if granted_to == "ROLE"}
        users, hidden = expand_to_users(cs, role_privileges)
        if not users:
            print("No users found with access through the granted roles.")
        else:
            display_rows, csv_rows = [], []
            for user in sorted(users):
                e = users[user]
                privileges = ", ".join(sorted(e["privileges"], key=privilege_order))
                paths = sorted(e["paths"], key=lambda p: (p.count("->"), p))
                display_rows.append([user, privileges, truncate("; ".join(paths))])
                csv_rows.append([user, privileges, "; ".join(paths)])
            print_table(["USER", "PRIVILEGES", "VIA_ROLES"], display_rows)
            print(f"\n({len(users):,} users - \"A -> B\" means the user holds role A, which was granted role B)")
            if args.output:
                write_csv(args.output, ["user", "privileges", "via_roles"], csv_rows)
        if hidden:
            print(f"Couldn't see the members of: {', '.join(sorted(hidden))} (your role lacks access) - users may be missing.")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
