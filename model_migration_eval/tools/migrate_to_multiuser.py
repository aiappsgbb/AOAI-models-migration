#!/usr/bin/env python3
"""
Migrate existing single-user data to the multi-user directory layout.

This script takes the current global ``prompts/``, ``data/synthetic/``,
and ``data/results/`` directories and copies them into a named user
namespace under ``data/users/<user_id>/``.

Usage:
    python tools/migrate_to_multiuser.py --email admin@example.com

    # Dry-run (show what would be copied):
    python tools/migrate_to_multiuser.py --email admin@example.com --dry-run

After running, the original global directories are left intact so the
application can still use them as seed data for new users.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _email_to_slug(email: str) -> str:
    """Convert an email to a filesystem-safe user_id slug."""
    slug = email.lower().strip()
    slug = slug.replace('@', '_at_')
    slug = re.sub(r'[^a-z0-9_]', '_', slug)
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug


def migrate(email: str, *, dry_run: bool = False):
    """Copy global data into a per-user namespace."""
    user_id = _email_to_slug(email)
    user_base = _PROJECT_ROOT / "data" / "users" / user_id
    print(f"User ID   : {user_id}")
    print(f"User base : {user_base}")
    print()

    copies = []

    # 1) prompts/ → data/users/<uid>/prompts/
    prompts_src = _PROJECT_ROOT / "prompts"
    prompts_dst = user_base / "prompts"
    if prompts_src.exists():
        skip = {"__pycache__"}
        for item in sorted(prompts_src.rglob("*")):
            if item.is_file() and not any(p in item.parts for p in skip):
                rel = item.relative_to(prompts_src)
                copies.append((item, prompts_dst / rel))

    # 2) data/synthetic/ → data/users/<uid>/synthetic/
    data_src = _PROJECT_ROOT / "data" / "synthetic"
    data_dst = user_base / "synthetic"
    if data_src.exists():
        skip = {"__pycache__"}
        for item in sorted(data_src.rglob("*")):
            if item.is_file() and not any(p in item.parts for p in skip):
                rel = item.relative_to(data_src)
                copies.append((item, data_dst / rel))

    # 3) data/results/ → data/users/<uid>/results/
    results_src = _PROJECT_ROOT / "data" / "results"
    results_dst = user_base / "results"
    if results_src.exists():
        for item in sorted(results_src.glob("*.json")):
            copies.append((item, results_dst / item.name))

    if not copies:
        print("Nothing to migrate — no source data found.")
        return

    print(f"Files to copy: {len(copies)}")
    print()

    for src, dst in copies:
        label = f"  {src.relative_to(_PROJECT_ROOT)} → {dst.relative_to(_PROJECT_ROOT)}"
        if dry_run:
            print(f"[DRY-RUN] {label}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[COPIED]  {label}")

    print()
    if dry_run:
        print("Dry-run complete. No files were copied.")
    else:
        print(f"Migration complete. User '{user_id}' data is ready at {user_base}")

    # 4) Create user record in auth.db so they can log in
    if not dry_run:
        try:
            sys.path.insert(0, str(_PROJECT_ROOT))
            from src.auth.user_store import UserStore
            store = UserStore(db_path=str(_PROJECT_ROOT / "data" / "auth.db"))
            user = store.get_or_create(email)
            print(f"Auth record created/verified for {email} (id={user.id})")
        except Exception as e:
            print(f"Warning: could not create auth record: {e}")
            print("The user can still log in via OTP — their account will be created on first login.")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate existing single-user data to a multi-user namespace."
    )
    parser.add_argument(
        "--email", required=True,
        help="Email address of the user to create the namespace for.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be copied without making changes.",
    )
    args = parser.parse_args()
    migrate(args.email, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
