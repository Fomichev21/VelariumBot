from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "db.sqlite3"


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    try:
        content = env_path.read_text(encoding="utf-8")
    except Exception:
        return

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(('"', "'")):
            value = value[1:-1]
        os.environ[key] = value


ROLE_USER = 1
ROLE_ADMIN = 2
ROLE_OWNER = 3

PAYMENT_STATUSES = {
    "pending": "Ожидает оплату",
    "paid": "Оплачен",
    "failed": "Ошибка",
}

TARIFFS = {
    "month": {
        "title": "1 месяц",
        "price": 99,
        "duration_days": 30,
        "description": "Оптимально, чтобы попробовать сервис",
    },
    "quarter": {
        "title": "3 месяца",
        "price": 249,
        "duration_days": 90,
        "description": "Самый популярный вариант",
    },
    "year": {
        "title": "12 месяцев",
        "price": 799,
        "duration_days": 365,
        "description": "Максимально выгодный тариф",
    },
}


def _to_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _to_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "8646213810:AAFBgdufxXg-gTFbQBaLEY8i9JZkkv7gz6I")
    telegram_proxy: str = os.getenv("TELEGRAM_PROXY", "").strip()
    owner_id: int = _to_int("OWNER_ID", 1779714149)
    support_username: str = os.getenv("SUPPORT_USERNAME", "Fomamai")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "manual_sbp")
    manual_payment_url: str = os.getenv(
        "MANUAL_PAYMENT_URL",
        "https://finance.ozon.ru/apps/sbp/ozonbankpay/019d979e-fb01-71ef-8625-7f52d022d8ea",
    ).strip()
    payment_invoice_prefix: str = os.getenv("PAYMENT_INVOICE_PREFIX", "AT").strip() or "AT"
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "change-me-in-production")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin123")
    admin_password_hash: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    web_host: str = os.getenv("WEB_HOST", "127.0.0.1")
    web_port: int = _to_int("WEB_PORT", 5000)

    wireguard_endpoint: str = os.getenv("WG_ENDPOINT", "vpn.example.com:51820")
    wireguard_public_key: str = os.getenv("WG_SERVER_PUBLIC_KEY", "SET_REAL_PUBLIC_KEY")
    wireguard_dns: str = os.getenv("WG_DNS", "1.1.1.1")
    wireguard_allowed_ips: str = os.getenv("WG_ALLOWED_IPS", "0.0.0.0/0, ::/0")
    wireguard_prefix: str = os.getenv("WG_CLIENT_PREFIX", "10.8.0.")
    wireguard_interface: str = os.getenv("WG_INTERFACE", "wg0")

    remnawave_base_url: str = os.getenv("REMNAWAVE_BASE_URL", "").rstrip("/")
    remnawave_token: str = os.getenv("REMNAWAVE_TOKEN", "").strip()
    remnawave_sub_public_url: str = os.getenv("REMNAWAVE_SUB_PUBLIC_URL", "").rstrip("/")
    remnawave_squad_uuid: str = os.getenv("REMNAWAVE_SQUAD_UUID", "").strip()
    remnawave_squad_name: str = os.getenv("REMNAWAVE_SQUAD_NAME", "").strip()
    remnawave_user_prefix: str = os.getenv("REMNAWAVE_USER_PREFIX", "vpn").strip() or "vpn"
    remnawave_traffic_limit_bytes: int = _to_int("REMNAWAVE_TRAFFIC_LIMIT_BYTES", 0)
    remnawave_traffic_limit_strategy: str = (
        os.getenv("REMNAWAVE_TRAFFIC_LIMIT_STRATEGY", "NO_RESET").strip().upper() or "NO_RESET"
    )
    remnawave_hwid_device_limit: int | None = _to_optional_int("REMNAWAVE_HWID_DEVICE_LIMIT")

    threexui_base_url: str = os.getenv("THREEXUI_BASE_URL", "").rstrip("/")
    threexui_username: str = os.getenv("THREEXUI_USERNAME", "admin")
    threexui_password: str = os.getenv("THREEXUI_PASSWORD", "")
    threexui_inbound_id: int = _to_int("THREEXUI_INBOUND_ID")
    threexui_inbound_remark: str = os.getenv("THREEXUI_INBOUND_REMARK", "")
    threexui_sub_base_url: str = os.getenv("THREEXUI_SUB_BASE_URL", "").rstrip("/")
    threexui_sub_path: str = os.getenv("THREEXUI_SUB_PATH", "sub").strip("/")
    threexui_client_prefix: str = os.getenv("THREEXUI_CLIENT_PREFIX", "vpn")
    threexui_flow: str = os.getenv("THREEXUI_FLOW", "")
    threexui_limit_ip: int = _to_int("THREEXUI_LIMIT_IP")
    threexui_total_gb: int = _to_int("THREEXUI_TOTAL_GB")


settings = Settings()


def is_admin(role: int) -> bool:
    return role >= ROLE_ADMIN


def is_owner(role: int) -> bool:
    return role >= ROLE_OWNER
