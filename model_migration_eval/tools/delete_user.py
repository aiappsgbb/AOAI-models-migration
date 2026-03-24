#!/usr/bin/env python3
"""
Delete a user from the system: removes the SQLite record, OTP codes,
and the entire per-user directory tree.

Usage:
    python tools/delete_user.py user@example.com

    # Dry-run (show what would be deleted, without touching anything):
    python tools/delete_user.py user@example.com --dry-run

    # Skip the confirmation prompt:
    python tools/delete_user.py user@example.com --yes
"""

import argparse
import re
import shutil
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "auth.db"


# ── helpers ──────────────────────────────────────────────────────────

def _email_to_slug(email: str) -> str:
    """Convert an email to a filesystem-safe user_id slug."""
    slug = email.lower().strip()
    slug = slug.replace("@", "_at_")
    slug = re.sub(r"[^\w]", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    return slug.strip("_")


def _sizeof_fmt(num_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _dir_stats(path: Path):
    """Return (file_count, total_bytes) for a directory tree."""
    files = 0
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                files += 1
                total += f.stat().st_size
    return files, total


# ── core ─────────────────────────────────────────────────────────────

def delete_user(email: str, *, dry_run: bool = False) -> bool:
    """Delete a user entirely.  Returns True if anything was deleted."""
    slug = _email_to_slug(email)
    user_dir = _PROJECT_ROOT / "data" / "users" / slug
    db_path = _DEFAULT_DB

    print(f"  Email    : {email}")
    print(f"  User ID  : {slug}")
    print(f"  DB       : {db_path}")
    print(f"  User dir : {user_dir}")
    print()

    found_anything = False

    # ── 1. Check SQLite record ───────────────────────────────────────
    db_user = None
    otp_count = 0
    if db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (slug,)
        ).fetchone()
        if row:
            db_user = dict(row)
            found_anything = True
        # Count OTP codes
        try:
            otp_count = conn.execute(
                "SELECT COUNT(*) FROM auth_codes WHERE email = ?",
                (email.lower().strip(),),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass  # table may not exist
        conn.close()

    if db_user:
        print(f"  ✔ DB record found:")
        print(f"      created_at : {db_user.get('created_at', '?')}")
        print(f"      last_login : {db_user.get('last_login', '?')}")
    else:
        print("  ✗ No DB record found")

    if otp_count:
        print(f"  ✔ {otp_count} OTP code(s) in auth_codes")
    else:
        print("  ✗ No OTP codes found")

    # ── 2. Check user directory ──────────────────────────────────────
    if user_dir.is_dir():
        found_anything = True
        file_count, total_bytes = _dir_stats(user_dir)
        print(f"  ✔ User directory exists: {file_count} files, {_sizeof_fmt(total_bytes)}")
        # Show top-level contents
        for child in sorted(user_dir.iterdir()):
            tag = "📁" if child.is_dir() else "📄"
            if child.is_dir():
                fc, fb = _dir_stats(child)
                print(f"      {tag} {child.name}/  ({fc} files, {_sizeof_fmt(fb)})")
            else:
                print(f"      {tag} {child.name}  ({_sizeof_fmt(child.stat().st_size)})")
    else:
        print("  ✗ User directory does not exist")

    print()

    if not found_anything:
        print("  ⚠  Nothing to delete for this user.")
        return False

    # ── 3. Delete ────────────────────────────────────────────────────
    if dry_run:
        print("  🔍 DRY RUN — no changes made.")
        return True

    deleted = []

    # 3a. Delete DB records
    if db_path.is_file() and (db_user or otp_count):
        conn = sqlite3.connect(str(db_path))
        if db_user:
            conn.execute("DELETE FROM users WHERE id = ?", (slug,))
            deleted.append("DB user record")
        if otp_count:
            conn.execute(
                "DELETE FROM auth_codes WHERE email = ?",
                (email.lower().strip(),),
            )
            deleted.append(f"{otp_count} OTP code(s)")
        conn.commit()
        conn.close()

    # 3b. Delete user directory tree
    if user_dir.is_dir():
        shutil.rmtree(user_dir)
        deleted.append(f"directory {user_dir.relative_to(_PROJECT_ROOT)}")

    print("  🗑  Deleted:")
    for item in deleted:
        print(f"      • {item}")

    print()
    print("  ✅ User deleted successfully.")
    return True


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Delete a user and all their data from the system.",
    )
    parser.add_argument(
        "email",
        help="Email address of the user to delete.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making changes.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args()

    email = args.email.strip()
    if "@" not in email:
        print(f"ERROR: '{email}' doesn't look like a valid email.", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"  ╔══════════════════════════════════════╗")
    print(f"  ║       DELETE USER                     ║")
    print(f"  ╚══════════════════════════════════════╝")
    print()

    # Preview first
    slug = _email_to_slug(email)
    user_dir = _PROJECT_ROOT / "data" / "users" / slug
    has_dir = user_dir.is_dir()
    has_db = False
    if _DEFAULT_DB.is_file():
        conn = sqlite3.connect(str(_DEFAULT_DB))
        has_db = conn.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?", (slug,)
        ).fetchone()[0] > 0
        conn.close()

    if not has_dir and not has_db:
        print(f"  Email    : {email}")
        print(f"  User ID  : {slug}")
        print()
        print("  ⚠  Nothing to delete for this user.")
        sys.exit(0)

    if not args.dry_run and not args.yes:
        delete_user(email, dry_run=True)
        print()
        answer = input("  ⚠  Are you sure you want to DELETE this user? [y/N] ").strip()
        if answer.lower() not in ("y", "yes", "si", "sí", "s"):
            print("  Cancelled.")
            sys.exit(0)
        print()

    ok = delete_user(email, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
