#!/usr/bin/env python3
"""Grant or revoke staff access.

Staff can open the studio and edit the shared vendor knowledge; customers
cannot. There is deliberately no endpoint for this — it's granted from the
server, by someone with shell access.

    python scripts/make_staff.py me@firm.fo             # promote
    python scripts/make_staff.py me@firm.fo --revoke    # demote
    python scripts/make_staff.py --list                 # who is staff?

Register the account through the normal signup first, then promote it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db, use_database  # noqa: E402
from app.db_models import User  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", nargs="?", help="the account to promote or demote")
    parser.add_argument("--revoke", action="store_true", help="remove staff access instead")
    parser.add_argument("--list", action="store_true", help="list the staff accounts")
    parser.add_argument("--db", help="database path or URL (else LESARIN_DB / LESARIN_DATABASE_URL)")
    args = parser.parse_args()

    if args.db:
        use_database(args.db)
    init_db()

    with SessionLocal() as session:
        if args.list:
            staff = session.scalars(select(User).where(User.is_staff.is_(True)).order_by(User.email))
            emails = [u.email for u in staff]
            print("\n".join(emails) if emails else "No staff accounts yet.")
            return 0

        if not args.email:
            parser.error("give an email address, or --list")

        user = auth.set_staff(session, args.email, staff=not args.revoke)
        if user is None:
            print(f"No account for {args.email!r}. Register it first, then run this.", file=sys.stderr)
            return 1

        print(f"{user.email} is {'staff' if user.is_staff else 'no longer staff'}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
