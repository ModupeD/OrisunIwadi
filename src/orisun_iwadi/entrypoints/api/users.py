"""Manage API users. The API itself is read-only, so users are created here.

    uv run python -m orisun_iwadi.entrypoints.api.users add alice --role researcher
    uv run python -m orisun_iwadi.entrypoints.api.users deactivate alice

The password is asked for interactively, or read from ORISUN_PASSWORD (for scripts).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import psycopg

from ...adapters.security.passwords import ScryptHasher
from ...adapters.storage.user_store import PostgresUserStore
from ...domain.access import Role

MIN_PASSWORD_LENGTH = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="create a user, or reset their password and role")
    add.add_argument("username")
    add.add_argument("--role", choices=[r.value for r in Role], default=Role.VIEWER.value)
    off = sub.add_parser("deactivate", help="stop a user from signing in")
    off.add_argument("username")
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn:
        store = PostgresUserStore(conn)
        if args.command == "add":
            password = os.getenv("ORISUN_PASSWORD") or getpass.getpass("Password: ")
            if len(password) < MIN_PASSWORD_LENGTH:
                print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
                      file=sys.stderr)
                return 1
            store.save(args.username, ScryptHasher().hash(password), Role(args.role))
            print(f"Saved {args.username} ({args.role}).")
        else:
            if not store.deactivate(args.username):
                print(f"No user named {args.username}.", file=sys.stderr)
                return 1
            print(f"Deactivated {args.username}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
