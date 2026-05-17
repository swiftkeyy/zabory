import asyncio
import logging
import sqlite3
import os
import tempfile
from datetime import datetime

import openpyxl
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
    FSInputFile,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импорт модуля для работы с БД
from database import _connect, _placeholder, init_db, DB_TYPE

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("BOT_TOKEN", "8705623484:AAHuEOSwTpEa6VlXcHwOoxk9H-ao2ChmK7w")
ADMINS_STR = os.getenv("ADMINS", "5118405789, 5635535380")

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавь его в Variables на Railway.")

ADMINS = [int(x.strip()) for x in ADMINS_STR.split(",") if x.strip()]

PHOTOS_PER_PAGE = 6
LEADS_PER_PAGE = 5
REVIEWS_PER_PAGE = 3
LEAD_STATUSES = ["Новая", "В работе", "Закрыта", "Отказ"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("DB_PATH", "fence_bot.db")


# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ======================
def get_prices_dict():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT fence_type, price_per_m2 FROM prices ORDER BY fence_type")
    prices = dict(cur.fetchall())
    conn.close()
    return prices


def sync_prices_with_types():
    """Синхронизирует таблицу цен с типами заборов (добавляет отсутствующие)"""
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    
    # Получаем все типы заборов
    cur.execute("SELECT name FROM fence_types")
    fence_types = [row[0] for row in cur.fetchall()]
    
    # Получаем существующие цены
    cur.execute("SELECT fence_type FROM prices")
    existing_prices = [row[0] for row in cur.fetchall()]
    
    # Добавляем отсутствующие цены
    added = 0
    for fence_type in fence_types:
        if fence_type not in existing_prices:
            if DB_TYPE == "postgresql":
                cur.execute(
                    f"INSERT INTO prices (fence_type, price_per_m2) VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING",
                    (fence_type, 0)
                )
            else:
                cur.execute(
                    f"INSERT OR IGNORE INTO prices (fence_type, price_per_m2) VALUES ({ph}, {ph})",
                    (fence_type, 0)
                )
            added += 1
            logger.info(f"✅ Добавлена цена для типа: {fence_type}")
    
    if added > 0:
        conn.commit()
        logger.info(f"✅ Синхронизировано {added} цен с типами заборов")
    
    conn.close()


def get_fence_types():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description FROM fence_types ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_fence_type(type_id: int):
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"SELECT id, name, description FROM fence_types WHERE id = {ph}", (type_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_reviews(offset: int, limit: int, approved_only: bool = True):
    conn = _connect()
    cur = conn.cursor()
    if approved_only:
        cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 1")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT id, author, text FROM reviews WHERE approved = 1 ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    else:
        cur.execute("SELECT COUNT(*) FROM reviews")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT id, author, text FROM reviews ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    rows = cur.fetchall()
    conn.close()
    return rows, total


def get_pending_reviews():
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, author, text, user_id, created_at FROM reviews WHERE approved = 0 ORDER BY id DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def approve_review(review_id: int):
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"UPDATE reviews SET approved = 1 WHERE id = {ph}", (review_id,))
    conn.commit()
    conn.close()


def reject_review(review_id: int):
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"DELETE FROM reviews WHERE id = {ph}", (review_id,))
    conn.commit()
    conn.close()


def get_works(offset: int, limit: int):
    """Получает работы для Telegram бота (использует общую таблицу)"""
    from sync_manager import get_works_unified
    
    # Получаем все работы (и TG, и VK)
    rows, total = get_works_unified(offset, limit)
    
    # Преобразуем формат для совместимости
    # Формат: (id, file_id, caption)
    result = []
    for row in rows:
        work_id, tg_file_id, vk_attachment, caption, platform, added_at = row
        # Для TG бота показываем только работы с tg_file_id
        if tg_file_id:
            result.append((work_id, tg_file_id, caption))
    
    return result, len(result)


def get_leads(offset: int, limit: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM leads")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT id, name, phone, status, created_at FROM leads ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return rows, total


def get_lead(lead_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, name, phone, address, comment, calc_data, status, created_at "
        "FROM leads WHERE id = ?",
        (lead_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_all_user_ids():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


# ====================== FSM ======================
class CalculatorStates(StatesGroup):
    length = State()
    height = State()


class LeadStates(StatesGroup):
    name = State()
    phone = State()
    address = State()
    comment = State()


class AddWorkStates(StatesGroup):
    photo = State()
    caption = State()


class EditPriceStates(StatesGroup):
    waiting_price = State()


class AddTypeStates(StatesGroup):
    name = State()
    description = State()


class EditTypeStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()


class AddReviewStates(StatesGroup):
    author = State()
    text = State()


class SubmitReviewStates(StatesGroup):
    author = State()
    text = State()


class BroadcastStates(StatesGroup):
    waiting_text = State()
    confirm = State()


# ====================== ХЕЛПЕРЫ ======================
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def main_menu():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data="calc_start"))
    b.row(InlineKeyboardButton(text="📸 Наши работы", callback_data="works_1"))
    b.row(InlineKeyboardButton(text="🏗 Виды заборов", callback_data="types"))
    b.row(InlineKeyboardButton(text="💰 Цены", callback_data="prices"))
    b.row(InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews_1"))
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    return b.as_markup()


def back_main_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))
    return b.as_markup()


def cancel_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"))
    return b.as_markup()


def admin_menu():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📋 Все заявки", callback_data="admin_leads_1"))
    b.row(InlineKeyboardButton(text="📸 Управление работами", callback_data="admin_works"))
    b.row(InlineKeyboardButton(text="🏗 Управление видами заборов", callback_data="admin_types"))
    b.row(InlineKeyboardButton(text="⭐ Управление отзывами", callback_data="admin_reviews"))
    b.row(InlineKeyboardButton(text="💰 Управление ценами", callback_data="admin_prices"))
    b.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    b.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"))
    b.row(InlineKeyboardButton(text="📥 Экспорт Excel", callback_data="admin_export"))
    b.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close"))
    return b.as_markup()


def admin_back_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))
    return b.as_markup()


async def safe_edit(call: CallbackQuery, text: str, reply_markup=None):
    """edit_text если можем, иначе отправляем новое сообщение."""
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await call.message.answer(text, reply_markup=reply_markup)


# ====================== РОУТЕР ======================
router = Router()


# ====================== СТАРТ ======================
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    
    if DB_TYPE == "postgresql":
        cur.execute(
            f"INSERT INTO users (user_id, username, full_name, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}) ON CONFLICT DO NOTHING",
            (
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                datetime.now().isoformat(),
            ),
        )
    else:
        cur.execute(
            f"INSERT OR IGNORE INTO users VALUES ({ph},{ph},{ph},{ph})",
            (
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                datetime.now().isoformat(),
            ),
        )
    conn.commit()
    conn.close()

    await message.answer(
        "🔨 <b>Заборы под ключ в Ижевске</b>\n\n"
        "Качественно • Быстро • С гарантией\nБесплатный выезд замерщика",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_menu())


@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(
        call,
        "🔨 <b>Заборы под ключ в Ижевске</b>\n\n"
        "Качественно • Быстро • С гарантией\nБесплатный выезд замерщика",
        reply_markup=main_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_back")
async def admin_back(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    await safe_edit(call, "🛠 <b>Админ-панель</b>", reply_markup=admin_menu())
    await call.answer()


@router.callback_query(F.data == "admin_close")
async def admin_close(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


# ====================== КАЛЬКУЛЯТОР ======================
@router.callback_query(F.data == "calc_start")
async def calc_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(CalculatorStates.length)
    await safe_edit(
        call,
        "🧮 <b>Калькулятор стоимости</b>\n\nВведите длину забора в метрах (например, <code>25</code> или <code>25.5</code>):",
        reply_markup=cancel_kb(),
    )
    await call.answer()


def _parse_positive_float(s: str):
    try:
        v = float(s.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    if v <= 0 or v > 10000:
        return None
    return v


@router.message(CalculatorStates.length)
async def calc_length(message: Message, state: FSMContext):
    v = _parse_positive_float(message.text)
    if v is None:
        await message.answer("❌ Введите положительное число (метры). Пример: <code>25</code>")
        return
    await state.update_data(length=v)
    await state.set_state(CalculatorStates.height)
    await message.answer(
        f"Длина: <b>{v} м</b>\n\nТеперь введите высоту забора в метрах (обычно 1.5–2.5):",
        reply_markup=cancel_kb(),
    )


@router.message(CalculatorStates.height)
async def calc_height(message: Message, state: FSMContext):
    v = _parse_positive_float(message.text)
    if v is None or v > 10:
        await message.answer("❌ Введите положительное число (метры). Пример: <code>2</code>")
        return
    await state.update_data(height=v)

    prices = get_prices_dict()
    if not prices:
        await message.answer("❌ В системе пока не настроены цены. Свяжитесь с менеджером.", reply_markup=back_main_kb())
        await state.clear()
        return

    types_list = list(prices.keys())
    await state.update_data(price_types=types_list)

    b = InlineKeyboardBuilder()
    for idx, t in enumerate(types_list):
        b.row(InlineKeyboardButton(text=f"{t} — {prices[t]} ₽/м²", callback_data=f"calc_t_{idx}"))
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"))

    await message.answer(
        f"Высота: <b>{v} м</b>\n\nВыберите материал забора:",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("calc_t_"), CalculatorStates.height)
async def calc_pick_type(call: CallbackQuery, state: FSMContext):
    try:
        idx = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    data = await state.get_data()
    types_list = data.get("price_types") or []
    if idx < 0 or idx >= len(types_list):
        await call.answer("Тип не найден", show_alert=True)
        return

    fence_type = types_list[idx]
    length = data["length"]
    height = data["height"]
    prices = get_prices_dict()
    price_per_m2 = prices.get(fence_type, 0)
    area = length * height
    total = int(round(area * price_per_m2))

    calc_data = f"{fence_type}, {length}×{height} м ({area:.1f} м²) — {total} ₽"
    await state.update_data(calc_data=calc_data)

    text = (
        "🧮 <b>Расчёт стоимости</b>\n\n"
        f"• Материал: <b>{fence_type}</b>\n"
        f"• Размеры: <b>{length} × {height} м</b>\n"
        f"• Площадь: <b>{area:.1f} м²</b>\n"
        f"• Цена за м²: <b>{price_per_m2} ₽</b> (материал, без работы)\n\n"
        f"💰 <b>Итого: ~{total:,} ₽</b>\n\n".replace(",", " ")
        + "⚠️ Это предварительный расчёт. Финальная стоимость определяется на бесплатном замере "
        "(учитываются рельеф, ворота, калитка, доставка)."
    )

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📍 Заказать бесплатный замер", callback_data="lead_start"))
    b.row(InlineKeyboardButton(text="🧮 Пересчитать", callback_data="calc_start"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await safe_edit(call, text, reply_markup=b.as_markup())
    await call.answer()


# ====================== НАШИ РАБОТЫ ======================
@router.callback_query(F.data.startswith("works_"))
async def works_page(call: CallbackQuery):
    try:
        page = max(1, int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        page = 1
    offset = (page - 1) * PHOTOS_PER_PAGE
    rows, total = get_works(offset, PHOTOS_PER_PAGE)

    if total == 0:
        await safe_edit(
            call,
            "📸 <b>Наши работы</b>\n\nПока нет загруженных фотографий. Скоро здесь появится наша галерея!",
            reply_markup=back_main_kb(),
        )
        await call.answer()
        return

    total_pages = (total + PHOTOS_PER_PAGE - 1) // PHOTOS_PER_PAGE
    page = min(page, total_pages)

    try:
        await call.message.delete()
    except Exception:
        pass

    media = []
    for i, (_id, file_id, caption) in enumerate(rows):
        if i == 0:
            cap = f"📸 <b>Наши работы</b> — стр. {page}/{total_pages}\n\n{caption or ''}".strip()
            media.append(InputMediaPhoto(media=file_id, caption=cap, parse_mode=ParseMode.HTML))
        else:
            media.append(InputMediaPhoto(media=file_id, caption=caption or None))

    try:
        await call.bot.send_media_group(call.message.chat.id, media=media)
    except Exception as e:
        logger.exception("send_media_group failed: %s", e)
        await call.bot.send_message(
            call.message.chat.id,
            "❌ Не удалось загрузить фотографии. Попробуйте позже.",
            reply_markup=back_main_kb(),
        )
        await call.answer()
        return

    b = InlineKeyboardBuilder()
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"works_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"works_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await call.bot.send_message(
        call.message.chat.id,
        f"Страница <b>{page}</b> из <b>{total_pages}</b>",
        reply_markup=b.as_markup(),
    )
    await call.answer()


# ====================== ВИДЫ ЗАБОРОВ ======================
@router.callback_query(F.data == "types")
async def types_list(call: CallbackQuery):
    types = get_fence_types()
    if not types:
        await safe_edit(
            call,
            "🏗 <b>Виды заборов</b>\n\nПока не добавлено ни одного типа. Загляните позже.",
            reply_markup=back_main_kb(),
        )
        await call.answer()
        return

    b = InlineKeyboardBuilder()
    for tid, name, _desc in types:
        b.row(InlineKeyboardButton(text=name, callback_data=f"ftype_{tid}"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await safe_edit(
        call,
        "🏗 <b>Виды заборов</b>\n\nВыберите тип, чтобы посмотреть подробности:",
        reply_markup=b.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ftype_"))
async def type_detail(call: CallbackQuery):
    try:
        tid = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    row = get_fence_type(tid)
    if not row:
        await call.answer("Тип не найден", show_alert=True)
        return
    _id, name, description = row
    prices = get_prices_dict()
    price = prices.get(name)
    text = description or f"<b>{name}</b>"
    if price:
        text += f"\n\n💰 Цена: <b>{price} ₽/м²</b> (материал, без учёта работы)"

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data="calc_start"))
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    b.row(InlineKeyboardButton(text="🔙 К видам заборов", callback_data="types"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await safe_edit(call, text, reply_markup=b.as_markup())
    await call.answer()


# ====================== ЦЕНЫ ======================
@router.callback_query(F.data == "prices")
async def prices_view(call: CallbackQuery):
    prices = get_prices_dict()
    if not prices:
        await safe_edit(
            call,
            "💰 <b>Цены</b>\n\nЦены пока не настроены. Напишите менеджеру.",
            reply_markup=back_main_kb(),
        )
        await call.answer()
        return
    lines = ["💰 <b>Прайс-лист (руб/м²)</b>\n"]
    for t, p in prices.items():
        lines.append(f"• {t}: <b>{p} ₽</b>")
    lines.append("\n⚠️ Цены указаны за материал, без учёта работы.")
    lines.append("Финальная стоимость — после бесплатного замера.")

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data="calc_start"))
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await safe_edit(call, "\n".join(lines), reply_markup=b.as_markup())
    await call.answer()


# ====================== ОТЗЫВЫ ======================
@router.callback_query(F.data.startswith("reviews_"))
async def reviews_page(call: CallbackQuery):
    try:
        page = max(1, int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        page = 1
    offset = (page - 1) * REVIEWS_PER_PAGE
    rows, total = get_reviews(offset, REVIEWS_PER_PAGE)

    if total == 0:
        await safe_edit(
            call,
            "⭐ <b>Отзывы</b>\n\nПока нет отзывов. Будьте первым — закажите замер!",
            reply_markup=back_main_kb(),
        )
        await call.answer()
        return

    total_pages = (total + REVIEWS_PER_PAGE - 1) // REVIEWS_PER_PAGE
    page = min(page, total_pages)

    parts = [f"⭐ <b>Отзывы</b> — стр. {page}/{total_pages}\n"]
    for _id, author, text in rows:
        parts.append(f"<b>{author}</b>\n{text}\n")

    b = InlineKeyboardBuilder()
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"reviews_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"reviews_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="submit_review"))
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    b.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main"))

    await safe_edit(call, "\n".join(parts), reply_markup=b.as_markup())
    await call.answer()


# ====================== ОТПРАВКА ОТЗЫВА ПОЛЬЗОВАТЕЛЕМ ======================
@router.callback_query(F.data == "submit_review")
async def submit_review_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(SubmitReviewStates.author)
    await safe_edit(
        call,
        "✍️ <b>Оставить отзыв</b>\n\nВведите ваше имя (например, <i>Алексей, Ижевск</i>):",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(SubmitReviewStates.author)
async def submit_review_author(message: Message, state: FSMContext):
    author = (message.text or "").strip()
    if not (2 <= len(author) <= 100):
        await message.answer("❌ Имя должно быть от 2 до 100 символов.")
        return
    await state.update_data(author=author)
    await state.set_state(SubmitReviewStates.text)
    await message.answer(
        f"Приятно познакомиться, <b>{author}</b>!\n\nТеперь напишите ваш отзыв (от 10 до 2000 символов):",
        reply_markup=cancel_kb(),
    )


@router.message(SubmitReviewStates.text)
async def submit_review_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not (10 <= len(text) <= 2000):
        await message.answer("❌ Отзыв должен быть от 10 до 2000 символов.")
        return
    data = await state.get_data()
    author = data["author"]
    user_id = message.from_user.id

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reviews (author, text, created_at, approved, user_id) VALUES (?, ?, ?, 0, ?)",
        (author, text, datetime.now().isoformat(), user_id),
    )
    review_id = cur.lastrowid
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(
        "✅ <b>Спасибо за ваш отзыв!</b>\n\n"
        "Он будет опубликован после проверки администратором.",
        reply_markup=back_main_kb(),
    )

    contact = f"@{message.from_user.username}" if message.from_user.username else (
        message.from_user.full_name or f"id{user_id}"
    )
    admin_text = (
        "⭐ <b>Новый отзыв на модерацию!</b>\n\n"
        f"<b>#{review_id}</b>\n"
        f"<b>Автор:</b> {author}\n"
        f"<b>Текст:</b> {text}\n\n"
        f"<b>Отправил:</b> {contact} (id <code>{user_id}</code>)"
    )
    for admin_id in ADMINS:
        try:
            b = InlineKeyboardBuilder()
            b.row(
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"review_approve_{review_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"review_reject_{review_id}"),
            )
            await message.bot.send_message(admin_id, admin_text, reply_markup=b.as_markup())
        except Exception as e:
            logger.warning("Failed to notify admin %s about review: %s", admin_id, e)


# ====================== ЗАЯВКА НА ЗАМЕР ======================
@router.callback_query(F.data == "lead_start")
async def lead_start(call: CallbackQuery, state: FSMContext):
    # Сохраняем расчёт из калькулятора, если он есть
    data = await state.get_data()
    calc_data = data.get("calc_data")
    await state.clear()
    if calc_data:
        await state.update_data(calc_data=calc_data)
    await state.set_state(LeadStates.name)
    await safe_edit(
        call,
        "📍 <b>Заявка на бесплатный замер</b>\n\nКак вас зовут?",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(LeadStates.name)
async def lead_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("❌ Имя должно быть от 2 до 100 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(LeadStates.phone)
    await message.answer(
        f"Приятно познакомиться, <b>{name}</b>!\n\nВведите ваш номер телефона:",
        reply_markup=cancel_kb(),
    )


@router.message(LeadStates.phone)
async def lead_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        await message.answer("❌ Введите корректный номер (минимум 10 цифр).")
        return
    await state.update_data(phone=phone)
    await state.set_state(LeadStates.address)
    await message.answer(
        "📍 Укажите адрес объекта (город, улица, дом):",
        reply_markup=cancel_kb(),
    )


def _validate_address(address: str) -> str | None:
    """Return None if address looks plausible, else an error message."""
    if not (5 <= len(address) <= 300):
        return "❌ Адрес слишком короткий или слишком длинный."
    letters = sum(1 for ch in address if ch.isalpha())
    digits = sum(1 for ch in address if ch.isdigit())
    if letters < 3:
        return "❌ В адресе должно быть название улицы (минимум 3 буквы). Пример: <code>Ленина 19</code>"
    if digits < 1:
        return "❌ Не вижу номера дома. Укажите его цифрами. Пример: <code>Ленина 19</code>"
    return None


@router.message(LeadStates.address)
async def lead_address(message: Message, state: FSMContext):
    address = (message.text or "").strip()
    err = _validate_address(address)
    if err:
        await message.answer(err)
        return
    await state.update_data(address=address)
    await state.set_state(LeadStates.comment)

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➡️ Пропустить", callback_data="lead_skip_comment"))
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"))

    await message.answer(
        "💬 Добавьте комментарий (длина забора, удобное время для звонка, особенности участка) — или пропустите:",
        reply_markup=b.as_markup(),
    )


async def _finalize_lead(bot: Bot, user_id: int, username: str | None, full_name: str | None, state: FSMContext) -> int:
    data = await state.get_data()
    name = data.get("name", "")
    phone = data.get("phone", "")
    address = data.get("address", "")
    comment = data.get("comment", "")
    calc_data = data.get("calc_data", "")

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO leads (user_id, name, phone, address, comment, calc_data, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'Новая', ?)",
        (user_id, name, phone, address, comment, calc_data, datetime.now().isoformat()),
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()

    contact = f"@{username}" if username else (full_name or f"id{user_id}")
    admin_text = (
        "🔥 <b>Новая заявка!</b>\n\n"
        f"<b>№</b> {lead_id}\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Адрес:</b> {address}\n"
        f"<b>Комментарий:</b> {comment or '—'}\n"
        f"<b>Расчёт:</b> {calc_data or '—'}\n"
        f"<b>Клиент:</b> {contact} (id <code>{user_id}</code>)"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)

    await state.clear()
    return lead_id


@router.message(LeadStates.comment)
async def lead_comment(message: Message, state: FSMContext):
    comment = (message.text or "").strip()
    if len(comment) > 1000:
        await message.answer("❌ Слишком длинный комментарий (макс 1000 символов).")
        return
    await state.update_data(comment=comment)
    lead_id = await _finalize_lead(
        message.bot,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        state,
    )
    await message.answer(
        f"✅ <b>Заявка №{lead_id} принята!</b>\n\nМенеджер свяжется с вами в ближайшее время для согласования замера.",
        reply_markup=back_main_kb(),
    )


@router.callback_query(F.data == "lead_skip_comment", LeadStates.comment)
async def lead_skip_comment(call: CallbackQuery, state: FSMContext):
    await state.update_data(comment="")
    lead_id = await _finalize_lead(
        call.bot,
        call.from_user.id,
        call.from_user.username,
        call.from_user.full_name,
        state,
    )
    await safe_edit(
        call,
        f"✅ <b>Заявка №{lead_id} принята!</b>\n\nМенеджер свяжется с вами в ближайшее время для согласования замера.",
        reply_markup=back_main_kb(),
    )
    await call.answer()


# ====================== АДМИН: ЗАЯВКИ ======================
@router.callback_query(F.data.startswith("admin_leads_"))
async def admin_leads(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    try:
        page = max(1, int(call.data.split("_")[-1]))
    except ValueError:
        page = 1
    offset = (page - 1) * LEADS_PER_PAGE
    rows, total = get_leads(offset, LEADS_PER_PAGE)

    if total == 0:
        await safe_edit(call, "📋 Заявок пока нет.", reply_markup=admin_back_kb())
        await call.answer()
        return

    total_pages = (total + LEADS_PER_PAGE - 1) // LEADS_PER_PAGE
    page = min(page, total_pages)

    lines = [f"📋 <b>Заявки</b> — стр. {page}/{total_pages} (всего: {total})\n"]
    b = InlineKeyboardBuilder()
    for lid, name, phone, status, created_at in rows:
        date_str = created_at[:16].replace("T", " ") if created_at else ""
        lines.append(f"#{lid} • {name} • {phone} • <b>{status}</b> • {date_str}")
        b.row(InlineKeyboardButton(text=f"#{lid} {name}", callback_data=f"lead_view_{lid}"))

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_leads_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_leads_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))

    await safe_edit(call, "\n".join(lines), reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("lead_view_"))
async def lead_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        lid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    row = get_lead(lid)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    _id, user_id, name, phone, address, comment, calc_data, status, created_at = row
    text = (
        f"📋 <b>Заявка №{_id}</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Адрес:</b> {address}\n"
        f"<b>Комментарий:</b> {comment or '—'}\n"
        f"<b>Расчёт:</b> {calc_data or '—'}\n"
        f"<b>Клиент tg id:</b> <code>{user_id}</code>\n"
        f"<b>Создана:</b> {created_at[:16].replace('T', ' ') if created_at else ''}"
    )
    b = InlineKeyboardBuilder()
    for s in LEAD_STATUSES:
        if s != status:
            b.row(InlineKeyboardButton(text=f"→ {s}", callback_data=f"lead_status_{_id}_{LEAD_STATUSES.index(s)}"))
    b.row(InlineKeyboardButton(text="🔙 К заявкам", callback_data="admin_leads_1"))
    b.row(InlineKeyboardButton(text="🛠 В админку", callback_data="admin_back"))
    await safe_edit(call, text, reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("lead_status_"))
async def lead_status(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    parts = call.data.split("_")
    try:
        lid = int(parts[2])
        sidx = int(parts[3])
    except (ValueError, IndexError):
        await call.answer()
        return
    if sidx < 0 or sidx >= len(LEAD_STATUSES):
        await call.answer()
        return
    new_status = LEAD_STATUSES[sidx]
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"UPDATE leads SET status = {ph} WHERE id = {ph}", (new_status, lid))
    conn.commit()
    conn.close()
    await call.answer(f"Статус → {new_status}")
    await lead_view(call.model_copy(update={"data": f"lead_view_{lid}"}))


# ====================== АДМИН: РАБОТЫ ======================
@router.callback_query(F.data == "admin_works")
async def admin_works(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM works")
    total = cur.fetchone()[0]
    conn.close()
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить фото", callback_data="work_add"))
    b.row(InlineKeyboardButton(text=f"📋 Список фото ({total})", callback_data="work_list_1"))
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))
    await safe_edit(call, f"📸 <b>Управление работами</b>\n\nВсего фото: <b>{total}</b>", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "work_add")
async def work_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.set_state(AddWorkStates.photo)
    await state.update_data(photos=[])  # Инициализируем список фото
    await safe_edit(
        call, 
        "📸 <b>Добавление фото в галерею</b>\n\n"
        "Отправьте одно или несколько фото.\n"
        "Когда закончите, нажмите кнопку ниже.",
        reply_markup=admin_back_kb()
    )
    await call.answer()


@router.message(AddWorkStates.photo, F.photo)
async def work_add_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    # Получаем текущий список фото
    data = await state.get_data()
    photos = data.get("photos", [])
    
    # Добавляем новое фото
    file_id = message.photo[-1].file_id
    photos.append(file_id)
    
    await state.update_data(photos=photos)
    
    # Показываем кнопки для продолжения или завершения
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"✅ Готово ({len(photos)} фото)", callback_data="work_photos_done"))
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_back"))
    
    await message.answer(
        f"📸 Фото {len(photos)} добавлено.\n\n"
        "Отправьте ещё фото или нажмите «Готово».",
        reply_markup=b.as_markup()
    )


@router.callback_query(F.data == "work_photos_done", AddWorkStates.photo)
async def work_photos_done(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    
    data = await state.get_data()
    photos = data.get("photos", [])
    
    if not photos:
        await call.answer("❌ Вы не добавили ни одного фото", show_alert=True)
        return
    
    await state.set_state(AddWorkStates.caption)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➡️ Без подписи", callback_data="work_skip_caption"))
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_back"))
    
    await call.message.edit_text(
        f"📝 Добавлено {len(photos)} фото.\n\n"
        "Введите общую подпись для всех фото (или пропустите):",
        reply_markup=b.as_markup()
    )
    await call.answer()


@router.message(AddWorkStates.photo)
async def work_add_photo_invalid(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("❌ Это не фото. Пришлите изображение.")


async def _save_work(bot: Bot, chat_id: int, state: FSMContext, caption: str):
    data = await state.get_data()
    photos = data.get("photos", [])
    
    if not photos:
        await state.clear()
        await bot.send_message(chat_id, "❌ Ошибка: фото потерялись. Начните заново.", reply_markup=admin_back_kb())
        return
    
    # Импортируем sync_manager и photo_converter
    from sync_manager import add_work
    from photo_converter import sync_photo_tg_to_vk
    
    # Получаем настройки VK
    vk_token = os.getenv("VK_BOT_TOKEN")
    vk_admins_str = os.getenv("VK_ADMINS", "")
    vk_admin_id = None
    
    if vk_token and vk_admins_str:
        vk_admin_ids = [int(x.strip()) for x in vk_admins_str.split(",") if x.strip()]
        if vk_admin_ids:
            vk_admin_id = vk_admin_ids[0]
    
    # Сохраняем все фото
    saved_count = 0
    synced_count = 0
    
    status_msg = await bot.send_message(
        chat_id,
        f"⏳ Сохранение {len(photos)} фото...",
        reply_markup=admin_back_kb()
    )
    
    for idx, file_id in enumerate(photos, 1):
        try:
            # Синхронизируем фото в VK (если настроен)
            vk_attachment = None
            if vk_admin_id and vk_token:
                logger.info(f"🔄 Синхронизация фото {idx}/{len(photos)} в VK...")
                vk_attachment = sync_photo_tg_to_vk(TOKEN, vk_token, file_id, vk_admin_id)
                if vk_attachment:
                    logger.info(f"✅ Фото {idx} синхронизировано в VK: {vk_attachment}")
                    synced_count += 1
                else:
                    logger.warning(f"⚠️ Не удалось синхронизировать фото {idx} в VK")
            
            # Добавляем работу в общую таблицу
            work_id = add_work(
                file_id=file_id,
                vk_attachment=vk_attachment,
                caption=caption,
                platform="tg"
            )
            saved_count += 1
            
            # Обновляем статус
            if idx % 3 == 0 or idx == len(photos):  # Обновляем каждые 3 фото или в конце
                await status_msg.edit_text(
                    f"⏳ Сохранено {saved_count}/{len(photos)} фото...",
                    reply_markup=admin_back_kb()
                )
        
        except Exception as e:
            logger.error(f"Ошибка добавления фото {idx}: {e}")
    
    # Итоговое сообщение
    if saved_count == len(photos):
        if synced_count > 0:
            msg = f"✅ Все {saved_count} фото добавлены в галерею и синхронизированы с VK!"
        else:
            msg = f"✅ Все {saved_count} фото добавлены в галерею!"
    else:
        msg = f"⚠️ Добавлено {saved_count} из {len(photos)} фото."
    
    await status_msg.edit_text(msg, reply_markup=admin_back_kb())
    await state.clear()


@router.message(AddWorkStates.caption)
async def work_caption(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await _save_work(message.bot, message.chat.id, state, (message.text or "").strip()[:500])


@router.callback_query(F.data == "work_skip_caption", AddWorkStates.caption)
async def work_skip_caption(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await call.answer()
    await _save_work(call.bot, call.message.chat.id, state, "")


@router.callback_query(F.data.startswith("work_list_"))
async def work_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        page = max(1, int(call.data.split("_")[-1]))
    except ValueError:
        page = 1
    offset = (page - 1) * PHOTOS_PER_PAGE
    rows, total = get_works(offset, PHOTOS_PER_PAGE)
    if total == 0:
        await safe_edit(call, "Пока нет загруженных фото.", reply_markup=admin_back_kb())
        await call.answer()
        return
    total_pages = (total + PHOTOS_PER_PAGE - 1) // PHOTOS_PER_PAGE
    page = min(page, total_pages)
    lines = [f"📋 <b>Фото</b> — стр. {page}/{total_pages} (всего: {total})\n"]
    b = InlineKeyboardBuilder()
    for wid, _fid, caption in rows:
        snippet = (caption or "(без подписи)").strip()
        if len(snippet) > 30:
            snippet = snippet[:27] + "…"
        lines.append(f"#{wid}: {snippet}")
        b.row(InlineKeyboardButton(text=f"🗑 Удалить #{wid}", callback_data=f"work_del_{wid}"))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"work_list_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"work_list_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="🔙 К работам", callback_data="admin_works"))
    await safe_edit(call, "\n".join(lines), reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("work_del_"))
async def work_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        wid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"DELETE FROM works WHERE id = {ph}", (wid,))
    conn.commit()
    conn.close()
    await call.answer("Удалено")
    await work_list(call.model_copy(update={"data": "work_list_1"}))


# ====================== АДМИН: ВИДЫ ЗАБОРОВ ======================
@router.callback_query(F.data == "admin_types")
async def admin_types(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    types = get_fence_types()
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить тип", callback_data="type_add"))
    for tid, name, _desc in types:
        b.row(InlineKeyboardButton(text=name, callback_data=f"type_edit_{tid}"))
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))
    await safe_edit(
        call,
        f"🏗 <b>Управление видами заборов</b>\n\nВсего: <b>{len(types)}</b>\nКликните на тип, чтобы изменить или удалить.",
        reply_markup=b.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "type_add")
async def type_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.set_state(AddTypeStates.name)
    await safe_edit(call, "Введите название нового типа забора:", reply_markup=admin_back_kb())
    await call.answer()


@router.message(AddTypeStates.name)
async def type_add_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 60):
        await message.answer("❌ Название должно быть 2–60 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(AddTypeStates.description)
    await message.answer(
        f"Название: <b>{name}</b>\n\nТеперь пришлите описание (можно с HTML-форматированием, до 3000 символов):",
        reply_markup=admin_back_kb(),
    )


@router.message(AddTypeStates.description)
async def type_add_desc(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    desc = (message.html_text or message.text or "").strip()
    if len(desc) > 3000:
        await message.answer("❌ Описание слишком длинное (макс 3000 символов).")
        return
    data = await state.get_data()
    name = data["name"]
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    try:
        cur.execute(
            f"INSERT INTO fence_types (name, description, created_at) VALUES ({ph}, {ph}, {ph})",
            (name, desc, datetime.now().isoformat()),
        )
        # Автоматически добавляем цену по умолчанию (0 руб)
        if DB_TYPE == "postgresql":
            cur.execute(
                f"INSERT INTO prices (fence_type, price_per_m2) VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING",
                (name, 0)
            )
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO prices (fence_type, price_per_m2) VALUES ({ph}, {ph})",
                (name, 0)
            )
        conn.commit()
        await message.answer(
            f"✅ Тип «{name}» добавлен.\n\n"
            "💡 Не забудьте установить цену в разделе «💰 Управление ценами».",
            reply_markup=admin_back_kb()
        )
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            await message.answer("⚠️ Тип с таким названием уже существует.", reply_markup=admin_back_kb())
        else:
            raise
    finally:
        conn.close()
    await state.clear()


@router.callback_query(F.data.startswith("type_edit_"))
async def type_edit_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        tid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    row = get_fence_type(tid)
    if not row:
        await call.answer("Не найдено", show_alert=True)
        return
    _id, name, _desc = row
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"type_rename_{tid}"))
    b.row(InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"type_redesc_{tid}"))
    b.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"type_del_{tid}"))
    b.row(InlineKeyboardButton(text="🔙 К типам", callback_data="admin_types"))
    await safe_edit(call, f"🏗 <b>{name}</b>\n\nЧто меняем?", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("type_rename_"))
async def type_rename(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        tid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    await state.update_data(type_id=tid)
    await state.set_state(EditTypeStates.waiting_name)
    await safe_edit(call, "Введите новое название:", reply_markup=admin_back_kb())
    await call.answer()


@router.message(EditTypeStates.waiting_name)
async def type_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 60):
        await message.answer("❌ Название должно быть 2–60 символов.")
        return
    data = await state.get_data()
    tid = data.get("type_id")
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    try:
        cur.execute(f"UPDATE fence_types SET name = {ph} WHERE id = {ph}", (name, tid))
        conn.commit()
        await message.answer("✅ Название обновлено.", reply_markup=admin_back_kb())
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Такое название уже занято.", reply_markup=admin_back_kb())
    finally:
        conn.close()
    await state.clear()


@router.callback_query(F.data.startswith("type_redesc_"))
async def type_redesc(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        tid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    await state.update_data(type_id=tid)
    await state.set_state(EditTypeStates.waiting_description)
    await safe_edit(call, "Пришлите новое описание (можно с HTML-форматированием):", reply_markup=admin_back_kb())
    await call.answer()


@router.message(EditTypeStates.waiting_description)
async def type_redesc_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    desc = (message.html_text or message.text or "").strip()
    if len(desc) > 3000:
        await message.answer("❌ Слишком длинное (макс 3000 символов).")
        return
    data = await state.get_data()
    tid = data.get("type_id")
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"UPDATE fence_types SET description = {ph} WHERE id = {ph}", (desc, tid))
    conn.commit()
    conn.close()
    await message.answer("✅ Описание обновлено.", reply_markup=admin_back_kb())
    await state.clear()


@router.callback_query(F.data.startswith("type_del_"))
async def type_del(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        tid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"DELETE FROM fence_types WHERE id = {ph}", (tid,))
    conn.commit()
    conn.close()
    await call.answer("Удалено")
    await admin_types(call.model_copy(update={"data": "admin_types"}), state)


# ====================== АДМИН: ОТЗЫВЫ ======================
@router.callback_query(F.data == "admin_reviews")
async def admin_reviews(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    rows, total = get_reviews(0, 100)
    b = InlineKeyboardBuilder()
    pending = get_pending_reviews()
    pending_count = len(pending)

    b.row(InlineKeyboardButton(text="➕ Добавить отзыв", callback_data="review_add"))
    if pending_count > 0:
        b.row(InlineKeyboardButton(
            text=f"🕐 На модерации ({pending_count})",
            callback_data="admin_pending_reviews",
        ))
    lines = [f"⭐ <b>Управление отзывами</b>\n\nОпубликовано: <b>{total}</b>\nНа модерации: <b>{pending_count}</b>\n"]
    for rid, author, text in rows[:20]:
        snippet = text[:40] + ("…" if len(text) > 40 else "")
        lines.append(f"#{rid} <b>{author}</b>: {snippet}")
        b.row(InlineKeyboardButton(text=f"🗑 Удалить #{rid}", callback_data=f"review_del_{rid}"))
    if total > 20:
        lines.append(f"\n…и ещё {total - 20}")
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))
    await safe_edit(call, "\n".join(lines), reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "review_add")
async def review_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.set_state(AddReviewStates.author)
    await safe_edit(call, "Введите имя автора (например, <i>Алексей, Ижевск</i>):", reply_markup=admin_back_kb())
    await call.answer()


@router.message(AddReviewStates.author)
async def review_author(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    author = (message.text or "").strip()
    if not (2 <= len(author) <= 100):
        await message.answer("❌ Имя должно быть 2–100 символов.")
        return
    await state.update_data(author=author)
    await state.set_state(AddReviewStates.text)
    await message.answer("Теперь пришлите текст отзыва (до 2000 символов):", reply_markup=admin_back_kb())


@router.message(AddReviewStates.text)
async def review_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not (10 <= len(text) <= 2000):
        await message.answer("❌ Отзыв должен быть 10–2000 символов.")
        return
    data = await state.get_data()
    author = data["author"]
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reviews (author, text, created_at) VALUES (?, ?, ?)",
        (author, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer("✅ Отзыв добавлен.", reply_markup=admin_back_kb())
    await state.clear()


@router.callback_query(F.data.startswith("review_del_"))
async def review_delete(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        rid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"DELETE FROM reviews WHERE id = {ph}", (rid,))
    conn.commit()
    conn.close()
    await call.answer("Удалено")
    await admin_reviews(call.model_copy(update={"data": "admin_reviews"}), state)


# ====================== АДМИН: МОДЕРАЦИЯ ОТЗЫВОВ ======================
@router.callback_query(F.data == "admin_pending_reviews")
async def admin_pending_reviews_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    pending = get_pending_reviews()
    if not pending:
        await safe_edit(
            call,
            "🕐 <b>Модерация отзывов</b>\n\nНет отзывов, ожидающих проверки.",
            reply_markup=admin_back_kb(),
        )
        await call.answer()
        return

    lines = [f"🕐 <b>Отзывы на модерации</b> ({len(pending)})\n"]
    b = InlineKeyboardBuilder()
    for rid, author, text, user_id, created_at in pending[:20]:
        snippet = text[:40] + ("…" if len(text) > 40 else "")
        date_str = created_at[:16].replace("T", " ") if created_at else ""
        lines.append(f"#{rid} <b>{author}</b>: {snippet}\n<i>{date_str}</i>")
        b.row(
            InlineKeyboardButton(text=f"✅ #{rid}", callback_data=f"review_approve_{rid}"),
            InlineKeyboardButton(text=f"❌ #{rid}", callback_data=f"review_reject_{rid}"),
        )
        b.row(InlineKeyboardButton(text=f"👁 Подробнее #{rid}", callback_data=f"review_detail_{rid}"))
    if len(pending) > 20:
        lines.append(f"\n…и ещё {len(pending) - 20}")
    b.row(InlineKeyboardButton(text="🔙 К отзывам", callback_data="admin_reviews"))
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))

    await safe_edit(call, "\n".join(lines), reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("review_detail_"))
async def review_detail(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        rid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, author, text, user_id, created_at, approved FROM reviews WHERE id = ?",
        (rid,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        await call.answer("Отзыв не найден", show_alert=True)
        return
    _id, author, text, user_id, created_at, approved = row
    status = "Опубликован" if approved else "Ожидает модерации"
    date_str = created_at[:16].replace("T", " ") if created_at else ""
    msg = (
        f"⭐ <b>Отзыв #{_id}</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Автор:</b> {author}\n"
        f"<b>Telegram ID:</b> <code>{user_id or '—'}</code>\n"
        f"<b>Дата:</b> {date_str}\n\n"
        f"<b>Текст:</b>\n{text}"
    )
    b = InlineKeyboardBuilder()
    if not approved:
        b.row(
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"review_approve_{rid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"review_reject_{rid}"),
        )
    b.row(InlineKeyboardButton(text="🔙 К модерации", callback_data="admin_pending_reviews"))
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))
    await safe_edit(call, msg, reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("review_approve_"))
async def review_approve_handler(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        rid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"SELECT author, user_id FROM reviews WHERE id = {ph} AND approved = 0", (rid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Отзыв уже одобрен или не найден", show_alert=True)
        return
    author, user_id = row
    cur.execute(f"UPDATE reviews SET approved = 1 WHERE id = {ph}", (rid,))
    conn.commit()
    conn.close()
    await call.answer(f"Отзыв #{rid} одобрен!")

    if user_id:
        try:
            await call.bot.send_message(
                user_id,
                f"✅ Ваш отзыв был опубликован! Спасибо, <b>{author}</b>!",
            )
        except Exception as e:
            logger.warning("Failed to notify user %s about approved review: %s", user_id, e)

    try:
        await safe_edit(
            call,
            f"✅ Отзыв #{rid} от <b>{author}</b> одобрен и опубликован.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🕐 К модерации", callback_data="admin_pending_reviews"),
                InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"),
            ).as_markup(),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("review_reject_"))
async def review_reject_handler(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        rid = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"SELECT author, user_id FROM reviews WHERE id = {ph} AND approved = 0", (rid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Отзыв уже обработан или не найден", show_alert=True)
        return
    author, user_id = row
    cur.execute(f"DELETE FROM reviews WHERE id = {ph}", (rid,))
    conn.commit()
    conn.close()
    await call.answer(f"Отзыв #{rid} отклонён")

    if user_id:
        try:
            await call.bot.send_message(
                user_id,
                "К сожалению, ваш отзыв не прошёл модерацию. "
                "Попробуйте написать новый, следуя правилам.",
            )
        except Exception as e:
            logger.warning("Failed to notify user %s about rejected review: %s", user_id, e)

    try:
        await safe_edit(
            call,
            f"❌ Отзыв #{rid} от <b>{author}</b> отклонён и удалён.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🕐 К модерации", callback_data="admin_pending_reviews"),
                InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"),
            ).as_markup(),
        )
    except Exception:
        pass


# ====================== АДМИН: УПРАВЛЕНИЕ ЦЕНАМИ ======================
@router.callback_query(F.data == "admin_prices")
async def admin_prices_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    prices = get_prices_dict()
    text = "💰 <b>Текущие цены (руб/м²)</b>\n\n"
    for t, p in prices.items():
        text += f"• {t}: <b>{p}</b>\n"

    b = InlineKeyboardBuilder()
    types_list = list(prices.keys())
    for idx, t in enumerate(types_list):
        b.row(InlineKeyboardButton(text=f"✏️ {t}", callback_data=f"price_edit_{idx}"))
    b.row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_back"))

    await safe_edit(call, text, reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("price_edit_"))
async def price_edit_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    try:
        idx = int(call.data.split("_")[-1])
    except ValueError:
        await call.answer()
        return
    prices = get_prices_dict()
    types_list = list(prices.keys())
    if idx < 0 or idx >= len(types_list):
        await call.answer("Не найдено", show_alert=True)
        return
    fence_type = types_list[idx]
    await state.update_data(fence_type=fence_type)
    await state.set_state(EditPriceStates.waiting_price)
    await safe_edit(call, f"Введите новую цену для <b>{fence_type}</b> (₽/м²):", reply_markup=admin_back_kb())
    await call.answer()


@router.message(EditPriceStates.waiting_price)
async def save_new_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        new_price = int((message.text or "").strip())
        if new_price < 0 or new_price > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число (0–1 000 000).")
        return
    data = await state.get_data()
    fence_type = data["fence_type"]

    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    cur.execute(f"UPDATE prices SET price_per_m2 = {ph} WHERE fence_type = {ph}", (new_price, fence_type))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Цена обновлена: {fence_type} → <b>{new_price} ₽/м²</b>", reply_markup=admin_back_kb())
    await state.clear()


# ====================== АДМИН: СТАТИСТИКА ======================
@router.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    users_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM leads")
    leads_total = cur.fetchone()[0]
    cur.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
    by_status = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM works")
    works_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 1")
    reviews_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 0")
    reviews_pending = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM fence_types")
    types_total = cur.fetchone()[0]
    conn.close()

    lines = ["📊 <b>Статистика</b>\n"]
    lines.append(f"👥 Пользователей: <b>{users_total}</b>")
    lines.append(f"📋 Заявок всего: <b>{leads_total}</b>")
    for s in LEAD_STATUSES:
        lines.append(f"  • {s}: <b>{by_status.get(s, 0)}</b>")
    lines.append(f"📸 Фото в галерее: <b>{works_total}</b>")
    lines.append(f"⭐ Отзывов: <b>{reviews_total}</b>")
    if reviews_pending > 0:
        lines.append(f"  • На модерации: <b>{reviews_pending}</b>")
    lines.append(f"🏗 Видов заборов: <b>{types_total}</b>")

    await safe_edit(call, "\n".join(lines), reply_markup=admin_back_kb())
    await call.answer()


# ====================== АДМИН: РАССЫЛКА ======================
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.set_state(BroadcastStates.waiting_text)
    await safe_edit(
        call,
        "📢 <b>Рассылка</b>\n\nПришлите текст сообщения для рассылки всем пользователям бота (можно с HTML-форматированием):",
        reply_markup=admin_back_kb(),
    )
    await call.answer()


@router.message(BroadcastStates.waiting_text)
async def admin_broadcast_preview(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.html_text or message.text or "").strip()
    if not (1 <= len(text) <= 3500):
        await message.answer("❌ Сообщение должно быть 1–3500 символов.")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(BroadcastStates.confirm)

    user_count = len(get_all_user_ids())
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"✅ Отправить ({user_count})", callback_data="broadcast_send"))
    b.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_back"))
    await message.answer(
        f"<b>Превью рассылки:</b>\n\n{text}\n\n———\nПолучат: <b>{user_count}</b> пользователей",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "broadcast_send", BroadcastStates.confirm)
async def admin_broadcast_send(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()
    user_ids = get_all_user_ids()
    await safe_edit(call, f"Отправляю {len(user_ids)} пользователям…", reply_markup=None)
    await call.answer()

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await call.bot.send_message(uid, text)
            sent += 1
        except Exception as e:
            logger.info("Broadcast failed for %s: %s", uid, e)
            failed += 1
        await asyncio.sleep(0.05)

    await call.bot.send_message(
        call.message.chat.id,
        f"✅ Рассылка завершена.\nОтправлено: <b>{sent}</b>\nОшибок: <b>{failed}</b>",
        reply_markup=admin_back_kb(),
    )


# ====================== АДМИН: ЭКСПОРТ ======================
@router.callback_query(F.data == "admin_export")
async def admin_export(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    await state.clear()
    await call.answer("Готовлю файл…")

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, name, phone, address, comment, calc_data, status, created_at "
        "FROM leads ORDER BY id DESC"
    )
    rows = cur.fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Заявки"
    headers = ["№", "Telegram ID", "Имя", "Телефон", "Адрес", "Комментарий", "Расчёт", "Статус", "Создана"]
    ws.append(headers)
    for row in rows:
        ws.append(list(row))

    for col_idx, header in enumerate(headers, start=1):
        max_len = max([len(str(header))] + [len(str(r[col_idx - 1] or "")) for r in rows] or [10])
        ws.column_dimensions[chr(64 + col_idx)].width = min(max_len + 2, 60)

    fd, tmp_path = tempfile.mkstemp(prefix="leads_", suffix=".xlsx")
    os.close(fd)
    wb.save(tmp_path)

    try:
        filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        await call.bot.send_document(
            call.message.chat.id,
            FSInputFile(tmp_path, filename=filename),
            caption=f"📥 Экспорт заявок: <b>{len(rows)}</b>",
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    await call.bot.send_message(call.message.chat.id, "Готово.", reply_markup=admin_back_kb())


# ====================== ЗАПУСК ======================
async def main():
    init_db()
    sync_prices_with_types()  # Синхронизируем цены с типами заборов

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # удаляем webhook и старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("🚀 Бот запущен успешно!")

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())
