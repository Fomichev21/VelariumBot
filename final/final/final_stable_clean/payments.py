from __future__ import annotations

import asyncio
import uuid

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import TARIFFS, settings
from database import (
    create_payment,
    get_payment,
    list_admin_ids,
    list_users_expiring_soon,
    mark_expiry_notice_sent,
    mark_payment_access_sent,
    mark_payment_paid,
)


def create_payment_for_tariff(user_id: int, tariff_code: str) -> dict[str, str | int]:
    tariff = TARIFFS[tariff_code]
    payment_id = str(uuid.uuid4())
    pay_url = settings.manual_payment_url

    invoice_seq = create_payment(
        payment_id=payment_id,
        user_id=user_id,
        amount=tariff["price"],
        tariff_code=tariff_code,
        provider=settings.payment_provider,
        payment_url=pay_url,
    )

    payment = get_payment(payment_id)
    return {
        "id": payment_id,
        "url": pay_url,
        "amount": tariff["price"],
        "title": tariff["title"],
        "invoice_code": payment["invoice_code"],
        "invoice_seq": invoice_seq,
    }


def check_payment(payment_id: str) -> dict | None:
    return get_payment(payment_id)


def complete_payment(payment_id: str, reviewed_by: int | None = None) -> dict | None:
    return mark_payment_paid(payment_id, reviewed_by=reviewed_by)


async def deliver_access_message_async(payment_result: dict | None) -> bool:
    if not payment_result or not settings.bot_token:
        return False

    payment = payment_result.get("payment") or {}
    user = payment_result.get("user") or {}
    vpn_key = payment_result.get("vpn_key") or {}
    payment_id = payment.get("id")
    access_url = str(vpn_key.get("config_text") or "").strip()

    if not payment_id or payment.get("access_sent_at") or not access_url:
        return False

    access_label = "Subscription Link" if access_url.startswith(("http://", "https://")) else "VPN ссылка"
    text = (
        "Оплата подтверждена.\n\n"
        f"Подписка активна до: {user.get('subscription_until') or 'не задано'}\n"
        f"UUID: {vpn_key.get('vpn_key') or 'не задан'}\n"
        f"{access_label}:\n{access_url}"
    )

    try:
        bot = Bot(token=settings.bot_token)
        try:
            await bot.send_message(payment["user_id"], text)
        finally:
            await bot.session.close()
    except Exception:
        return False

    mark_payment_access_sent(payment_id)
    return True


def deliver_access_message(payment_result: dict | None) -> bool:
    return asyncio.run(deliver_access_message_async(payment_result))


def build_admin_payment_markup(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Принять", callback_data=f"adm_payment_accept:{payment_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"adm_payment_reject:{payment_id}"),
            ]
        ]
    )


async def notify_admins_about_payment(bot: Bot, payment_id: str) -> int:
    payment = get_payment(payment_id)
    if not payment:
        return 0

    admin_ids = list_admin_ids()
    if not admin_ids and settings.owner_id:
        admin_ids = [settings.owner_id]

    if not admin_ids:
        return 0

    text = (
        "Новый счет ожидает проверки.\n\n"
        f"Счет: {payment['invoice_code'] or payment['id']}\n"
        f"Платеж ID: {payment['id']}\n"
        f"Пользователь: {payment['user_id']}\n"
        f"Тариф: {TARIFFS[payment['tariff_code']]['title']}\n"
        f"Сумма: {payment['amount']} RUB\n"
        f"Ссылка на оплату:\n{payment['payment_url']}"
    )

    delivered = 0
    markup = build_admin_payment_markup(payment_id)
    for admin_id in dict.fromkeys(admin_ids):
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
            delivered += 1
        except Exception:
            continue

    return delivered


async def notify_payment_rejected(bot: Bot, payment: dict | None) -> bool:
    if not payment:
        return False

    try:
        await bot.send_message(
            payment["user_id"],
            (
                "Оплата не подтверждена администратором.\n\n"
                f"Счет: {payment.get('invoice_code') or payment['id']}\n"
                "Если вы уже оплатили счет, свяжитесь с поддержкой и приложите чек."
            ),
        )
    except Exception:
        return False

    return True


async def notify_subscription_reset(bot: Bot, user_id: int) -> bool:
    try:
        await bot.send_message(
            user_id,
            (
                "Доступ к VPN и подписка были сброшены администратором.\n\n"
                "Если это ошибка, напишите в поддержку."
            ),
        )
    except Exception:
        return False

    return True


async def process_expiry_reminders(bot: Bot) -> int:
    reminded = 0
    for user in list_users_expiring_soon(within_hours=24):
        subscription_until = str(user.get("subscription_until") or "").strip()
        if not subscription_until:
            continue

        try:
            await bot.send_message(
                int(user["user_id"]),
                (
                    "Подписка заканчивается меньше чем через 24 часа.\n\n"
                    f"Дата окончания: {subscription_until}\n"
                    "Продли подписку заранее, чтобы не потерять доступ."
                ),
            )
        except Exception:
            continue

        mark_expiry_notice_sent(int(user["user_id"]), subscription_until)
        reminded += 1

    return reminded
