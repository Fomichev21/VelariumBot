from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ROLE_ADMIN
from database import (
    add_user,
    create_promo,
    get_payment,
    get_role,
    get_stats,
    get_user,
    list_recent_payments,
    list_users,
    mark_payment_failed,
    reset_subscription,
    set_banned,
    update_balance,
)
from payments import (
    complete_payment,
    deliver_access_message_async,
    notify_payment_rejected,
    notify_subscription_reset,
)

router = Router()


class AdminStates(StatesGroup):
    waiting_for_balance_user_id = State()
    waiting_for_balance_amount = State()
    waiting_for_promo_code = State()
    waiting_for_promo_days = State()
    waiting_for_broadcast = State()
    waiting_for_ban_user_id = State()
    waiting_for_reset_user_id = State()


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пользователи", callback_data="adm_users")],
            [InlineKeyboardButton(text="Выдать баланс", callback_data="adm_balance")],
            [InlineKeyboardButton(text="Промокоды", callback_data="adm_promo")],
            [InlineKeyboardButton(text="Сбросить VPN", callback_data="adm_reset_vpn")],
            [InlineKeyboardButton(text="Статистика", callback_data="adm_stats")],
            [InlineKeyboardButton(text="Платежи", callback_data="adm_payments")],
            [InlineKeyboardButton(text="Рассылка", callback_data="adm_broadcast")],
            [InlineKeyboardButton(text="Бан", callback_data="adm_ban")],
            [InlineKeyboardButton(text="Главное меню", callback_data="back_main")],
        ]
    )


def _is_admin(user_id: int) -> bool:
    return get_role(user_id) >= ROLE_ADMIN


async def _guard_admin(message_or_callback: CallbackQuery | Message) -> bool:
    add_user(message_or_callback.from_user.id, message_or_callback.from_user.username)
    if _is_admin(message_or_callback.from_user.id):
        return True

    text = "Недостаточно прав."
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer(text, show_alert=True)
    else:
        await message_or_callback.answer(text)
    return False


@router.callback_query(F.data == "open_admin")
async def open_admin(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return
    await callback.answer()
    await callback.message.edit_text("Админ-панель", reply_markup=admin_menu())


@router.callback_query(F.data == "adm_stats")
async def stats(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return

    summary = get_stats()
    await callback.answer()
    await callback.message.edit_text(
        "Статистика\n\n"
        f"Пользователей: {summary['users']}\n"
        f"Активных подписок: {summary['active_subscriptions']}\n"
        f"Оплаченных счетов: {summary['paid_payments']}\n"
        f"Выручка: {summary['revenue']}₽\n"
        f"Баланс пользователей: {summary['total_balance']}₽",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "adm_users")
async def users(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return

    rows = list_users(limit=10)
    if not rows:
        text = "Пользователей пока нет."
    else:
        text = "Последние пользователи\n\n" + "\n".join(
            (
                f"{row['user_id']} • @{row['username'] or 'unknown'} • "
                f"роль {row['role']} • подписка до {row['subscription_until'] or 'нет'}"
            )
            for row in rows
        )

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=admin_menu())


@router.callback_query(F.data == "adm_payments")
async def payments(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return

    rows = list_recent_payments(limit=10)
    if not rows:
        text = "Платежей пока нет."
    else:
        text = "Последние платежи\n\n" + "\n".join(
            (
                f"{row['invoice_code'] or row['id'][:8]} • user {row['user_id']} • "
                f"{row['amount']}₽ • {row['status']}"
            )
            for row in rows
        )

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=admin_menu())


@router.callback_query(F.data.startswith("adm_payment_accept:"))
async def approve_payment(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return

    payment_id = callback.data.split(":", maxsplit=1)[1]
    payment = get_payment(payment_id)
    if not payment:
        await callback.answer("Счет не найден", show_alert=True)
        return

    if payment["status"] == "paid":
        await callback.answer("Этот счет уже подтвержден", show_alert=True)
        return

    if payment["status"] == "failed":
        await callback.answer("Этот счет уже отклонен", show_alert=True)
        return

    try:
        result = complete_payment(payment_id, reviewed_by=callback.from_user.id)
        await deliver_access_message_async(result)
    except Exception as exc:
        await callback.answer("Не удалось подтвердить оплату", show_alert=True)
        await callback.message.answer(
            f"Ошибка при подтверждении счета {payment.get('invoice_code') or payment_id}: {exc}"
        )
        return

    updated_payment = get_payment(payment_id)
    await callback.answer("Оплата подтверждена")
    await callback.message.edit_text(
        "Счет подтвержден.\n\n"
        f"Счет: {updated_payment.get('invoice_code') or payment_id}\n"
        f"Пользователь: {updated_payment['user_id']}\n"
        f"Сумма: {updated_payment['amount']}₽\n"
        "Статус: Оплачен",
    )


@router.callback_query(F.data.startswith("adm_payment_reject:"))
async def reject_payment(callback: CallbackQuery) -> None:
    if not await _guard_admin(callback):
        return

    payment_id = callback.data.split(":", maxsplit=1)[1]
    payment = get_payment(payment_id)
    if not payment:
        await callback.answer("Счет не найден", show_alert=True)
        return

    if payment["status"] == "paid":
        await callback.answer("Этот счет уже подтвержден", show_alert=True)
        return

    if payment["status"] == "failed":
        await callback.answer("Этот счет уже отклонен", show_alert=True)
        return

    updated_payment = mark_payment_failed(payment_id, reviewed_by=callback.from_user.id)
    await notify_payment_rejected(callback.bot, updated_payment)
    await callback.answer("Счет отклонен")
    await callback.message.edit_text(
        "Счет отклонен.\n\n"
        f"Счет: {updated_payment.get('invoice_code') or payment_id}\n"
        f"Пользователь: {updated_payment['user_id']}\n"
        f"Сумма: {updated_payment['amount']}₽\n"
        "Статус: Отклонен",
    )


@router.callback_query(F.data == "adm_balance")
async def balance_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_balance_user_id)
    await callback.message.edit_text("Введите ID пользователя, которому нужно выдать баланс.")


@router.message(AdminStates.waiting_for_balance_user_id)
async def balance_user_id(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    try:
        user_id = int(message.text or "")
    except ValueError:
        await message.answer("Нужен числовой ID.")
        return

    await state.update_data(target_user_id=user_id)
    await state.set_state(AdminStates.waiting_for_balance_amount)
    await message.answer("Теперь введите сумму пополнения.")


@router.message(AdminStates.waiting_for_balance_amount)
async def balance_amount(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    try:
        amount = int(message.text or "")
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return

    data = await state.get_data()
    target_user_id = int(data["target_user_id"])
    user = get_user(target_user_id)
    if user["created_at"] is None:
        await message.answer("Пользователь еще не запускал бота.")
        return

    update_balance(target_user_id, amount)
    await state.clear()
    await message.answer(
        f"Пользователю {target_user_id} начислено {amount}₽.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "adm_promo")
async def promo_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_promo_code)
    await callback.message.edit_text("Введите код промокода.")


@router.message(AdminStates.waiting_for_promo_code)
async def promo_code(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("Промокод не должен быть пустым.")
        return

    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.waiting_for_promo_days)
    await message.answer("На сколько дней будет этот промокод?")


@router.message(AdminStates.waiting_for_promo_days)
async def promo_days(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    try:
        days = int(message.text or "")
    except ValueError:
        await message.answer("Количество дней должно быть числом.")
        return

    if days <= 0:
        await message.answer("Количество дней должно быть больше нуля.")
        return

    data = await state.get_data()
    create_promo(data["promo_code"], days)
    await state.clear()
    await message.answer(
        f"Промокод {data['promo_code']} сохранен на {days} дн.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "adm_reset_vpn")
async def reset_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_reset_user_id)
    await callback.message.edit_text("Введите ID пользователя, которому нужно полностью сбросить VPN и подписку.")


@router.message(AdminStates.waiting_for_reset_user_id)
async def reset_user_vpn(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    try:
        target_user_id = int(message.text or "")
    except ValueError:
        await message.answer("Нужен числовой ID.")
        return

    user = get_user(target_user_id)
    if user["created_at"] is None:
        await message.answer("Такого пользователя нет в базе.")
        return

    try:
        result = reset_subscription(target_user_id)
    except Exception as exc:
        await message.answer(f"Не удалось сбросить подписку: {exc}")
        return

    await notify_subscription_reset(message.bot, target_user_id)
    await state.clear()
    await message.answer(
        (
            f"Подписка пользователя {target_user_id} полностью сброшена.\n"
            f"Доступ на сервере удален: {'да' if result['removed_remote'] else 'нет'}"
        ),
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "adm_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text("Введите текст рассылки.")


@router.message(AdminStates.waiting_for_broadcast)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    delivered = 0
    for user in list_users(limit=500):
        try:
            await message.bot.send_message(user["user_id"], message.text)
            delivered += 1
        except Exception:
            continue

    await state.clear()
    await message.answer(
        f"Рассылка завершена. Доставлено: {delivered}.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "adm_ban")
async def ban_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_ban_user_id)
    await callback.message.edit_text("Введите ID пользователя для переключения бана.")


@router.message(AdminStates.waiting_for_ban_user_id)
async def ban_toggle(message: Message, state: FSMContext) -> None:
    if not await _guard_admin(message):
        return

    try:
        target_user_id = int(message.text or "")
    except ValueError:
        await message.answer("Нужен числовой ID.")
        return

    user = get_user(target_user_id)
    if user["created_at"] is None:
        await message.answer("Такого пользователя нет в базе.")
        return

    new_state = not bool(user["is_banned"])
    set_banned(target_user_id, new_state)
    await state.clear()
    await message.answer(
        f"Пользователь {target_user_id} теперь {'заблокирован' if new_state else 'разблокирован'}.",
        reply_markup=admin_menu(),
    )
