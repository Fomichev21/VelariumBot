from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests

from config import settings


class ThreeXUIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreeXUIAccess:
    client_id: str
    inbound_id: int
    email: str
    sub_id: str
    access_url: str
    expiry_ms: int


@dataclass(frozen=True)
class ThreeXUIClientRecord:
    inbound_id: int
    client: dict


def is_three_xui_configured() -> bool:
    return bool(
        settings.threexui_base_url
        and settings.threexui_username
        and settings.threexui_password
    )


class ThreeXUIClient:
    def __init__(self) -> None:
        if not is_three_xui_configured():
            raise ThreeXUIError("3x-ui credentials are not configured.")
        self.base_url = settings.threexui_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json"})

    def close(self) -> None:
        self.session.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs,
    ) -> dict | list | str:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()

        if not expect_json:
            return response.text

        body = response.text.strip()
        if not body:
            return {}

        try:
            data = response.json()
        except ValueError as exc:
            raise ThreeXUIError(f"3x-ui returned non-JSON response for {path}.") from exc

        if isinstance(data, dict) and data.get("success") is False:
            raise ThreeXUIError(data.get("msg") or f"3x-ui request failed for {path}.")

        return data

    def login(self) -> None:
        payload = {
            "username": settings.threexui_username,
            "password": settings.threexui_password,
        }
        last_error: Exception | None = None
        for kwargs in ({"json": payload}, {"data": payload}):
            try:
                self._request("POST", "/login", **kwargs)
                if self.session.cookies:
                    return
            except Exception as exc:
                last_error = exc

        raise ThreeXUIError("3x-ui login failed.") from last_error

    def list_inbounds(self) -> list[dict]:
        data = self._request("GET", "/panel/api/inbounds/list")
        if isinstance(data, dict):
            obj = data.get("obj", [])
            return obj if isinstance(obj, list) else []
        return data if isinstance(data, list) else []

    def get_inbound(self, inbound_id: int) -> dict:
        data = self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")
        if isinstance(data, dict) and isinstance(data.get("obj"), dict):
            return data["obj"]
        raise ThreeXUIError(f"Inbound {inbound_id} was not found in 3x-ui.")

    def resolve_inbound_id(self) -> int:
        if settings.threexui_inbound_id:
            return settings.threexui_inbound_id

        inbounds = self.list_inbounds()
        if not inbounds:
            raise ThreeXUIError("No inbounds were returned by 3x-ui.")

        target_remark = settings.threexui_inbound_remark.strip().lower()
        if target_remark:
            for inbound in inbounds:
                remark = str(inbound.get("remark", "")).strip().lower()
                if remark == target_remark:
                    return int(inbound["id"])

        for inbound in inbounds:
            if inbound.get("enable", True):
                return int(inbound["id"])

        return int(inbounds[0]["id"])

    def get_new_uuid(self) -> str:
        data = self._request("GET", "/panel/api/server/getNewUUID")
        if isinstance(data, dict):
            obj = data.get("obj")
            if isinstance(obj, str) and obj:
                return obj
        return str(uuid.uuid4())

    def add_client(
        self,
        *,
        user_id: int,
        tariff_code: str,
        expire_at: str,
    ) -> ThreeXUIAccess:
        inbound_id = self.resolve_inbound_id()
        client_id = self.get_new_uuid()
        sub_id = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
        email = f"{settings.threexui_client_prefix}-{user_id}-{tariff_code}-{sub_id[:6]}"
        expiry_ms = _iso_to_unix_ms(expire_at)

        client_payload = {
            "id": client_id,
            "flow": settings.threexui_flow,
            "email": email,
            "limitIp": settings.threexui_limit_ip,
            "totalGB": settings.threexui_total_gb,
            "expiryTime": expiry_ms,
            "enable": True,
            "tgId": str(user_id),
            "subId": sub_id,
            "comment": f"telegram:{user_id}",
            "reset": 0,
        }
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":")),
        }

        response = self._request("POST", "/panel/api/inbounds/addClient", json=payload)
        if response == {}:
            inbound = self.get_inbound(inbound_id)
            if not _inbound_has_email(inbound, email):
                raise ThreeXUIError(
                    "3x-ui addClient returned an empty response and client was not found."
                )

        return ThreeXUIAccess(
            client_id=client_id,
            inbound_id=inbound_id,
            email=email,
            sub_id=sub_id,
            access_url=build_subscription_url(sub_id),
            expiry_ms=expiry_ms,
        )

    def find_client_by_id(self, client_id: str) -> ThreeXUIClientRecord | None:
        for inbound in self.list_inbounds():
            inbound_id = int(inbound["id"])
            for client in _parse_clients(inbound.get("settings")):
                if str(client.get("id")) == client_id:
                    return ThreeXUIClientRecord(inbound_id=inbound_id, client=client)
        return None

    def update_client_expiry(self, client_id: str, expire_at: str) -> ThreeXUIAccess:
        record = self.find_client_by_id(client_id)
        if not record:
            raise ThreeXUIError(f"Client {client_id} was not found in 3x-ui.")

        expiry_ms = _iso_to_unix_ms(expire_at)
        client_payload = dict(record.client)
        client_payload["expiryTime"] = expiry_ms
        client_payload["enable"] = True
        payload = {
            "id": record.inbound_id,
            "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":")),
        }
        self._request("POST", f"/panel/api/inbounds/updateClient/{client_id}", json=payload)

        sub_id = str(client_payload.get("subId") or "").strip()
        if not sub_id:
            raise ThreeXUIError(f"Client {client_id} does not have subId in 3x-ui.")

        return ThreeXUIAccess(
            client_id=client_id,
            inbound_id=record.inbound_id,
            email=str(client_payload.get("email") or ""),
            sub_id=sub_id,
            access_url=build_subscription_url(sub_id),
            expiry_ms=expiry_ms,
        )

    def delete_client(self, client_id: str) -> bool:
        record = self.find_client_by_id(client_id)
        if not record:
            return False

        self._request(
            "POST",
            f"/panel/api/inbounds/{record.inbound_id}/delClient/{client_id}",
        )
        return True


def build_subscription_url(sub_id: str) -> str:
    base_url = settings.threexui_sub_base_url
    if not base_url:
        parsed = urlsplit(settings.threexui_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ThreeXUIError("THREEXUI_SUB_BASE_URL is not configured.")
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    parsed = urlsplit(base_url)
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    sub_path = settings.threexui_sub_path.strip("/")

    if sub_path and (not path_segments or path_segments[-1] != sub_path):
        path_segments.append(sub_path)

    path_segments.append(sub_id)
    normalized_path = "/" + "/".join(path_segments)

    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _iso_to_unix_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _parse_clients(raw_settings: str | dict | None) -> list[dict]:
    if isinstance(raw_settings, dict):
        clients = raw_settings.get("clients", [])
        return clients if isinstance(clients, list) else []

    if not isinstance(raw_settings, str) or not raw_settings.strip():
        return []

    try:
        payload = json.loads(raw_settings)
    except json.JSONDecodeError:
        return []

    clients = payload.get("clients", [])
    return clients if isinstance(clients, list) else []


def _inbound_has_email(inbound: dict, email: str) -> bool:
    return any(
        client.get("email") == email
        for client in _parse_clients(inbound.get("settings"))
    )
