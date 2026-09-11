"""Push/pull this site's "brain" to the central knowledge service, signed
with this site's identity (``docs/brain-sync.md``).

    python -m site_agent.central_client push --central https://central.example
    python -m site_agent.central_client pull --central https://central.example

Distinct from ``python -m app.sync`` (a portable bundle for merging between a
customer's *own* installations, everything included, no gate): ``push`` sends
only what ``app.brain.export_push_bundle`` says is eligible — human-confirmed
field mappings and anonymous label observations, never a guess and never a
value. Central publishes a pushed template immediately (the confirmation
already happened here); ``pull`` merges back published templates and any
vocabulary that's crossed the shared k-anonymity threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

import httpx

from .identity import get_identity

_DEFAULT_TIMEOUT = 30.0


def push(central_url: str, bundle: dict) -> dict:
    identity = get_identity()
    token = identity.signed_jwt()
    resp = httpx.post(
        f"{central_url.rstrip('/')}/sync/push",
        json=bundle,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def pull(central_url: str) -> dict:
    identity = get_identity()
    token = identity.signed_jwt()
    resp = httpx.get(
        f"{central_url.rstrip('/')}/sync/pull",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def enroll(central_url: str, name: str, enrollment_token: str) -> dict:
    """One-time: register this site's public key centrally using a token an
    admin minted. Central activates the site before push/pull will work."""
    identity = get_identity()
    resp = httpx.post(
        f"{central_url.rstrip('/')}/sites/enroll",
        json={
            "name": name,
            "public_key": identity.public_pem.decode("ascii"),
            "enrollment_token": enrollment_token,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m site_agent.central_client",
        description="Enroll with, and push/pull the brain to, a central knowledge service.",
    )
    parser.add_argument("--central", required=True, help="central service base URL")
    parser.add_argument("--db", help="SQLite mapping store (overrides $LESARIN_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll", help="register this site's public key centrally")
    p_enroll.add_argument("--name", required=True, help="a human-readable name for this site")
    p_enroll.add_argument("--token", required=True, help="one-time enrollment token from central admin")

    sub.add_parser("push", help="push this site's brain to central")
    sub.add_parser("pull", help="pull central's published knowledge into this site")

    args = parser.parse_args(argv)

    if args.command == "enroll":
        result = enroll(args.central, args.name, args.token)
        print(json.dumps(result, indent=2))
        return 0

    from app.db import SessionLocal, init_db, use_database
    from app import brain, repo as app_repo

    if args.db:
        use_database(args.db)
    init_db()

    with SessionLocal() as session:
        if args.command == "push":
            bundle = brain.export_push_bundle(session)
            result = push(args.central, bundle)
            app_repo.clear_label_observations(session)  # contributed — don't resend
            print(json.dumps(result, indent=2), file=sys.stderr)
            return 0

        # pull
        bundle = pull(args.central)
        stats = brain.merge_pull_bundle(session, bundle)
        print(json.dumps(stats, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
