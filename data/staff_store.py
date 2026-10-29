"""Storage for staff accounts and sessions. Crypto and policy live in api/staff_auth.py."""

import datetime as dt
import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import patient_db as db


def create_user(email: str, name: str, role: str, password_hash: str, totp_secret: str) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO staff_users (email, name, role, password_hash, totp_secret, created_at) VALUES (?,?,?,?,?,?)",
            (email, name, role, password_hash, totp_secret, dt.datetime.now(dt.timezone.utc).isoformat()))
        return cur.lastrowid


def get_user_by_email(email: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM staff_users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM staff_users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT user_id, email, name, role, active, created_at FROM staff_users ORDER BY user_id").fetchall()
        return [dict(r) for r in rows]


def count_users() -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM staff_users WHERE active = 1").fetchone()["c"]


def count_active_admins() -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM staff_users WHERE active = 1 AND role = 'admin'").fetchone()["c"]


def set_active(user_id: int, active: bool) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE staff_users SET active = ? WHERE user_id = ?", (int(active), user_id))


def record_login_failure(user_id: int, lock_after: int, lock_until: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE staff_users SET failed_logins = failed_logins + 1 WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE staff_users SET locked_until = ?, failed_logins = 0 "
                     "WHERE user_id = ? AND failed_logins >= ?", (lock_until, user_id, lock_after))


def record_login_success(user_id: int, totp_step: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE staff_users SET failed_logins = 0, locked_until = 0, last_totp_step = ? WHERE user_id = ?",
                     (totp_step, user_id))


def create_session(token_hash: str, session_id: str, user_id: Optional[int], name: str, role: str,
                   demo: bool, now: int, expires_at: int) -> None:
    with db.get_conn() as conn:
        conn.execute("INSERT INTO staff_sessions (token_hash, session_id, user_id, name, role, demo, created_at, "
                     "last_seen, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (token_hash, session_id, user_id, name, role, int(demo), now, now, expires_at))


def get_session(token_hash: str) -> Optional[dict]:
    """Session joined with its account, so a role change or deactivation applies at once."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT s.*, u.role AS user_role, u.name AS user_name, u.active AS user_active "
            "FROM staff_sessions s LEFT JOIN staff_users u ON u.user_id = s.user_id WHERE s.token_hash = ?",
            (token_hash,)).fetchone()
        return dict(row) if row else None


def touch_session(token_hash: str, now: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE staff_sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))


def revoke_session(token_hash: str, now: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE staff_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (now, token_hash))


def revoke_user_sessions(user_id: int, now: int) -> int:
    with db.get_conn() as conn:
        return conn.execute("UPDATE staff_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                            (now, user_id)).rowcount


def purge_dead_sessions(older_than: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM staff_sessions WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)",
                     (older_than, older_than))
