"""
Read-only: quick sanity check that your Snowflake connection works.
Prints current user/role/warehouse/account/version, then lists every
accessible database (and schemas in the configured database, if set).

Usage:
  python test_connection.py
"""

from sf_conn import get_connection, current_database, run_cli


def main():
    conn = get_connection()
    try:
        cs = conn.cursor()
        cs.execute(
            "select current_user(), current_role(), current_warehouse(), "
            "current_account(), current_version()"
        )
        user, role, warehouse, account, version = cs.fetchone()
        print("Connection OK")
        print(f"  User:      {user}")
        print(f"  Role:      {role}")
        print(f"  Warehouse: {warehouse}")
        print(f"  Account:   {account}")
        print(f"  Version:   {version}")

        print("\nAccessible databases:")
        cs.execute("show databases")
        for row in cs.fetchall():
            print(f"  - {row[1]}")

        try:
            db = current_database()
        except SystemExit:
            db = None
        if db:
            print(f"\nSchemas in {db}:")
            cs.execute(f"show schemas in database {db}")
            for row in cs.fetchall():
                print(f"  - {row[1]}")

    finally:
        conn.close()


if __name__ == "__main__":
    run_cli(main)
