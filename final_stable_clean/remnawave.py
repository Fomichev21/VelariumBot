from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from config import settings


class RemnawaveError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemnawaveAccess:
    remote_user_uuid: str
    username: str
    short_uuid: str
    subscription_url: str
    expire_at: str


def is_remnawave_configured() -> bool:
    return bool(settings.remnawave_base_url and settings.remnawave_token)


def get_missing_remnawave_settings() -> list[str]:
    missing: list[str] = []
    if not settings.remnawave_base_url:
        missing.append("REMNAWAVE_BASE_URL")
    if not settings.remnawave_token:
        missing.append("REMNAWAVE_TOKEN")
    if not settings.remnawave_squad_uuid and not settings.remnawave_squad_name:
        missing.append("REMNAWAVE_SQUAD_UUID or REMNAWAVE_SQUAD_NAME")
    return missing


class RemnawaveClient:
    def __init__(self) -> None:
        if not is_remnawave_configured():
            raise RemnawaveError("Remnawave credentials are not configured.")

        self.base_url = settings.remnawave_base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.remnawave_token}",
            }
        )

    def close(self) -> None:
        self.session.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs: Any,
    ) -> dict | list | str:
        response = self.session.request(
            method,
            f"{self.api_base}{path}",
            timeout=30,
            **kwargs,
        )

        if not response.ok:
            detail = response.text.strip()
            raise RemnawaveError(
                f"Remnawave request failed for {path}: {response.status_code} {detail[:300]}"
            )

        if not expect_json:
            return response.text

        body = response.text.strip()
        if not body:
            return {}

        try:
            data = response.json()
        except ValueError as exc:
            raise RemnawaveError(
                f"Remnawave returned non-JSON response for {path}."
            ) from exc

        if isinstance(data, dict) and "response" in data:
            return data["response"]
        return data

    def list_internal_squads(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/internal-squads")
        if isinstance(data, dict):
            squads = data.get("internalSquads") or data.get("internal_squads") or []
            return squads if isinstance(squads, list) else []
        return data if isinstance(data, list) else []

    def resolve_squad_uuid(self) -> str:
        if settings.remnawave_squad_uuid:
            return settings.remnawave_squad_uuid

        squads = self.list_internal_squads()
        if not squads:
            raise RemnawaveError("No internal squads were returned by Remnawave.")

        target_name = settings.remnawave_squad_name.strip().lower()
        if target_name:
            for squad in squads:
                name = str(squad.get("name") or "").strip().lower()
                if name == target_name:
                    return str(squad["uuid"])

        return str(squads[0]["uuid"])

    def get_user_by_uuid(self, remote_user_uuid: str) -> dict[str, Any]:
        data = self._request("GET", f"/users/{remote_user_uuid}")
        if isinstance(data, dict):
            return data
        raise RemnawaveError(f"User {remote_user_uuid} was not found in Remnawave.")

    def get_users_by_telegram_id(self, telegram_id: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/users/by-telegram-id/{telegram_id}")
        return data if isinstance(data, list) else []

    def find_user_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        users = self.get_users_by_telegram_id(telegram_id)
        if not users:
            return None

        users.sort(
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )
        return users[0]

    def add_user(
        self,
        *,
        user_id: int,
        tariff_code: str,
        expire_at: str,
        telegram_username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> RemnawaveAccess:
        payload: dict[str, Any] = {
            "username": build_panel_username(
                user_id=user_id,
                telegram_username=telegram_username,
                first_name=first_name,
                last_name=last_name,
            ),
            "expireAt": to_api_datetime(expire_at),
            "status": "ACTIVE",
            "trafficLimitStrategy": settings.remnawave_traffic_limit_strategy,
            "trafficLimitBytes": settings.remnawave_traffic_limit_bytes,
            "telegramId": user_id,
            "description": build_description(
                user_id=user_id,
                tariff_code=tariff_code,
                telegram_username=telegram_username,
                first_name=first_name,
                last_name=last_name,
            ),
            "activeInternalSquads": [self.resolve_squad_uuid()],
        }
        if settings.remnawave_hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = settings.remnawave_hwid_device_limit

        data = self._request("POST", "/users", json=payload)
        if not isinstance(data, dict):
            raise RemnawaveError("Remnawave create user returned an invalid payload.")
        return user_to_access(data)

    def update_user(
        self,
        remote_user_uuid: str,
        *,
        expire_at: str,
        user_id: int,
        tariff_code: str,
        telegram_username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> RemnawaveAccess:
        payload: dict[str, Any] = {
            "uuid": remote_user_uuid,
            "username": build_panel_username(
                user_id=user_id,
                telegram_username=telegram_username,
                first_name=first_name,
                last_name=last_name,
            ),
            "expireAt": to_api_datetime(expire_at),
            "status": "ACTIVE",
            "telegramId": user_id,
            "description": build_description(
                user_id=user_id,
                tariff_code=tariff_code,
                telegram_username=telegram_username,
                first_name=first_name,
                last_name=last_name,
            ),
            "activeInternalSquads": [self.resolve_squad_uuid()],
            "trafficLimitStrategy": settings.remnawave_traffic_limit_strategy,
            "trafficLimitBytes": settings.remnawave_traffic_limit_bytes,
        }
        if settings.remnawave_hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = settings.remnawave_hwid_device_limit

        data = self._request("PATCH", "/users", json=payload)
        if not isinstance(data, dict):
            raise RemnawaveError("Remnawave update user returned an invalid payload.")
        return user_to_access(data)

    def delete_user(self, remote_user_uuid: str) -> bool:
        data = self._request("DELETE", f"/users/{remote_user_uuid}")
        if isinstance(data, dict):
            return bool(data.get("isDeleted"))
        return False


def build_panel_username(
    *,
    user_id: int,
    telegram_username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    candidates = [
        telegram_username,
        " ".join(part for part in [first_name, last_name] if part),
        first_name,
        last_name,
    ]
    for candidate in candidates:
        cleaned = sanitize_username(candidate or "")
        if len(cleaned) >= 3:
            return cleaned[:36]

    suffix = uuid.uuid4().hex[:6]
    return f"{settings.remnawave_user_prefix}_{user_id}_{suffix}"[:36]


def sanitize_username(value: str) -> str:
    value = value.strip().lstrip("@")
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_-")
    return value


def build_description(
    *,
    user_id: int,
    tariff_code: str,
    telegram_username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    name = " ".join(part for part in [first_name, last_name] if part).strip()
    tg_username = (telegram_username or "").strip()
    bits = [f"telegram:{user_id}", f"tariff:{tariff_code}"]
    if tg_username:
        bits.append(f"username:@{tg_username.lstrip('@')}")
    if name:
        bits.append(f"name:{name}")
    return " | ".join(bits)


def to_api_datetime(value: str) -> str:
    dt = parse_datetime(value)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def user_to_access(user: dict[str, Any]) -> RemnawaveAccess:
    remote_user_uuid = str(user.get("uuid") or "").strip()
    short_uuid = str(user.get("shortUuid") or "").strip()
    subscription_url = str(user.get("subscriptionUrl") or "").strip()
    expire_at = from_api_datetime(str(user.get("expireAt") or ""))

    if not remote_user_uuid or not short_uuid:
        raise RemnawaveError("Remnawave response is missing user identifiers.")

    if not subscription_url:
        base = settings.remnawave_sub_public_url.rstrip("/")
        if base:
            subscription_url = f"{base}/{short_uuid}"
        else:
            raise RemnawaveError("Remnawave response is missing subscriptionUrl.")

    return RemnawaveAccess(
        remote_user_uuid=remote_user_uuid,
        username=str(user.get("username") or ""),
        short_uuid=short_uuid,
        subscription_url=subscription_url,
        expire_at=expire_at,
    )


def from_api_datetime(value: str) -> str:
    dt = parse_datetime(value)
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(sep=" ")
