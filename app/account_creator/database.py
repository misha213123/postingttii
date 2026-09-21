from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.account_creator.models import Account, CreationJob, SocialAccount

PLATFORMS = ("tiktok", "instagram", "youtube")
DB_PATH = settings.data_dir / "account_manager.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_number INTEGER NOT NULL UNIQUE,
                email TEXT UNIQUE,
                email_alias_id TEXT,
                display_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                password_encrypted BLOB,
                bio TEXT NOT NULL DEFAULT '',
                avatar_path TEXT NOT NULL DEFAULT '',
                browser_profile_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'CREATED',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                profile_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'NOT_CREATED',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, platform),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS creation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                step TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'WAITING',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, platform),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_social_accounts_account_id
                ON social_accounts(account_id);
            CREATE INDEX IF NOT EXISTS idx_creation_jobs_account_id
                ON creation_jobs(account_id);
            """
        )


def _next_account_number(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(account_number), 0) + 1 AS n FROM accounts").fetchone()
    return int(row["n"])


def _account_payload(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        return None

    account = Account.from_row(row).to_dict()
    social_rows = conn.execute(
        "SELECT * FROM social_accounts WHERE account_id = ? ORDER BY platform",
        (account_id,),
    ).fetchall()
    job_rows = conn.execute(
        "SELECT * FROM creation_jobs WHERE account_id = ? ORDER BY platform",
        (account_id,),
    ).fetchall()

    social = {item.platform: item.to_dict() for item in map(SocialAccount.from_row, social_rows)}
    jobs = {item.platform: item.to_dict() for item in map(CreationJob.from_row, job_rows)}

    for platform in PLATFORMS:
        social.setdefault(
            platform,
            {
                "account_id": account_id,
                "platform": platform,
                "username": "",
                "profile_url": "",
                "status": "NOT_CREATED",
            },
        )

    account["social_accounts"] = social
    account["creation_jobs"] = jobs
    account["profile_ready"] = bool(account.get("display_name") and account.get("username") and account.get("avatar_path"))
    return account


def list_accounts() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        ids = conn.execute("SELECT id FROM accounts ORDER BY account_number").fetchall()
        return [payload for row in ids if (payload := _account_payload(conn, int(row["id"]))) is not None]


def get_account(account_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        return _account_payload(conn, account_id)


def create_account(
    *,
    email: str = "",
    display_name: str = "",
    username: str = "",
    bio: str = "",
) -> dict[str, Any]:
    init_db()
    now = _now()

    with _connect() as conn:
        number = _next_account_number(conn)
        profile_path = settings.data_dir / "browser_profiles" / f"account_{number:02d}"
        profile_value = str(profile_path)
        cursor = conn.execute(
            """
            INSERT INTO accounts (
                account_number, email, email_alias_id, display_name, username,
                password_encrypted, bio, avatar_path, browser_profile_path,
                status, created_at, updated_at
            ) VALUES (?, ?, '', ?, ?, NULL, ?, '', ?, 'CREATED', ?, ?)
            """,
            (
                number,
                email.strip() or None,
                display_name.strip(),
                username.strip(),
                bio.strip(),
                profile_value,
                now,
                now,
            ),
        )
        account_id = int(cursor.lastrowid)

        for platform in PLATFORMS:
            conn.execute(
                """
                INSERT INTO social_accounts (
                    account_id, platform, username, profile_url, status, created_at, updated_at
                ) VALUES (?, ?, '', '', 'NOT_CREATED', ?, ?)
                """,
                (account_id, platform, now, now),
            )
            conn.execute(
                """
                INSERT INTO creation_jobs (
                    account_id, platform, step, status, error, created_at, updated_at
                ) VALUES (?, ?, '', 'WAITING', '', ?, ?)
                """,
                (account_id, platform, now, now),
            )

        # Create only the safe local container folders here. Playwright will own
        # their contents later in Phase 6.
        (settings.data_dir / "accounts" / str(account_id)).mkdir(parents=True, exist_ok=True)
        profile_path.mkdir(parents=True, exist_ok=True)

        return _account_payload(conn, account_id)  # type: ignore[return-value]


def update_account(account_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    init_db()
    allowed = {"email", "display_name", "username", "bio"}
    updates: list[str] = []
    params: list[Any] = []

    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if isinstance(value, str):
            value = value.strip()
        if key == "email" and not value:
            value = None
        updates.append(f"{key} = ?")
        params.append(value)

    if not updates:
        return get_account(account_id)

    updates.append("updated_at = ?")
    params.append(_now())
    params.append(account_id)

    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not exists:
            return None
        conn.execute(
            f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        return _account_payload(conn, account_id)


def delete_account(account_id: int) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT account_number FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return True


def account_counts() -> dict[str, int]:
    init_db()
    with _connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
        ready = int(conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'READY'").fetchone()[0])
        action_required = int(
            conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE status IN ('NEED_ACTION', 'ACTION_REQUIRED')"
            ).fetchone()[0]
        )
        creating = int(
            conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE status NOT IN ('READY', 'NEED_ACTION', 'ACTION_REQUIRED', 'FAILED')"
            ).fetchone()[0]
        )
    return {
        "total": total,
        "ready": ready,
        "action_required": action_required,
        "creating": creating,
    }


init_db()


def alias_assignments() -> dict[str, dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, account_number, email, email_alias_id, display_name, username, status
            FROM accounts
            WHERE email_alias_id IS NOT NULL AND email_alias_id != ''
            ORDER BY account_number
            """
        ).fetchall()

    return {
        str(row["email_alias_id"]): {
            "account_id": int(row["id"]),
            "account_number": int(row["account_number"]),
            "email": row["email"] or "",
            "display_name": row["display_name"] or "",
            "username": row["username"] or "",
            "status": row["status"] or "CREATED",
        }
        for row in rows
    }


def assign_alias(account_id: int, alias_id: str, email: str) -> dict[str, Any] | None:
    init_db()
    alias_id = alias_id.strip()
    email = email.strip()
    if not alias_id or not email:
        raise ValueError("alias_id и email обязательны")

    now = _now()
    with _connect() as conn:
        target = conn.execute(
            "SELECT id FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not target:
            return None

        existing = conn.execute(
            """
            SELECT id, account_number
            FROM accounts
            WHERE email_alias_id = ? AND id != ?
            """,
            (alias_id, account_id),
        ).fetchone()
        if existing:
            raise ValueError(
                f"Alias уже назначен Account {int(existing['account_number']):02d}"
            )

        conn.execute(
            """
            UPDATE accounts
            SET email = ?, email_alias_id = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (email, alias_id, "EMAIL_READY", now, account_id),
        )
        return _account_payload(conn, account_id)


def unassign_alias(account_id: int) -> dict[str, Any] | None:
    init_db()
    now = _now()
    with _connect() as conn:
        target = conn.execute(
            "SELECT id FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not target:
            return None

        conn.execute(
            """
            UPDATE accounts
            SET email = NULL, email_alias_id = '', status = ?, updated_at = ?
            WHERE id = ?
            """,
            ("CREATED", now, account_id),
        )
        return _account_payload(conn, account_id)


def username_exists(username: str, *, exclude_account_id: int | None = None) -> bool:
    init_db()
    username = username.strip().lower()
    if not username:
        return False

    with _connect() as conn:
        if exclude_account_id is None:
            row = conn.execute(
                "SELECT 1 FROM accounts WHERE lower(username) = ? LIMIT 1",
                (username,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM accounts WHERE lower(username) = ? AND id != ? LIMIT 1",
                (username, exclude_account_id),
            ).fetchone()
    return bool(row)


def save_creator_profile(
    account_id: int,
    *,
    display_name: str,
    username: str,
    bio: str,
    avatar_path: str,
) -> dict[str, Any] | None:
    init_db()
    now = _now()

    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not row:
            return None

        if not row["email"]:
            raise ValueError("Сначала назначь email alias")

        duplicate = conn.execute(
            "SELECT id FROM accounts WHERE lower(username) = lower(?) AND id != ? LIMIT 1",
            (username.strip(), account_id),
        ).fetchone()
        if duplicate:
            raise ValueError("Такой username уже используется в Account Manager")

        conn.execute(
            """
            UPDATE accounts
            SET display_name = ?, username = ?, bio = ?, avatar_path = ?,
                status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                display_name.strip(),
                username.strip(),
                bio.strip(),
                avatar_path,
                "PROFILE_READY",
                now,
                account_id,
            ),
        )
        return _account_payload(conn, account_id)
