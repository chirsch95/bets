"""Per-user identity, password storage, sessions, and per-user data paths.

Path layout (everything under data/ is gitignored):

    data/users.json                   — account list (id, username, hash)
    data/users/<id>/bets.json         — that user's bets ledger
    data/users/<id>/bankroll.json     — that user's bankroll ledger
    data/users/<id>/prefs.json        — display name, stake units, pushover key

Auth model: username + password (werkzeug hash). Flask session cookie
holds {user_id}. Cookie is signed by FLASK_SECRET_KEY — auto-generated
into data/.flask_secret on first import if the env var is unset, since
we never want sessions to silently invalidate on each restart.

CLI:

    python -m bets.auth list                       # show users
    python -m bets.auth add-user <username>        # create + prompt for password
    python -m bets.auth set-password <username>    # change password
    python -m bets.auth delete-user <username>     # remove user + their data
"""

from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .config import DATA_DIR

USERS_PATH = DATA_DIR / "users.json"
USERS_DIR = DATA_DIR / "users"
SECRET_PATH = DATA_DIR / ".flask_secret"

# Username pattern: letters, digits, dash, underscore. 1-32 chars.
# Doubles as the directory name, so anything that would be ugly on disk
# is rejected at create time.
_USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


# ---------- path helpers ----------


def user_dir(user_id: str) -> Path:
    return USERS_DIR / user_id


def bets_path(user_id: str) -> Path:
    return user_dir(user_id) / "bets.json"


def bankroll_path(user_id: str) -> Path:
    return user_dir(user_id) / "bankroll.json"


def prefs_path(user_id: str) -> Path:
    return user_dir(user_id) / "prefs.json"


# ---------- users.json CRUD ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_users() -> dict:
    return {"users": []}


def load_users() -> dict:
    if not USERS_PATH.exists():
        return _empty_users()
    return json.loads(USERS_PATH.read_text())


def save_users(state: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(state, indent=2) + "\n")


def get_user(user_id: str) -> dict | None:
    for u in load_users().get("users", []):
        if u.get("id") == user_id:
            return u
    return None


def find_user_by_username(username: str) -> dict | None:
    username = (username or "").strip().lower()
    for u in load_users().get("users", []):
        if (u.get("username") or "").lower() == username:
            return u
    return None


def all_user_ids() -> list[str]:
    return [u["id"] for u in load_users().get("users", []) if u.get("id")]


def create_user(username: str, password: str) -> dict:
    """Create a user record + their data directory. Caller is responsible
    for verifying the username doesn't already exist."""
    username = username.strip().lower()
    if not _USERNAME_RE.match(username):
        raise ValueError(
            "username must be 1-32 chars, start with a letter, only "
            "letters/digits/dash/underscore"
        )
    if find_user_by_username(username):
        raise ValueError(f"user {username!r} already exists")
    user = {
        "id": username,
        "username": username,
        "password_hash": generate_password_hash(password),
        "created_at": _now_iso(),
    }
    state = load_users()
    state["users"].append(user)
    save_users(state)
    user_dir(username).mkdir(parents=True, exist_ok=True)
    return user


def set_password(user_id: str, password: str) -> bool:
    state = load_users()
    for u in state["users"]:
        if u.get("id") == user_id:
            u["password_hash"] = generate_password_hash(password)
            save_users(state)
            return True
    return False


def verify_password(username: str, password: str) -> dict | None:
    """Return the user record on success, None on failure. Constant-time
    via werkzeug's check_password_hash regardless of whether the user
    exists (we still call check against a sentinel hash to keep timing
    consistent)."""
    user = find_user_by_username(username)
    if user is None:
        # Equalize timing — check against a throwaway hash so a username
        # that doesn't exist takes about as long as a wrong password.
        check_password_hash(generate_password_hash("dummy"), password or "")
        return None
    if not check_password_hash(user.get("password_hash") or "", password or ""):
        return None
    return user


def delete_user(user_id: str) -> bool:
    state = load_users()
    before = len(state["users"])
    state["users"] = [u for u in state["users"] if u.get("id") != user_id]
    if len(state["users"]) == before:
        return False
    save_users(state)
    udir = user_dir(user_id)
    if udir.exists():
        shutil.rmtree(udir)
    return True


# ---------- prefs.json ----------


def default_prefs(display_name: str | None = None) -> dict:
    return {
        "display_name": display_name or "",
        "stake_1u": 5.0,
        "stake_2u": 10.0,
        "pushover_user_key": "",
        "rules_acknowledged_at": None,
    }


def load_prefs(user_id: str) -> dict:
    """Returns prefs dict if file exists, else None. Caller decides
    whether missing means 'show wizard' (web) or 'use defaults' (CLI)."""
    p = prefs_path(user_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_prefs(user_id: str, prefs: dict) -> None:
    p = prefs_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, indent=2) + "\n")


def has_completed_setup(user_id: str) -> bool:
    """User has finished the first-login wizard if both prefs.json and
    bankroll.json exist for them. Bankroll comes from the wizard, so its
    presence is the cleanest 'fully onboarded' marker."""
    return prefs_path(user_id).exists() and bankroll_path(user_id).exists()


# ---------- session ----------


def current_user_id() -> str | None:
    return session.get("user_id")


def login_required(view: Callable) -> Callable:
    """Decorator: 401 JSON if no session. Use on every per-user endpoint."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        uid = current_user_id()
        if not uid:
            return jsonify({"error": "auth required"}), 401
        # Re-check the user still exists (account may have been deleted
        # while their session was live).
        if get_user(uid) is None:
            session.clear()
            return jsonify({"error": "auth required"}), 401
        return view(*args, **kwargs)

    return wrapper


def login(user: dict) -> None:
    session.permanent = True
    session["user_id"] = user["id"]


def logout() -> None:
    session.clear()


# ---------- secret key ----------


def get_or_create_secret_key() -> str:
    """Returns FLASK_SECRET_KEY env var if set, else loads (or generates)
    a persistent secret in data/.flask_secret. Auto-persisting avoids the
    'sessions silently invalidate on every restart' footgun."""
    env = os.getenv("FLASK_SECRET_KEY")
    if env:
        return env
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key + "\n")
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return key


# ---------- migration from single-user layout ----------


LEGACY_BETS_PATH = DATA_DIR / "bets.json"
LEGACY_BANKROLL_PATH = DATA_DIR / "bankroll.json"
PRIMARY_USER_ID = "chad"


def migrate_legacy_if_needed() -> bool:
    """Move legacy single-user files into data/users/chad/ on first boot
    after the multi-user refactor. Idempotent. Returns True if any
    migration happened, False if nothing to do.

    Also creates the chad user record (no password) and a default prefs
    file using the values that were hardcoded before the refactor. The
    user still has to set a password via `python -m bets.auth set-password chad`.
    """
    did_anything = False
    chad_dir = user_dir(PRIMARY_USER_ID)
    chad_dir.mkdir(parents=True, exist_ok=True)

    # Move bets.json
    new_bets = bets_path(PRIMARY_USER_ID)
    if LEGACY_BETS_PATH.exists() and not new_bets.exists():
        shutil.move(str(LEGACY_BETS_PATH), str(new_bets))
        did_anything = True

    # Move bankroll.json
    new_bankroll = bankroll_path(PRIMARY_USER_ID)
    if LEGACY_BANKROLL_PATH.exists() and not new_bankroll.exists():
        shutil.move(str(LEGACY_BANKROLL_PATH), str(new_bankroll))
        did_anything = True

    # Seed prefs with the values that were hardcoded pre-refactor so chad
    # never sees the wizard.
    if not prefs_path(PRIMARY_USER_ID).exists():
        save_prefs(
            PRIMARY_USER_ID,
            {
                "display_name": "Chad",
                "stake_1u": 5.0,
                "stake_2u": 10.0,
                "pushover_user_key": os.getenv("PUSHOVER_USER", ""),
                "rules_acknowledged_at": _now_iso(),
            },
        )
        did_anything = True

    # Add chad to users.json with NO password — `set-password` CLI is
    # required before login works. We don't ship a default password.
    state = load_users()
    if not any(u.get("id") == PRIMARY_USER_ID for u in state["users"]):
        state["users"].append(
            {
                "id": PRIMARY_USER_ID,
                "username": PRIMARY_USER_ID,
                "password_hash": "",  # unset → verify_password always fails
                "created_at": _now_iso(),
            }
        )
        save_users(state)
        did_anything = True

    return did_anything


# ---------- CLI ----------


def _read_password_twice(prompt: str = "Password") -> str:
    pw1 = getpass.getpass(f"{prompt}: ")
    if not pw1:
        print("password cannot be empty", file=sys.stderr)
        sys.exit(2)
    pw2 = getpass.getpass(f"{prompt} (again): ")
    if pw1 != pw2:
        print("passwords do not match", file=sys.stderr)
        sys.exit(2)
    return pw1


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        users = load_users().get("users", [])
        if not users:
            print("(no users)")
            return
        for u in users:
            has_pw = "✓" if u.get("password_hash") else "(no password set)"
            print(f"  {u['id']:20s} {has_pw}")
        return

    if cmd == "add-user":
        if len(sys.argv) < 3:
            print("usage: python -m bets.auth add-user <username>", file=sys.stderr)
            sys.exit(2)
        username = sys.argv[2]
        if find_user_by_username(username):
            print(f"user {username!r} already exists", file=sys.stderr)
            sys.exit(1)
        password = _read_password_twice()
        user = create_user(username, password)
        print(f"created user {user['id']!r}")
        return

    if cmd == "set-password":
        if len(sys.argv) < 3:
            print("usage: python -m bets.auth set-password <username>", file=sys.stderr)
            sys.exit(2)
        username = sys.argv[2]
        existing = find_user_by_username(username)
        if existing is None:
            print(f"no user named {username!r}", file=sys.stderr)
            sys.exit(1)
        password = _read_password_twice()
        set_password(existing["id"], password)
        print(f"password updated for {existing['id']!r}")
        return

    if cmd == "delete-user":
        if len(sys.argv) < 3:
            print("usage: python -m bets.auth delete-user <username>", file=sys.stderr)
            sys.exit(2)
        username = sys.argv[2]
        existing = find_user_by_username(username)
        if existing is None:
            print(f"no user named {username!r}", file=sys.stderr)
            sys.exit(1)
        confirm = input(
            f"delete user {existing['id']!r} AND their data dir? (yes/no): "
        ).strip().lower()
        if confirm != "yes":
            print("cancelled")
            return
        delete_user(existing["id"])
        print(f"deleted {existing['id']!r}")
        return

    print(f"unknown command: {cmd}", file=sys.stderr)
    print(
        "commands: list | add-user <name> | set-password <name> | delete-user <name>",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
