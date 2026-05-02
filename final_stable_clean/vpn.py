import subprocess
from datetime import datetime
from secrets import token_urlsafe

from config import settings


def generate_access_key() -> str:
    return token_urlsafe(18)


def generate_keys():
    private_key = subprocess.check_output("wg genkey", shell=True).decode().strip()
    public_key = subprocess.check_output(
        f"echo {private_key} | wg pubkey", shell=True
    ).decode().strip()

    return private_key, public_key


def add_peer(public_key: str, ip: str):
    subprocess.run(
        f"wg set {settings.wireguard_interface} peer {public_key} allowed-ips {ip}/32",
        shell=True
    )


import subprocess
from config import settings


def build_config(user_id: int, expire_at=None):
    # 1. генерируем ключи
    private_key = subprocess.check_output("wg genkey", shell=True).decode().strip()
    public_key = subprocess.check_output(
        f"echo {private_key} | wg pubkey", shell=True
    ).decode().strip()

    # 2. выдаём IP
    client_octet = (user_id % 200) + 2
    client_ip = f"{settings.wireguard_prefix}{client_octet}"

    # 3. добавляем в WireGuard
    subprocess.run(
        f"wg set {settings.wireguard_interface} peer {public_key} allowed-ips {client_ip}/32",
        shell=True
    )

    # 4. создаём конфиг
    config = f"""
[Interface]
PrivateKey = {private_key}
Address = {client_ip}/32
DNS = {settings.wireguard_dns}

[Peer]
PublicKey = {settings.wireguard_public_key}
Endpoint = {settings.wireguard_endpoint}
AllowedIPs = {settings.wireguard_allowed_ips}
PersistentKeepalive = 25
"""

    return config


def build_download_name(user_id: int) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d")
    return f"vpn-{user_id}-{stamp}.conf"
