from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any

from config import DATABASE_PATH, ROLE_ADMIN, ROLE_OWNER, TARIFFS, settings
from remnawave import RemnawaveClient, RemnawaveError, is_remnawave_configured
from vpn import build_config, generate_access_key


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def format_invoice_code(invoice_seq: int | None) -> str | None:
    if not invoice_seq:
        return None
    return f"{settings.payment_invoice_prefix}={invoice_seq:02d}"


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance INTEGER DEFAULT 0,
            role INTEGER DEFAULT 1,
            subscription_until TEXT,
            expiry_notice_for TEXT,
            is_banned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            referred_by INTEGER
        )
        """
    )

    columns = _column_names(conn, "users")
    migrations = {
        "balance": "ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0",
        "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT",
        "last_name": "ALTER TABLE users ADD COLUMN last_name TEXT",
        "role": "ALTER TABLE users ADD COLUMN role INTEGER DEFAULT 1",
        "subscription_until": "ALTER TABLE users ADD COLUMN subscription_until TEXT",
        "expiry_notice_for": "ALTER TABLE users ADD COLUMN expiry_notice_for TEXT",
        "is_banned": "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "created_at": "ALTER TABLE users ADD COLUMN created_at TEXT",
        "referred_by": "ALTER TABLE users ADD COLUMN referred_by INTEGER",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
    conn.execute(
        "UPDATE users SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
    )


def _ensure_promos_table(conn: sqlite3.Connection) -> None:
    target_name = "promos"
    if _table_exists(conn, "promo") and not _table_exists(conn, target_name):
        conn.execute("ALTER TABLE promo RENAME TO promos")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            value INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    columns = _column_names(conn, "promos")
    if "created_at" not in columns:
        conn.execute("ALTER TABLE promos ADD COLUMN created_at TEXT")
    conn.execute(
        "UPDATE promos SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
    )


def _ensure_payments_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            invoice_seq INTEGER,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            tariff_code TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT,
            reviewed_by INTEGER,
            reviewed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """
    )
    columns = _column_names(conn, "payments")
    if "invoice_seq" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN invoice_seq INTEGER")
    if "access_sent_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN access_sent_at TEXT")
    if "reviewed_by" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN reviewed_by INTEGER")
    if "reviewed_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN reviewed_at TEXT")

    rows = conn.execute(
        """
        SELECT id
        FROM payments
        WHERE invoice_seq IS NULL
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    if rows:
        current_max = conn.execute(
            "SELECT COALESCE(MAX(invoice_seq), 0) AS max_seq FROM payments"
        ).fetchone()["max_seq"]
        next_seq = int(current_max or 0) + 1
        for row in rows:
            conn.execute(
                "UPDATE payments SET invoice_seq = ? WHERE id = ?",
                (next_seq, row["id"]),
            )
            next_seq += 1


def _ensure_vpn_keys_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vpn_keys (
            user_id INTEGER PRIMARY KEY,
            vpn_key TEXT NOT NULL,
            config_text TEXT NOT NULL,
            expire_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """
    )


def _ensure_referrals_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,
            rewarded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(referrer_id) REFERENCES users(user_id),
            FOREIGN KEY(referred_id) REFERENCES users(user_id)
        )
        """
    )


def init_db() -> None:
    with closing(connect()) as conn:
        _ensure_users_table(conn)
        _ensure_promos_table(conn)
        _ensure_payments_table(conn)
        _ensure_vpn_keys_table(conn)
        _ensure_referrals_table(conn)
        conn.commit()

    if settings.owner_id:
        ensure_owner(settings.owner_id)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def payment_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    result = row_to_dict(row)
    if not result:
        return None
    result["invoice_code"] = format_invoice_code(result.get("invoice_seq"))
    return result


def ensure_owner(user_id: int) -> None:
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, role)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role=excluded.role
            """,
            (user_id, "owner", ROLE_OWNER),
        )
        conn.execute(
            "UPDATE users SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()


def add_user(
    user_id: int,
    username: str | None,
    referred_by: int | None = None,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
) -> None:
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, referred_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=COALESCE(excluded.first_name, users.first_name),
                last_name=COALESCE(excluded.last_name, users.last_name)
            """,
            (user_id, username, first_name, last_name, referred_by),
        )
        conn.execute(
            "UPDATE users SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()

    # Записываем реферала если он ещё не зарегистрирован
    if referred_by and referred_by != user_id:
        with closing(connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO referrals (referrer_id, referred_id)
                VALUES (?, ?)
                """,
                (referred_by, user_id),
            )
            conn.commit()


def get_user(user_id: int) -> dict[str, Any]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return dict(row)
    return {
        "user_id": user_id,
        "username": None,
        "first_name": None,
        "last_name": None,
        "balance": 0,
        "role": 1,
        "subscription_until": None,
        "expiry_notice_for": None,
        "is_banned": 0,
        "created_at": None,
        "referred_by": None,
    }


def list_users(limit: int = 20) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC, user_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_admin_ids() -> list[int]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE role >= ?
            ORDER BY role DESC, user_id ASC
            """,
            (ROLE_ADMIN,),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def get_balance(user_id: int) -> int:
    return int(get_user(user_id)["balance"] or 0)


def get_role(user_id: int) -> int:
    return int(get_user(user_id)["role"] or 1)


def is_banned(user_id: int) -> bool:
    return bool(get_user(user_id)["is_banned"])


def set_role(user_id: int, role: int) -> None:
    with closing(connect()) as conn:
        conn.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))
        conn.commit()


def set_banned(user_id: int, banned: bool) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE users SET is_banned=? WHERE user_id=?",
            (1 if banned else 0, user_id),
        )
        conn.commit()


def update_balance(user_id: int, amount: int) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


def create_promo(code: str, value: int) -> None:
    normalized_code = code.strip().upper()
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO promos (code, value)
            VALUES (?, ?)
            ON CONFLICT(code) DO UPDATE SET value=excluded.value
            """,
            (normalized_code, value),
        )
        conn.commit()


def list_promos(limit: int = 20) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM promos ORDER BY created_at DESC, code ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def use_promo(user_id: int, code: str) -> dict[str, Any] | None:
    normalized_code = code.strip().upper()
    with closing(connect()) as conn:
        row = conn.execute("SELECT value FROM promos WHERE code = ?", (normalized_code,)).fetchone()
        if not row:
            return None

    days = int(row["value"])
    activation = activate_subscription_days(user_id, days, f"promo-{normalized_code.lower()}")
    with closing(connect()) as conn:
        conn.execute("DELETE FROM promos WHERE code = ?", (normalized_code,))
        conn.commit()
    return {
        "days": days,
        "subscription_until": activation["subscription_until"],
        "config_text": activation["config_text"],
    }


def get_stats() -> dict[str, int]:
    with closing(connect()) as conn:
        users = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        total_balance = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) AS sum_balance FROM users"
        ).fetchone()["sum_balance"]
        paid_payments = conn.execute(
            "SELECT COUNT(*) AS count FROM payments WHERE status='paid'"
        ).fetchone()["count"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS revenue FROM payments WHERE status='paid'"
        ).fetchone()["revenue"]
        active_subscriptions = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            WHERE subscription_until IS NOT NULL AND subscription_until > CURRENT_TIMESTAMP
            """
        ).fetchone()["count"]

    return {
        "users": int(users),
        "total_balance": int(total_balance),
        "paid_payments": int(paid_payments),
        "revenue": int(revenue),
        "active_subscriptions": int(active_subscriptions),
    }


def create_payment(
    payment_id: str,
    user_id: int,
    amount: int,
    tariff_code: str,
    provider: str,
    payment_url: str,
) -> int:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(invoice_seq), 0) + 1 AS next_seq FROM payments"
        ).fetchone()
        invoice_seq = int(row["next_seq"] or 1)
        conn.execute(
            """
            INSERT INTO payments (
                id, invoice_seq, user_id, amount, tariff_code, provider, payment_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payment_id, invoice_seq, user_id, amount, tariff_code, provider, payment_url),
        )
        conn.commit()
    return invoice_seq


def get_payment(payment_id: str) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return payment_row_to_dict(row)


def list_recent_payments(limit: int = 20) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM payments ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [payment_row_to_dict(row) for row in rows]


def list_user_payments(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM payments
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [payment_row_to_dict(row) for row in rows]


def save_vpn_key(user_id: int, vpn_key: str, config_text: str, expire_at: str | None) -> None:
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO vpn_keys (user_id, vpn_key, config_text, expire_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                vpn_key=excluded.vpn_key,
                config_text=excluded.config_text,
                expire_at=excluded.expire_at
            """,
            (user_id, vpn_key, config_text, expire_at),
        )
        conn.commit()


def get_vpn_key(user_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM vpn_keys WHERE user_id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


def clear_vpn_key(user_id: int) -> None:
    with closing(connect()) as conn:
        conn.execute("DELETE FROM vpn_keys WHERE user_id = ?", (user_id,))
        conn.commit()


def _set_subscription_until(user_id: int, subscription_until: str | None) -> None:
    with closing(connect()) as conn:
        conn.execute(
            """
            UPDATE users
            SET subscription_until = ?,
                expiry_notice_for = NULL
            WHERE user_id = ?
            """,
            (subscription_until, user_id),
        )
        conn.commit()


def _extend_subscription(current_value: str | None, duration_days: int) -> str:
    now = datetime.utcnow()
    current_dt = None
    if current_value:
        try:
            current_dt = datetime.fromisoformat(current_value)
        except ValueError:
            current_dt = None
    base_dt = current_dt if current_dt and current_dt > now else now
    return (base_dt + timedelta(days=duration_days)).replace(microsecond=0).isoformat(sep=" ")


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def activate_subscription_days(user_id: int, duration_days: int, source_code: str) -> dict[str, Any]:
    user = get_user(user_id)
    existing_vpn_key = get_vpn_key(user_id)
    expire_at = _extend_subscription(user.get("subscription_until"), duration_days)
    telegram_username = user.get("username")
    first_name = user.get("first_name")
    last_name = user.get("last_name")

    if is_remnawave_configured():
        client = RemnawaveClient()
        try:
            access = None
            remote_user_uuid = str(existing_vpn_key.get("vpn_key") or "") if existing_vpn_key else ""
            if remote_user_uuid and _looks_like_uuid(remote_user_uuid):
                try:
                    access = client.update_user(
                        remote_user_uuid,
                        expire_at=expire_at,
                        user_id=user_id,
                        tariff_code=source_code,
                        telegram_username=telegram_username,
                        first_name=first_name,
                        last_name=last_name,
                    )
                except RemnawaveError:
                    access = None

            if access is None:
                remote_user = client.find_user_by_telegram_id(user_id)
                if remote_user:
                    access = client.update_user(
                        str(remote_user["uuid"]),
                        expire_at=expire_at,
                        user_id=user_id,
                        tariff_code=source_code,
                        telegram_username=telegram_username,
                        first_name=first_name,
                        last_name=last_name,
                    )
                else:
                    access = client.add_user(
                        user_id=user_id,
                        tariff_code=source_code,
                        expire_at=expire_at,
                        telegram_username=telegram_username,
                        first_name=first_name,
                        last_name=last_name,
                    )
        finally:
            client.close()

        access_key = access.remote_user_uuid
        config_text = access.subscription_url
    else:
        access_key = generate_access_key()
        config_text = build_config(user_id, expire_at)

    _set_subscription_until(user_id, expire_at)
    save_vpn_key(user_id, access_key, config_text, expire_at)

    return {
        "subscription_until": expire_at,
        "vpn_key": access_key,
        "config_text": config_text,
    }


def activate_subscription(user_id: int, tariff_code: str) -> dict[str, Any]:
    tariff = TARIFFS[tariff_code]
    return activate_subscription_days(user_id, tariff["duration_days"], tariff_code)


def reset_subscription(user_id: int) -> dict[str, Any]:
    vpn_key = get_vpn_key(user_id)
    removed_remote = False
    remote_user_uuid = str(vpn_key.get("vpn_key") or "") if vpn_key else ""

    if is_remnawave_configured():
        client = RemnawaveClient()
        try:
            targets: list[str] = []
            if remote_user_uuid and _looks_like_uuid(remote_user_uuid):
                targets.append(remote_user_uuid)
            else:
                remote_user = client.find_user_by_telegram_id(user_id)
                if remote_user:
                    targets.append(str(remote_user["uuid"]))

            for target_uuid in targets:
                removed_remote = client.delete_user(target_uuid) or removed_remote
        finally:
            client.close()

    clear_vpn_key(user_id)
    _set_subscription_until(user_id, None)

    return {
        "user": get_user(user_id),
        "removed_remote": removed_remote,
    }


def mark_payment_paid(payment_id: str, reviewed_by: int | None = None) -> dict[str, Any] | None:
    payment = get_payment(payment_id)
    if not payment:
        return None
    if payment["status"] == "paid":
        vpn_key = get_vpn_key(payment["user_id"])
        user = get_user(payment["user_id"])
        return {
            "payment": payment,
            "user": user,
            "vpn_key": vpn_key,
        }

    result = activate_subscription(payment["user_id"], payment["tariff_code"])
    with closing(connect()) as conn:
        conn.execute(
            """
            UPDATE payments
            SET status='paid',
                paid_at=CURRENT_TIMESTAMP,
                reviewed_at=CURRENT_TIMESTAMP,
                reviewed_by=COALESCE(?, reviewed_by)
            WHERE id=?
            """,
            (reviewed_by, payment_id),
        )
        conn.commit()

    # Начисляем бонус рефереру (+7 дней) если ещё не начисляли
    _reward_referrer(payment["user_id"])

    return {
        "payment": get_payment(payment_id),
        "user": get_user(payment["user_id"]),
        "vpn_key": get_vpn_key(payment["user_id"]),
        "activation": result,
    }


def mark_payment_failed(payment_id: str, reviewed_by: int | None = None) -> dict[str, Any] | None:
    payment = get_payment(payment_id)
    if not payment:
        return None

    with closing(connect()) as conn:
        conn.execute(
            """
            UPDATE payments
            SET status='failed',
                reviewed_at=CURRENT_TIMESTAMP,
                reviewed_by=COALESCE(?, reviewed_by)
            WHERE id=?
            """,
            (reviewed_by, payment_id),
        )
        conn.commit()

    return get_payment(payment_id)


def _reward_referrer(paid_user_id: int) -> None:
    """Начисляет рефереру +7 дней подписки за первую оплату приглашённого."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM referrals WHERE referred_id = ? AND rewarded = 0",
            (paid_user_id,),
        ).fetchone()
        if not row:
            return

        referrer_id = int(row["referrer_id"])

    activate_subscription_days(referrer_id, 3, "referral")

    with closing(connect()) as conn:
        conn.execute(
            "UPDATE referrals SET rewarded = 1 WHERE referred_id = ?",
            (paid_user_id,),
        )
        conn.commit()


def get_referral_stats(user_id: int) -> dict[str, Any]:
    """Возвращает статистику рефералов пользователя."""
    with closing(connect()) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM referrals WHERE referrer_id = ?",
            (user_id,),
        ).fetchone()["count"]
        rewarded = conn.execute(
            "SELECT COUNT(*) AS count FROM referrals WHERE referrer_id = ? AND rewarded = 1",
            (user_id,),
        ).fetchone()["count"]
    return {"total": int(total), "rewarded": int(rewarded)}


def list_users_expiring_soon(within_hours: int = 24) -> list[dict[str, Any]]:
    now = datetime.utcnow().replace(microsecond=0)
    upper_bound = now + timedelta(hours=within_hours)
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM users
            WHERE subscription_until IS NOT NULL
              AND subscription_until > ?
              AND subscription_until <= ?
              AND (expiry_notice_for IS NULL OR expiry_notice_for != subscription_until)
            ORDER BY subscription_until ASC
            """,
            (now.isoformat(sep=" "), upper_bound.isoformat(sep=" ")),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_expiry_notice_sent(user_id: int, subscription_until: str) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE users SET expiry_notice_for = ? WHERE user_id = ?",
            (subscription_until, user_id),
        )
        conn.commit()


def mark_payment_access_sent(payment_id: str) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE payments SET access_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (payment_id,),
        )
        conn.commit()

