import asyncio
import logging
import re
import sqlite3
import os
import tempfile
from datetime import datetime

import openpyxl
from vkbottle import (
    BaseStateGroup,
    Bot,
    Callback,
    DocMessagesUploader,
    GroupEventType,
    Keyboard,
    KeyboardButtonColor,
)
from vkbottle.bot import Message, MessageEvent

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("VK_BOT_TOKEN", "")
VK_ADMINS_STR = os.getenv("VK_ADMINS", "")
VK_ADMINS = [int(x.strip()) for x in VK_ADMINS_STR.split(",") if x.strip()]

if not TOKEN:
    raise ValueError("VK_BOT_TOKEN не найден! Добавь его в Variables на Railway.")

PHOTOS_PER_PAGE = 6
LEADS_PER_PAGE = 5
REVIEWS_PER_PAGE = 3
LEAD_STATUSES = ["Новая", "В работе", "Закрыта", "Отказ"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("DB_PATH", "fence_bot.db")

bot = Bot(token=TOKEN)


# ====================== УТИЛИТЫ ======================
def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


# ====================== БАЗА ДАННЫХ ======================
def _connect():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            created_at TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            phone TEXT,
            address TEXT,
            comment TEXT,
            calc_data TEXT,
            status TEXT DEFAULT 'Новая',
            created_at TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT UNIQUE,
            caption TEXT,
            added_at TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS prices (
            fence_type TEXT PRIMARY KEY,
            price_per_m2 INTEGER
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS fence_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            created_at TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT,
            text TEXT,
            created_at TEXT,
            approved INTEGER DEFAULT 1,
            user_id INTEGER
        )"""
    )

    # Миграции
    for col_sql in [
        "ALTER TABLE reviews ADD COLUMN approved INTEGER DEFAULT 1",
        "ALTER TABLE reviews ADD COLUMN user_id INTEGER",
        "ALTER TABLE leads ADD COLUMN platform TEXT DEFAULT 'tg'",
    ]:
        try:
            cur.execute(col_sql)
        except sqlite3.OperationalError:
            pass

    # VK-специфичные таблицы
    cur.execute(
        """CREATE TABLE IF NOT EXISTS vk_users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS vk_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attachment TEXT UNIQUE,
            caption TEXT,
            added_at TEXT
        )"""
    )

    # Дефолтные цены
    default_prices = {
        "Профнастил": 2500,
        "Металлический штакетник": 3200,
        "3D-сетка": 1800,
        "Рабица": 1200,
        "Дерево": 2800,
        "Комбинированный": 3500,
    }
    for t, p in default_prices.items():
        cur.execute("INSERT OR IGNORE INTO prices VALUES (?, ?)", (t, p))

    # Дефолтные виды заборов
    default_types = [
        (
            "Профнастил",
            "Забор из профнастила\n\n"
            "Самый популярный вариант. Лист профилированной стали с полимерным покрытием.\n\n"
            "- Срок службы: 20-30 лет\n"
            "- Глухой, защищает от пыли и любопытных глаз\n"
            "- Большой выбор цветов по RAL\n"
            "- Не требует ухода",
        ),
        (
            "Металлический штакетник",
            "Металлический штакетник (евроштакетник)\n\n"
            "Стальные планки с полимерным покрытием.\n\n"
            "- Срок службы: 25-30 лет\n"
            "- Полупрозрачный или глухой монтаж\n"
            "- Аккуратный современный вид\n"
            "- Хорошо продувается ветром",
        ),
        (
            "3D-сетка",
            "3D-сетка (сварные панели)\n\n"
            "Жёсткие сварные панели из прутка с полимерным покрытием.\n\n"
            "- Срок службы: 15-25 лет\n"
            "- Не парусит, выдерживает ветровые нагрузки\n"
            "- Быстрый монтаж\n"
            "- Пропускает свет на участок",
        ),
        (
            "Рабица",
            "Забор из сетки-рабицы\n\n"
            "Бюджетное решение для дачи, садового участка.\n\n"
            "- Срок службы: 10-15 лет\n"
            "- Самый доступный вариант\n"
            "- Пропускает свет\n"
            "- Можно с оцинковкой или ПВХ-покрытием",
        ),
        (
            "Дерево",
            "Деревянный забор\n\n"
            "Классика и натуральный вид.\n\n"
            "- Срок службы: 10-20 лет (с обработкой)\n"
            "- Тёплый, «домашний» внешний вид\n"
            "- Возможна покраска в любой цвет\n"
            "- Требует периодического обновления покрытия",
        ),
        (
            "Комбинированный",
            "Комбинированный забор\n\n"
            "Сочетание кирпичных/каменных столбов с пролётами из профнастила, штакетника или ковки.\n\n"
            "- Срок службы: 40+ лет\n"
            "- Самый презентабельный вид\n"
            "- Высокая прочность и шумоизоляция\n"
            "- Подходит для частных домов",
        ),
    ]
    now = datetime.now().isoformat()
    for name, desc in default_types:
        cur.execute(
            "INSERT OR IGNORE INTO fence_types (name, description, created_at) VALUES (?, ?, ?)",
            (name, desc, now),
        )

    # Дефолтные отзывы
    cur.execute("SELECT COUNT(*) FROM reviews")
    if cur.fetchone()[0] == 0:
        default_reviews = [
            (
                "Алексей, Ижевск",
                "Ставили забор из профнастила вокруг участка 12 соток. Замерщик приехал на следующий день, всё посчитали без скрытых платежей. Бригада отработала за 2 дня — аккуратно, ровно, столбы строго по уровню. Спасибо!",
            ),
            (
                "Марина, пос. Воткинский",
                "Заказывали евроштакетник графитового цвета. Очень довольны, выглядит как на картинке. Ребята вежливые, всё убрали за собой.",
            ),
            (
                "Сергей, Завьялово",
                "Делали комбинированный забор: кирпичные столбы + профнастил. Цена честная, по итогу как договаривались. Соседи уже спрашивали контакты.",
            ),
        ]
        for author, text in default_reviews:
            cur.execute(
                "INSERT INTO reviews (author, text, created_at) VALUES (?, ?, ?)",
                (author, text, now),
            )

    conn.commit()
    conn.close()


# ====================== ЗАПРОСЫ К БД ======================
def get_prices_dict():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT fence_type, price_per_m2 FROM prices ORDER BY fence_type")
    prices = dict(cur.fetchall())
    conn.close()
    return prices


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
    cur.execute("SELECT id, name, description FROM fence_types WHERE id = ?", (type_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_reviews(offset: int, limit: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 1")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT id, author, text FROM reviews WHERE approved = 1 ORDER BY id DESC LIMIT ? OFFSET ?",
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


def get_vk_works(offset: int, limit: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vk_works")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT id, attachment, caption FROM vk_works ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return rows, total


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
        "SELECT id, user_id, name, phone, address, comment, calc_data, status, created_at, platform "
        "FROM leads WHERE id = ?",
        (lead_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_all_vk_user_ids():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM vk_users")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


# ====================== FSM ======================
class CalcStates(BaseStateGroup):
    LENGTH = "calc_length"
    HEIGHT = "calc_height"


class LeadStates(BaseStateGroup):
    NAME = "lead_name"
    PHONE = "lead_phone"
    ADDRESS = "lead_address"
    COMMENT = "lead_comment"


class AddWorkStates(BaseStateGroup):
    PHOTO = "work_photo"
    CAPTION = "work_caption"


class EditPriceStates(BaseStateGroup):
    WAITING = "price_waiting"


class AddTypeStates(BaseStateGroup):
    NAME = "type_name"
    DESC = "type_desc"


class EditTypeStates(BaseStateGroup):
    NAME = "type_edit_name"
    DESC = "type_edit_desc"


class AddReviewStates(BaseStateGroup):
    AUTHOR = "review_author"
    TEXT = "review_text"


class SubmitReviewStates(BaseStateGroup):
    AUTHOR = "submit_author"
    TEXT = "submit_text"


class BroadcastStates(BaseStateGroup):
    WAITING = "broadcast_text"
    CONFIRM = "broadcast_confirm"


# ====================== ХЕЛПЕРЫ ======================
def is_admin(user_id: int) -> bool:
    return user_id in VK_ADMINS


def main_menu_kb():
    return (
        Keyboard(inline=True)
        .add(Callback("🧮 Рассчитать стоимость", payload={"cmd": "calc_start"}))
        .row()
        .add(Callback("📸 Наши работы", payload={"cmd": "works", "p": 1}))
        .row()
        .add(Callback("🏗 Виды заборов", payload={"cmd": "types"}))
        .row()
        .add(Callback("💰 Цены", payload={"cmd": "prices"}))
        .row()
        .add(Callback("⭐ Отзывы", payload={"cmd": "reviews", "p": 1}))
        .row()
        .add(Callback("📍 Заказать замер", payload={"cmd": "lead_start"}))
        .get_json()
    )


def back_main_kb():
    return (
        Keyboard(inline=True)
        .add(Callback("🏠 Главное меню", payload={"cmd": "main"}))
        .get_json()
    )


def cancel_kb():
    return (
        Keyboard(inline=True)
        .add(Callback("❌ Отмена", payload={"cmd": "cancel"}))
        .get_json()
    )


def admin_menu_kb():
    return (
        Keyboard(inline=True)
        .add(Callback("📋 Все заявки", payload={"cmd": "admin_leads", "p": 1}))
        .row()
        .add(Callback("📸 Работы", payload={"cmd": "admin_works"}))
        .add(Callback("🏗 Виды", payload={"cmd": "admin_types"}))
        .row()
        .add(Callback("⭐ Отзывы", payload={"cmd": "admin_reviews"}))
        .add(Callback("💰 Цены", payload={"cmd": "admin_prices"}))
        .row()
        .add(Callback("📊 Стат.", payload={"cmd": "admin_stats"}))
        .add(Callback("📢 Рассылка", payload={"cmd": "admin_broadcast"}))
        .row()
        .add(Callback("📥 Excel", payload={"cmd": "admin_export"}))
        .add(Callback("❌ Закрыть", payload={"cmd": "admin_close"}))
        .get_json()
    )


def admin_back_kb():
    return (
        Keyboard(inline=True)
        .add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
        .get_json()
    )


async def _clear_state(peer_id: int):
    try:
        await bot.state_dispenser.delete(peer_id)
    except KeyError:
        pass


def _parse_positive_float(s: str):
    try:
        v = float(s.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    if v <= 0 or v > 10000:
        return None
    return v


def _validate_address(address: str):
    if not (5 <= len(address) <= 300):
        return "❌ Адрес слишком короткий или слишком длинный."
    letters = sum(1 for ch in address if ch.isalpha())
    digits = sum(1 for ch in address if ch.isdigit())
    if letters < 3:
        return "❌ В адресе должно быть название улицы (минимум 3 буквы). Пример: Ленина 19"
    if digits < 1:
        return "❌ Не вижу номера дома. Укажите его цифрами. Пример: Ленина 19"
    return None


MAIN_TEXT = "🔨 ЗАБОРЫ ПОД КЛЮЧ В ИЖЕВСКЕ\n\nКачественно • Быстро • С гарантией\nБесплатный выезд замерщика"


# ====================== ОБРАБОТЧИКИ СООБЩЕНИЙ ======================

# --- Старт ---
@bot.on.private_message(payload={"command": "start"})
async def start_payload(message: Message):
    await _clear_state(message.peer_id)
    conn = _connect()
    cur = conn.cursor()
    user_info = await bot.api.users.get(user_ids=[message.from_id])
    first = user_info[0].first_name if user_info else ""
    last = user_info[0].last_name if user_info else ""
    cur.execute(
        "INSERT OR REPLACE INTO vk_users VALUES (?,?,?,?)",
        (message.from_id, first, last, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer(MAIN_TEXT, keyboard=main_menu_kb())


@bot.on.private_message(text=["начать", "Начать", "/start", "start", "меню", "Меню"])
async def start_text(message: Message):
    await _clear_state(message.peer_id)
    conn = _connect()
    cur = conn.cursor()
    user_info = await bot.api.users.get(user_ids=[message.from_id])
    first = user_info[0].first_name if user_info else ""
    last = user_info[0].last_name if user_info else ""
    cur.execute(
        "INSERT OR REPLACE INTO vk_users VALUES (?,?,?,?)",
        (message.from_id, first, last, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer(MAIN_TEXT, keyboard=main_menu_kb())


# --- Админ ---
@bot.on.private_message(text=["админ", "Админ", "/admin", "admin"])
async def admin_cmd(message: Message):
    if not is_admin(message.from_id):
        return
    await _clear_state(message.peer_id)
    await message.answer("🛠 Админ-панель", keyboard=admin_menu_kb())


# --- Калькулятор: длина ---
@bot.on.private_message(state=CalcStates.LENGTH)
async def calc_length_handler(message: Message):
    v = _parse_positive_float(message.text)
    if v is None:
        await message.answer("❌ Введите положительное число (метры). Пример: 25")
        return
    await bot.state_dispenser.set(message.peer_id, CalcStates.HEIGHT, length=v)
    await message.answer(
        f"Длина: {v} м\n\nТеперь введите высоту забора в метрах (обычно 1.5–2.5):",
        keyboard=cancel_kb(),
    )


# --- Калькулятор: высота ---
@bot.on.private_message(state=CalcStates.HEIGHT)
async def calc_height_handler(message: Message):
    v = _parse_positive_float(message.text)
    if v is None or v > 10:
        await message.answer("❌ Введите положительное число (метры). Пример: 2")
        return
    data = message.state_peer.payload or {}
    length = data.get("length", 0)
    prices = get_prices_dict()
    if not prices:
        await _clear_state(message.peer_id)
        await message.answer(
            "❌ В системе пока не настроены цены. Свяжитесь с менеджером.",
            keyboard=back_main_kb(),
        )
        return
    types_list = list(prices.keys())
    await bot.state_dispenser.set(
        message.peer_id, CalcStates.HEIGHT, length=length, height=v, price_types=types_list
    )
    kb = Keyboard(inline=True)
    for idx, t in enumerate(types_list):
        if idx > 0:
            kb.row()
        kb.add(Callback(f"{t} — {prices[t]} ₽/м²", payload={"cmd": "calc_type", "i": idx}))
    kb.row()
    kb.add(Callback("❌ Отмена", payload={"cmd": "cancel"}))
    await message.answer(f"Высота: {v} м\n\nВыберите материал забора:", keyboard=kb.get_json())


# --- Заявка: имя ---
@bot.on.private_message(state=LeadStates.NAME)
async def lead_name_handler(message: Message):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("❌ Имя должно быть от 2 до 100 символов.")
        return
    data = message.state_peer.payload or {}
    await bot.state_dispenser.set(message.peer_id, LeadStates.PHONE, name=name, **{k: v for k, v in data.items() if k != "name"})
    await message.answer(
        f"Приятно познакомиться, {name}!\n\nВведите ваш номер телефона:",
        keyboard=cancel_kb(),
    )


# --- Заявка: телефон ---
@bot.on.private_message(state=LeadStates.PHONE)
async def lead_phone_handler(message: Message):
    phone = (message.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        await message.answer("❌ Введите корректный номер (минимум 10 цифр).")
        return
    data = message.state_peer.payload or {}
    await bot.state_dispenser.set(message.peer_id, LeadStates.ADDRESS, phone=phone, **{k: v for k, v in data.items() if k != "phone"})
    await message.answer("📍 Укажите адрес объекта (город, улица, дом):", keyboard=cancel_kb())


# --- Заявка: адрес ---
@bot.on.private_message(state=LeadStates.ADDRESS)
async def lead_address_handler(message: Message):
    address = (message.text or "").strip()
    err = _validate_address(address)
    if err:
        await message.answer(err)
        return
    data = message.state_peer.payload or {}
    await bot.state_dispenser.set(message.peer_id, LeadStates.COMMENT, address=address, **{k: v for k, v in data.items() if k != "address"})
    kb = (
        Keyboard(inline=True)
        .add(Callback("➡️ Пропустить", payload={"cmd": "lead_skip_comment"}))
        .row()
        .add(Callback("❌ Отмена", payload={"cmd": "cancel"}))
        .get_json()
    )
    await message.answer(
        "💬 Добавьте комментарий (длина забора, удобное время для звонка) — или пропустите:",
        keyboard=kb,
    )


# --- Заявка: комментарий ---
@bot.on.private_message(state=LeadStates.COMMENT)
async def lead_comment_handler(message: Message):
    comment = (message.text or "").strip()
    if len(comment) > 1000:
        await message.answer("❌ Слишком длинный комментарий (макс 1000 символов).")
        return
    data = message.state_peer.payload or {}
    await _finalize_lead(message.peer_id, message.from_id, data, comment)


async def _finalize_lead(peer_id: int, user_id: int, data: dict, comment: str):
    name = data.get("name", "")
    phone = data.get("phone", "")
    address = data.get("address", "")
    calc_data = data.get("calc_data", "")

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO leads (user_id, name, phone, address, comment, calc_data, status, created_at, platform) "
        "VALUES (?, ?, ?, ?, ?, ?, 'Новая', ?, 'vk')",
        (user_id, name, phone, address, comment, calc_data, datetime.now().isoformat()),
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()

    await _clear_state(peer_id)
    await bot.api.messages.send(
        peer_id=peer_id,
        message=f"✅ Заявка №{lead_id} принята!\n\nМенеджер свяжется с вами в ближайшее время для согласования замера.",
        keyboard=back_main_kb(),
        random_id=0,
    )

    admin_text = (
        f"🔥 НОВАЯ ЗАЯВКА (VK)!\n\n"
        f"№ {lead_id}\n"
        f"Имя: {name}\n"
        f"Телефон: {phone}\n"
        f"Адрес: {address}\n"
        f"Комментарий: {comment or '—'}\n"
        f"Расчёт: {calc_data or '—'}\n"
        f"VK id: {user_id}"
    )
    for admin_id in VK_ADMINS:
        try:
            await bot.api.messages.send(peer_id=admin_id, message=admin_text, random_id=0)
        except Exception as e:
            logger.warning("Failed to notify VK admin %s: %s", admin_id, e)

    return lead_id


# --- Добавление работ (фото) ---
@bot.on.private_message(state=AddWorkStates.PHOTO)
async def work_photo_handler(message: Message):
    if not is_admin(message.from_id):
        return
    if not message.attachments:
        await message.answer("❌ Пришлите фото.")
        return
    photo = None
    for att in message.attachments:
        if att.photo:
            photo = att.photo
            break
    if not photo:
        await message.answer("❌ Это не фото. Пришлите изображение.")
        return
    attachment = f"photo{photo.owner_id}_{photo.id}"
    if photo.access_key:
        attachment += f"_{photo.access_key}"
    await bot.state_dispenser.set(message.peer_id, AddWorkStates.CAPTION, attachment=attachment)
    kb = (
        Keyboard(inline=True)
        .add(Callback("➡️ Без подписи", payload={"cmd": "work_skip_caption"}))
        .row()
        .add(Callback("❌ Отмена", payload={"cmd": "admin_back"}))
        .get_json()
    )
    await message.answer("Введите подпись к фото (или пропустите):", keyboard=kb)


@bot.on.private_message(state=AddWorkStates.CAPTION)
async def work_caption_handler(message: Message):
    if not is_admin(message.from_id):
        return
    data = message.state_peer.payload or {}
    attachment = data.get("attachment")
    caption = (message.text or "").strip()[:500]
    await _save_vk_work(message.peer_id, attachment, caption)


async def _save_vk_work(peer_id: int, attachment: str, caption: str):
    if not attachment:
        await _clear_state(peer_id)
        await bot.api.messages.send(
            peer_id=peer_id,
            message="❌ Ошибка: фото потерялось. Начните заново.",
            keyboard=admin_back_kb(),
            random_id=0,
        )
        return
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO vk_works (attachment, caption, added_at) VALUES (?, ?, ?)",
            (attachment, caption, datetime.now().isoformat()),
        )
        conn.commit()
        msg = "✅ Фото добавлено в галерею."
    except sqlite3.IntegrityError:
        msg = "⚠️ Это фото уже было добавлено."
    finally:
        conn.close()
    await _clear_state(peer_id)
    await bot.api.messages.send(peer_id=peer_id, message=msg, keyboard=admin_back_kb(), random_id=0)


# --- Редактирование цен ---
@bot.on.private_message(state=EditPriceStates.WAITING)
async def price_edit_handler(message: Message):
    if not is_admin(message.from_id):
        return
    try:
        new_price = int((message.text or "").strip())
        if new_price < 0 or new_price > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число (0–1 000 000).")
        return
    data = message.state_peer.payload or {}
    fence_type = data.get("fence_type", "")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE prices SET price_per_m2 = ? WHERE fence_type = ?", (new_price, fence_type))
    conn.commit()
    conn.close()
    await _clear_state(message.peer_id)
    await message.answer(f"✅ Цена обновлена: {fence_type} → {new_price} ₽/м²", keyboard=admin_back_kb())


# --- Добавление типа забора ---
@bot.on.private_message(state=AddTypeStates.NAME)
async def type_add_name_handler(message: Message):
    if not is_admin(message.from_id):
        return
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 60):
        await message.answer("❌ Название должно быть 2–60 символов.")
        return
    await bot.state_dispenser.set(message.peer_id, AddTypeStates.DESC, type_name=name)
    await message.answer(
        f"Название: {name}\n\nТеперь пришлите описание (до 3000 символов):",
        keyboard=admin_back_kb(),
    )


@bot.on.private_message(state=AddTypeStates.DESC)
async def type_add_desc_handler(message: Message):
    if not is_admin(message.from_id):
        return
    desc = (message.text or "").strip()
    if len(desc) > 3000:
        await message.answer("❌ Описание слишком длинное (макс 3000 символов).")
        return
    data = message.state_peer.payload or {}
    name = data.get("type_name", "")
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO fence_types (name, description, created_at) VALUES (?, ?, ?)",
            (name, desc, datetime.now().isoformat()),
        )
        conn.commit()
        await message.answer(f"✅ Тип «{name}» добавлен.", keyboard=admin_back_kb())
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Тип с таким названием уже существует.", keyboard=admin_back_kb())
    finally:
        conn.close()
    await _clear_state(message.peer_id)


# --- Редактирование типа: название ---
@bot.on.private_message(state=EditTypeStates.NAME)
async def type_rename_handler(message: Message):
    if not is_admin(message.from_id):
        return
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 60):
        await message.answer("❌ Название должно быть 2–60 символов.")
        return
    data = message.state_peer.payload or {}
    tid = data.get("type_id")
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE fence_types SET name = ? WHERE id = ?", (name, tid))
        conn.commit()
        await message.answer("✅ Название обновлено.", keyboard=admin_back_kb())
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Такое название уже занято.", keyboard=admin_back_kb())
    finally:
        conn.close()
    await _clear_state(message.peer_id)


# --- Редактирование типа: описание ---
@bot.on.private_message(state=EditTypeStates.DESC)
async def type_redesc_handler(message: Message):
    if not is_admin(message.from_id):
        return
    desc = (message.text or "").strip()
    if len(desc) > 3000:
        await message.answer("❌ Слишком длинное (макс 3000 символов).")
        return
    data = message.state_peer.payload or {}
    tid = data.get("type_id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE fence_types SET description = ? WHERE id = ?", (desc, tid))
    conn.commit()
    conn.close()
    await _clear_state(message.peer_id)
    await message.answer("✅ Описание обновлено.", keyboard=admin_back_kb())


# --- Админ: добавление отзыва ---
@bot.on.private_message(state=AddReviewStates.AUTHOR)
async def review_author_handler(message: Message):
    if not is_admin(message.from_id):
        return
    author = (message.text or "").strip()
    if not (2 <= len(author) <= 100):
        await message.answer("❌ Имя должно быть 2–100 символов.")
        return
    await bot.state_dispenser.set(message.peer_id, AddReviewStates.TEXT, author=author)
    await message.answer("Теперь пришлите текст отзыва (до 2000 символов):", keyboard=admin_back_kb())


@bot.on.private_message(state=AddReviewStates.TEXT)
async def review_text_handler(message: Message):
    if not is_admin(message.from_id):
        return
    text = (message.text or "").strip()
    if not (10 <= len(text) <= 2000):
        await message.answer("❌ Отзыв должен быть 10–2000 символов.")
        return
    data = message.state_peer.payload or {}
    author = data.get("author", "")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reviews (author, text, created_at, approved) VALUES (?, ?, ?, 1)",
        (author, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await _clear_state(message.peer_id)
    await message.answer("✅ Отзыв добавлен.", keyboard=admin_back_kb())


# --- Пользователь: отправка отзыва ---
@bot.on.private_message(state=SubmitReviewStates.AUTHOR)
async def submit_author_handler(message: Message):
    author = (message.text or "").strip()
    if not (2 <= len(author) <= 100):
        await message.answer("❌ Имя должно быть от 2 до 100 символов.")
        return
    await bot.state_dispenser.set(message.peer_id, SubmitReviewStates.TEXT, author=author)
    await message.answer(
        f"Приятно познакомиться, {author}!\n\nТеперь напишите ваш отзыв (от 10 до 2000 символов):",
        keyboard=cancel_kb(),
    )


@bot.on.private_message(state=SubmitReviewStates.TEXT)
async def submit_text_handler(message: Message):
    text = (message.text or "").strip()
    if not (10 <= len(text) <= 2000):
        await message.answer("❌ Отзыв должен быть от 10 до 2000 символов.")
        return
    data = message.state_peer.payload or {}
    author = data.get("author", "")
    user_id = message.from_id

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reviews (author, text, created_at, approved, user_id) VALUES (?, ?, ?, 0, ?)",
        (author, text, datetime.now().isoformat(), user_id),
    )
    review_id = cur.lastrowid
    conn.commit()
    conn.close()

    await _clear_state(message.peer_id)
    await message.answer(
        "✅ Спасибо за ваш отзыв!\n\nОн будет опубликован после проверки администратором.",
        keyboard=back_main_kb(),
    )

    admin_text = (
        f"⭐ НОВЫЙ ОТЗЫВ НА МОДЕРАЦИЮ!\n\n"
        f"#{review_id}\n"
        f"Автор: {author}\n"
        f"Текст: {text}\n\n"
        f"VK id: {user_id}"
    )
    approve_kb = (
        Keyboard(inline=True)
        .add(Callback("✅ Одобрить", payload={"cmd": "review_approve", "id": review_id}))
        .add(Callback("❌ Отклонить", payload={"cmd": "review_reject", "id": review_id}))
        .get_json()
    )
    for admin_id in VK_ADMINS:
        try:
            await bot.api.messages.send(
                peer_id=admin_id, message=admin_text, keyboard=approve_kb, random_id=0
            )
        except Exception as e:
            logger.warning("Failed to notify VK admin %s about review: %s", admin_id, e)


# --- Рассылка: текст ---
@bot.on.private_message(state=BroadcastStates.WAITING)
async def broadcast_text_handler(message: Message):
    if not is_admin(message.from_id):
        return
    text = (message.text or "").strip()
    if not (1 <= len(text) <= 3500):
        await message.answer("❌ Сообщение должно быть 1–3500 символов.")
        return
    user_count = len(get_all_vk_user_ids())
    await bot.state_dispenser.set(message.peer_id, BroadcastStates.CONFIRM, broadcast_text=text)
    kb = (
        Keyboard(inline=True)
        .add(Callback(f"✅ Отправить ({user_count})", payload={"cmd": "broadcast_send"}))
        .row()
        .add(Callback("🔙 Отмена", payload={"cmd": "admin_back"}))
        .get_json()
    )
    await message.answer(
        f"Превью рассылки:\n\n{text}\n\n———\nПолучат: {user_count} пользователей VK",
        keyboard=kb,
    )


# ====================== CALLBACK ОБРАБОТЧИК ======================
@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent)
async def handle_callback(event: MessageEvent):
    payload = event.payload
    if not isinstance(payload, dict):
        payload = {}
    cmd = payload.get("cmd", "")

    if cmd == "main":
        await _clear_state(event.peer_id)
        await event.edit_message(MAIN_TEXT, keyboard=main_menu_kb())
    elif cmd == "cancel":
        await _clear_state(event.peer_id)
        await event.edit_message(MAIN_TEXT, keyboard=main_menu_kb())
    elif cmd == "calc_start":
        await cmd_calc_start(event)
    elif cmd == "calc_type":
        await cmd_calc_type(event, payload)
    elif cmd == "works":
        await cmd_works(event, payload)
    elif cmd == "types":
        await cmd_types(event)
    elif cmd == "ftype":
        await cmd_ftype(event, payload)
    elif cmd == "prices":
        await cmd_prices(event)
    elif cmd == "reviews":
        await cmd_reviews(event, payload)
    elif cmd == "submit_review":
        await cmd_submit_review(event)
    elif cmd == "lead_start":
        await cmd_lead_start(event)
    elif cmd == "lead_skip_comment":
        await cmd_lead_skip_comment(event)
    elif cmd == "admin_back":
        if is_admin(event.user_id):
            await _clear_state(event.peer_id)
            await event.edit_message("🛠 Админ-панель", keyboard=admin_menu_kb())
    elif cmd == "admin_close":
        if is_admin(event.user_id):
            await _clear_state(event.peer_id)
            await event.edit_message("Панель закрыта.")
    elif cmd == "admin_leads":
        await cmd_admin_leads(event, payload)
    elif cmd == "lead_view":
        await cmd_lead_view(event, payload)
    elif cmd == "lead_status":
        await cmd_lead_status(event, payload)
    elif cmd == "admin_works":
        await cmd_admin_works(event)
    elif cmd == "work_add":
        await cmd_work_add(event)
    elif cmd == "work_list":
        await cmd_work_list(event, payload)
    elif cmd == "work_del":
        await cmd_work_del(event, payload)
    elif cmd == "work_skip_caption":
        await cmd_work_skip_caption(event)
    elif cmd == "admin_types":
        await cmd_admin_types(event)
    elif cmd == "type_add":
        await cmd_type_add(event)
    elif cmd == "type_edit":
        await cmd_type_edit(event, payload)
    elif cmd == "type_rename":
        await cmd_type_rename(event, payload)
    elif cmd == "type_redesc":
        await cmd_type_redesc(event, payload)
    elif cmd == "type_del":
        await cmd_type_del(event, payload)
    elif cmd == "admin_reviews":
        await cmd_admin_reviews(event)
    elif cmd == "review_add":
        await cmd_review_add(event)
    elif cmd == "review_del":
        await cmd_review_del(event, payload)
    elif cmd == "admin_pending":
        await cmd_admin_pending(event)
    elif cmd == "review_detail":
        await cmd_review_detail(event, payload)
    elif cmd == "review_approve":
        await cmd_review_approve(event, payload)
    elif cmd == "review_reject":
        await cmd_review_reject(event, payload)
    elif cmd == "admin_prices":
        await cmd_admin_prices(event)
    elif cmd == "price_edit":
        await cmd_price_edit(event, payload)
    elif cmd == "admin_stats":
        await cmd_admin_stats(event)
    elif cmd == "admin_broadcast":
        await cmd_admin_broadcast(event)
    elif cmd == "broadcast_send":
        await cmd_broadcast_send(event)
    elif cmd == "admin_export":
        await cmd_admin_export(event)
    else:
        await event.show_snackbar("Неизвестная команда")


# ====================== CALLBACK ФУНКЦИИ ======================

# --- Калькулятор ---
async def cmd_calc_start(event: MessageEvent):
    await bot.state_dispenser.set(event.peer_id, CalcStates.LENGTH)
    await event.edit_message(
        "🧮 КАЛЬКУЛЯТОР СТОИМОСТИ\n\nВведите длину забора в метрах (например, 25 или 25.5):",
        keyboard=cancel_kb(),
    )


async def cmd_calc_type(event: MessageEvent, payload: dict):
    idx = payload.get("i", 0)
    state = await bot.state_dispenser.get(event.peer_id)
    if not state:
        await event.show_snackbar("Начните расчёт заново")
        return
    data = state.payload or {}
    types_list = data.get("price_types", [])
    if idx < 0 or idx >= len(types_list):
        await event.show_snackbar("Тип не найден")
        return
    fence_type = types_list[idx]
    length = data.get("length", 0)
    height = data.get("height", 0)
    prices = get_prices_dict()
    price_per_m2 = prices.get(fence_type, 0)
    area = length * height
    total = int(round(area * price_per_m2))

    calc_data = f"{fence_type}, {length}×{height} м ({area:.1f} м²) — {total} ₽"
    await bot.state_dispenser.set(event.peer_id, CalcStates.HEIGHT, calc_data=calc_data, **data)

    total_formatted = f"{total:,}".replace(",", " ")
    text = (
        f"🧮 РАСЧЁТ СТОИМОСТИ\n\n"
        f"- Материал: {fence_type}\n"
        f"- Размеры: {length} × {height} м\n"
        f"- Площадь: {area:.1f} м²\n"
        f"- Цена за м²: {price_per_m2} ₽ (материал, без работы)\n\n"
        f"💰 Итого: ~{total_formatted} ₽\n\n"
        f"⚠️ Это предварительный расчёт. Финальная стоимость определяется на бесплатном замере."
    )

    kb = (
        Keyboard(inline=True)
        .add(Callback("📍 Заказать бесплатный замер", payload={"cmd": "lead_start"}))
        .row()
        .add(Callback("🧮 Пересчитать", payload={"cmd": "calc_start"}))
        .row()
        .add(Callback("🏠 Главное меню", payload={"cmd": "main"}))
        .get_json()
    )
    await event.edit_message(text, keyboard=kb)


# --- Наши работы ---
async def cmd_works(event: MessageEvent, payload: dict):
    page = max(1, payload.get("p", 1))
    offset = (page - 1) * PHOTOS_PER_PAGE
    rows, total = get_vk_works(offset, PHOTOS_PER_PAGE)

    if total == 0:
        await event.edit_message(
            "📸 НАШИ РАБОТЫ\n\nПока нет загруженных фотографий. Скоро здесь появится наша галерея!",
            keyboard=back_main_kb(),
        )
        return

    total_pages = (total + PHOTOS_PER_PAGE - 1) // PHOTOS_PER_PAGE
    page = min(page, total_pages)

    attachments = []
    captions = []
    for _id, attachment, caption in rows:
        attachments.append(attachment)
        if caption:
            captions.append(caption)

    text = f"📸 НАШИ РАБОТЫ — стр. {page}/{total_pages}"
    if captions:
        text += "\n\n" + "\n".join(captions)

    kb = Keyboard(inline=True)
    nav = []
    if page > 1:
        nav.append(Callback("◀️ Назад", payload={"cmd": "works", "p": page - 1}))
    if page < total_pages:
        nav.append(Callback("Вперёд ▶️", payload={"cmd": "works", "p": page + 1}))
    if nav:
        for n in nav:
            kb.add(n)
        kb.row()
    kb.add(Callback("📍 Заказать замер", payload={"cmd": "lead_start"}))
    kb.row()
    kb.add(Callback("🏠 Главное меню", payload={"cmd": "main"}))

    await event.send_message(
        message=text,
        attachment=",".join(attachments),
        keyboard=kb.get_json(),
    )
    try:
        await event.edit_message("⬆️ Смотрите фото выше")
    except Exception:
        pass


# --- Виды заборов ---
async def cmd_types(event: MessageEvent):
    types = get_fence_types()
    if not types:
        await event.edit_message(
            "🏗 ВИДЫ ЗАБОРОВ\n\nПока не добавлено ни одного типа.",
            keyboard=back_main_kb(),
        )
        return

    kb = Keyboard(inline=True)
    for i, (tid, name, _desc) in enumerate(types):
        if i > 0:
            kb.row()
        kb.add(Callback(name, payload={"cmd": "ftype", "id": tid}))
    kb.row()
    kb.add(Callback("🏠 Главное меню", payload={"cmd": "main"}))
    await event.edit_message(
        "🏗 ВИДЫ ЗАБОРОВ\n\nВыберите тип, чтобы посмотреть подробности:",
        keyboard=kb.get_json(),
    )


async def cmd_ftype(event: MessageEvent, payload: dict):
    tid = payload.get("id")
    if not tid:
        await event.show_snackbar("Тип не найден")
        return
    row = get_fence_type(tid)
    if not row:
        await event.show_snackbar("Тип не найден")
        return
    _id, name, description = row
    prices = get_prices_dict()
    price = prices.get(name)
    text = strip_html(description or name)
    if price:
        text += f"\n\n💰 Цена: {price} ₽/м² (материал, без учёта работы)"

    kb = (
        Keyboard(inline=True)
        .add(Callback("🧮 Рассчитать стоимость", payload={"cmd": "calc_start"}))
        .row()
        .add(Callback("📍 Заказать замер", payload={"cmd": "lead_start"}))
        .row()
        .add(Callback("🔙 К видам заборов", payload={"cmd": "types"}))
        .row()
        .add(Callback("🏠 Главное меню", payload={"cmd": "main"}))
        .get_json()
    )
    await event.edit_message(text, keyboard=kb)


# --- Цены ---
async def cmd_prices(event: MessageEvent):
    prices = get_prices_dict()
    if not prices:
        await event.edit_message(
            "💰 ЦЕНЫ\n\nЦены пока не настроены. Напишите менеджеру.",
            keyboard=back_main_kb(),
        )
        return
    lines = ["💰 ПРАЙС-ЛИСТ (руб/м²)\n"]
    for t, p in prices.items():
        lines.append(f"- {t}: {p} ₽")
    lines.append("\n⚠️ Цены указаны за материал, без учёта работы.")
    lines.append("Финальная стоимость — после бесплатного замера.")

    kb = (
        Keyboard(inline=True)
        .add(Callback("🧮 Рассчитать стоимость", payload={"cmd": "calc_start"}))
        .row()
        .add(Callback("📍 Заказать замер", payload={"cmd": "lead_start"}))
        .row()
        .add(Callback("🏠 Главное меню", payload={"cmd": "main"}))
        .get_json()
    )
    await event.edit_message("\n".join(lines), keyboard=kb)


# --- Отзывы ---
async def cmd_reviews(event: MessageEvent, payload: dict):
    page = max(1, payload.get("p", 1))
    offset = (page - 1) * REVIEWS_PER_PAGE
    rows, total = get_reviews(offset, REVIEWS_PER_PAGE)

    if total == 0:
        await event.edit_message(
            "⭐ ОТЗЫВЫ\n\nПока нет отзывов. Будьте первым — закажите замер!",
            keyboard=back_main_kb(),
        )
        return

    total_pages = (total + REVIEWS_PER_PAGE - 1) // REVIEWS_PER_PAGE
    page = min(page, total_pages)

    parts = [f"⭐ ОТЗЫВЫ — стр. {page}/{total_pages}\n"]
    for _id, author, text in rows:
        parts.append(f"{author}\n{text}\n")

    kb = Keyboard(inline=True)
    nav = []
    if page > 1:
        nav.append(Callback("◀️ Назад", payload={"cmd": "reviews", "p": page - 1}))
    if page < total_pages:
        nav.append(Callback("Вперёд ▶️", payload={"cmd": "reviews", "p": page + 1}))
    if nav:
        for n in nav:
            kb.add(n)
        kb.row()
    kb.add(Callback("✍️ Оставить отзыв", payload={"cmd": "submit_review"}))
    kb.row()
    kb.add(Callback("📍 Заказать замер", payload={"cmd": "lead_start"}))
    kb.row()
    kb.add(Callback("🏠 Главное меню", payload={"cmd": "main"}))

    await event.edit_message("\n".join(parts), keyboard=kb.get_json())


# --- Отправка отзыва пользователем ---
async def cmd_submit_review(event: MessageEvent):
    await bot.state_dispenser.set(event.peer_id, SubmitReviewStates.AUTHOR)
    await event.edit_message(
        "✍️ ОСТАВИТЬ ОТЗЫВ\n\nВведите ваше имя (например, Алексей, Ижевск):",
        keyboard=cancel_kb(),
    )


# --- Заявка на замер ---
async def cmd_lead_start(event: MessageEvent):
    state = await bot.state_dispenser.get(event.peer_id)
    calc_data = ""
    if state and state.payload:
        calc_data = state.payload.get("calc_data", "")
    await bot.state_dispenser.set(event.peer_id, LeadStates.NAME, calc_data=calc_data)
    await event.edit_message(
        "📍 ЗАЯВКА НА БЕСПЛАТНЫЙ ЗАМЕР\n\nКак вас зовут?",
        keyboard=cancel_kb(),
    )


async def cmd_lead_skip_comment(event: MessageEvent):
    state = await bot.state_dispenser.get(event.peer_id)
    if not state:
        return
    data = state.payload or {}
    await _finalize_lead(event.peer_id, event.user_id, data, "")


# ====================== АДМИН: ЗАЯВКИ ======================
async def cmd_admin_leads(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    page = max(1, payload.get("p", 1))
    offset = (page - 1) * LEADS_PER_PAGE
    rows, total = get_leads(offset, LEADS_PER_PAGE)

    if total == 0:
        await event.edit_message("📋 Заявок пока нет.", keyboard=admin_back_kb())
        return

    total_pages = (total + LEADS_PER_PAGE - 1) // LEADS_PER_PAGE
    page = min(page, total_pages)

    lines = [f"📋 ЗАЯВКИ — стр. {page}/{total_pages} (всего: {total})\n"]
    kb = Keyboard(inline=True)
    for lid, name, phone, status, created_at in rows:
        date_str = created_at[:16].replace("T", " ") if created_at else ""
        lines.append(f"#{lid} - {name} - {phone} - {status} - {date_str}")
        kb.row()
        kb.add(Callback(f"#{lid} {name}", payload={"cmd": "lead_view", "id": lid}))

    nav = []
    if page > 1:
        nav.append(Callback("◀️", payload={"cmd": "admin_leads", "p": page - 1}))
    if page < total_pages:
        nav.append(Callback("▶️", payload={"cmd": "admin_leads", "p": page + 1}))
    if nav:
        kb.row()
        for n in nav:
            kb.add(n)
    kb.row()
    kb.add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message("\n".join(lines), keyboard=kb.get_json())


async def cmd_lead_view(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    lid = payload.get("id")
    row = get_lead(lid)
    if not row:
        await event.show_snackbar("Заявка не найдена")
        return
    cols = row
    _id = cols[0]
    user_id = cols[1]
    name = cols[2]
    phone = cols[3]
    address = cols[4]
    comment = cols[5]
    calc_data = cols[6]
    status = cols[7]
    created_at = cols[8]
    platform = cols[9] if len(cols) > 9 else "tg"

    text = (
        f"📋 ЗАЯВКА №{_id}\n\n"
        f"Статус: {status}\n"
        f"Имя: {name}\n"
        f"Телефон: {phone}\n"
        f"Адрес: {address}\n"
        f"Комментарий: {comment or '—'}\n"
        f"Расчёт: {calc_data or '—'}\n"
        f"Платформа: {platform}\n"
        f"ID: {user_id}\n"
        f"Создана: {created_at[:16].replace('T', ' ') if created_at else ''}"
    )
    kb = Keyboard(inline=True)
    for i, s in enumerate(LEAD_STATUSES):
        if s != status:
            kb.row()
            kb.add(Callback(f"→ {s}", payload={"cmd": "lead_status", "id": _id, "s": i}))
    kb.row()
    kb.add(Callback("🔙 К заявкам", payload={"cmd": "admin_leads", "p": 1}))
    kb.row()
    kb.add(Callback("🛠 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def cmd_lead_status(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    lid = payload.get("id")
    sidx = payload.get("s")
    if sidx is None or sidx < 0 or sidx >= len(LEAD_STATUSES):
        return
    new_status = LEAD_STATUSES[sidx]
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lid))
    conn.commit()
    conn.close()
    await event.show_snackbar(f"Статус → {new_status}")
    # Refresh the view
    await cmd_lead_view(event, {"id": lid})


# ====================== АДМИН: РАБОТЫ ======================
async def cmd_admin_works(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vk_works")
    total = cur.fetchone()[0]
    conn.close()
    kb = (
        Keyboard(inline=True)
        .add(Callback("➕ Добавить фото", payload={"cmd": "work_add"}))
        .row()
        .add(Callback(f"📋 Список фото ({total})", payload={"cmd": "work_list", "p": 1}))
        .row()
        .add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
        .get_json()
    )
    await event.edit_message(
        f"📸 УПРАВЛЕНИЕ РАБОТАМИ\n\nВсего фото: {total}", keyboard=kb
    )


async def cmd_work_add(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    await bot.state_dispenser.set(event.peer_id, AddWorkStates.PHOTO)
    await event.edit_message("📸 Пришлите фото для добавления в галерею:", keyboard=admin_back_kb())


async def cmd_work_list(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    page = max(1, payload.get("p", 1))
    offset = (page - 1) * PHOTOS_PER_PAGE
    rows, total = get_vk_works(offset, PHOTOS_PER_PAGE)
    if total == 0:
        await event.edit_message("Пока нет загруженных фото.", keyboard=admin_back_kb())
        return
    total_pages = (total + PHOTOS_PER_PAGE - 1) // PHOTOS_PER_PAGE
    page = min(page, total_pages)
    lines = [f"📋 ФОТО — стр. {page}/{total_pages} (всего: {total})\n"]
    kb = Keyboard(inline=True)
    for wid, _att, caption in rows:
        snippet = (caption or "(без подписи)").strip()
        if len(snippet) > 30:
            snippet = snippet[:27] + "…"
        lines.append(f"#{wid}: {snippet}")
        kb.row()
        kb.add(Callback(f"🗑 Удалить #{wid}", payload={"cmd": "work_del", "id": wid}))
    nav = []
    if page > 1:
        nav.append(Callback("◀️", payload={"cmd": "work_list", "p": page - 1}))
    if page < total_pages:
        nav.append(Callback("▶️", payload={"cmd": "work_list", "p": page + 1}))
    if nav:
        kb.row()
        for n in nav:
            kb.add(n)
    kb.row()
    kb.add(Callback("🔙 К работам", payload={"cmd": "admin_works"}))
    await event.edit_message("\n".join(lines), keyboard=kb.get_json())


async def cmd_work_del(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    wid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM vk_works WHERE id = ?", (wid,))
    conn.commit()
    conn.close()
    await event.show_snackbar("Удалено")
    await cmd_work_list(event, {"p": 1})


async def cmd_work_skip_caption(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    state = await bot.state_dispenser.get(event.peer_id)
    if not state:
        return
    data = state.payload or {}
    attachment = data.get("attachment")
    await _save_vk_work(event.peer_id, attachment, "")


# ====================== АДМИН: ВИДЫ ЗАБОРОВ ======================
async def cmd_admin_types(event: MessageEvent):
    if not is_admin(event.user_id):
        return

    types = get_fence_types()

    kb = Keyboard(inline=True)

    kb.add(
        Callback(
            "➕ Добавить тип",
            payload={"cmd": "type_add"}
        )
    )

    max_buttons = 8

    for i, (tid, name, _desc) in enumerate(types[:max_buttons]):
        kb.row()

        # VK не любит длинные кнопки
        short_name = name[:30]

        kb.add(
            Callback(
                short_name,
                payload={
                    "cmd": "type_edit",
                    "id": tid
                }
            )
        )

    if len(types) > max_buttons:
        kb.row()
        kb.add(
            Callback(
                f"Ещё ({len(types)-max_buttons})",
                payload={"cmd": "types_more"}
            )
        )

    kb.row()

    kb.add(
        Callback(
            "🔙 В админку",
            payload={"cmd": "admin_back"}
        )
    )

    await event.edit_message(
        f"🏗 УПРАВЛЕНИЕ ВИДАМИ ЗАБОРОВ\n\n"
        f"Всего: {len(types)}\n"
        f"Кликните на тип, чтобы изменить или удалить.",
        keyboard=kb.get_json(),
    )


async def cmd_type_add(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    await bot.state_dispenser.set(event.peer_id, AddTypeStates.NAME)
    await event.edit_message("Введите название нового типа забора:", keyboard=admin_back_kb())


async def cmd_type_edit(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    tid = payload.get("id")
    row = get_fence_type(tid)
    if not row:
        await event.show_snackbar("Не найдено")
        return
    _id, name, _desc = row
    kb = (
        Keyboard(inline=True)
        .add(Callback("✏️ Изменить название", payload={"cmd": "type_rename", "id": tid}))
        .row()
        .add(Callback("📝 Изменить описание", payload={"cmd": "type_redesc", "id": tid}))
        .row()
        .add(Callback("🗑 Удалить", payload={"cmd": "type_del", "id": tid}))
        .row()
        .add(Callback("🔙 К типам", payload={"cmd": "admin_types"}))
        .get_json()
    )
    await event.edit_message(f"🏗 {name}\n\nЧто меняем?", keyboard=kb)


async def cmd_type_rename(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    tid = payload.get("id")
    await bot.state_dispenser.set(event.peer_id, EditTypeStates.NAME, type_id=tid)
    await event.edit_message("Введите новое название:", keyboard=admin_back_kb())


async def cmd_type_redesc(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    tid = payload.get("id")
    await bot.state_dispenser.set(event.peer_id, EditTypeStates.DESC, type_id=tid)
    await event.edit_message("Пришлите новое описание:", keyboard=admin_back_kb())


async def cmd_type_del(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    tid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM fence_types WHERE id = ?", (tid,))
    conn.commit()
    conn.close()
    await event.show_snackbar("Удалено")
    await cmd_admin_types(event)


# ====================== АДМИН: ОТЗЫВЫ ======================
async def cmd_admin_reviews(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    rows, total = get_reviews(0, 100)
    pending = get_pending_reviews()
    pending_count = len(pending)

    kb = Keyboard(inline=True)
    kb.add(Callback("➕ Добавить отзыв", payload={"cmd": "review_add"}))
    if pending_count > 0:
        kb.row()
        kb.add(Callback(f"🕐 На модерации ({pending_count})", payload={"cmd": "admin_pending"}))

    lines = [f"⭐ УПРАВЛЕНИЕ ОТЗЫВАМИ\n\nОпубликовано: {total}\nНа модерации: {pending_count}\n"]
    for rid, author, text in rows[:15]:
        snippet = text[:40] + ("…" if len(text) > 40 else "")
        lines.append(f"#{rid} {author}: {snippet}")
        kb.row()
        kb.add(Callback(f"🗑 Удалить #{rid}", payload={"cmd": "review_del", "id": rid}))
    if total > 15:
        lines.append(f"\n…и ещё {total - 15}")
    kb.row()
    kb.add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message("\n".join(lines), keyboard=kb.get_json())


async def cmd_review_add(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    await bot.state_dispenser.set(event.peer_id, AddReviewStates.AUTHOR)
    await event.edit_message(
        "Введите имя автора (например, Алексей, Ижевск):", keyboard=admin_back_kb()
    )


async def cmd_review_del(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    rid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    await event.show_snackbar("Удалено")
    await cmd_admin_reviews(event)


# ====================== АДМИН: МОДЕРАЦИЯ ОТЗЫВОВ ======================
async def cmd_admin_pending(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    pending = get_pending_reviews()
    if not pending:
        await event.edit_message(
            "🕐 МОДЕРАЦИЯ ОТЗЫВОВ\n\nНет отзывов, ожидающих проверки.",
            keyboard=admin_back_kb(),
        )
        return

    lines = [f"🕐 ОТЗЫВЫ НА МОДЕРАЦИИ ({len(pending)})\n"]
    kb = Keyboard(inline=True)
    for rid, author, text, user_id, created_at in pending[:10]:
        snippet = text[:40] + ("…" if len(text) > 40 else "")
        date_str = created_at[:16].replace("T", " ") if created_at else ""
        lines.append(f"#{rid} {author}: {snippet}\n{date_str}")
        kb.row()
        kb.add(Callback(f"✅ #{rid}", payload={"cmd": "review_approve", "id": rid}))
        kb.add(Callback(f"❌ #{rid}", payload={"cmd": "review_reject", "id": rid}))
    if len(pending) > 10:
        lines.append(f"\n…и ещё {len(pending) - 10}")
    kb.row()
    kb.add(Callback("🔙 К отзывам", payload={"cmd": "admin_reviews"}))
    kb.row()
    kb.add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message("\n".join(lines), keyboard=kb.get_json())


async def cmd_review_detail(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    rid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, author, text, user_id, created_at, approved FROM reviews WHERE id = ?",
        (rid,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        await event.show_snackbar("Отзыв не найден")
        return
    _id, author, text, user_id, created_at, approved = row
    status = "Опубликован" if approved else "Ожидает модерации"
    date_str = created_at[:16].replace("T", " ") if created_at else ""
    msg = (
        f"⭐ ОТЗЫВ #{_id}\n\n"
        f"Статус: {status}\n"
        f"Автор: {author}\n"
        f"User ID: {user_id or '—'}\n"
        f"Дата: {date_str}\n\n"
        f"Текст:\n{text}"
    )
    kb = Keyboard(inline=True)
    if not approved:
        kb.add(Callback("✅ Одобрить", payload={"cmd": "review_approve", "id": rid}))
        kb.add(Callback("❌ Отклонить", payload={"cmd": "review_reject", "id": rid}))
        kb.row()
    kb.add(Callback("🔙 К модерации", payload={"cmd": "admin_pending"}))
    kb.row()
    kb.add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message(msg, keyboard=kb.get_json())


async def cmd_review_approve(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    rid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT author, user_id FROM reviews WHERE id = ? AND approved = 0", (rid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await event.show_snackbar("Отзыв уже одобрен или не найден")
        return
    author, user_id = row
    cur.execute("UPDATE reviews SET approved = 1 WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    await event.show_snackbar(f"Отзыв #{rid} одобрен!")

    if user_id:
        try:
            await bot.api.messages.send(
                peer_id=user_id,
                message=f"✅ Ваш отзыв был опубликован! Спасибо, {author}!",
                random_id=0,
            )
        except Exception as e:
            logger.warning("Failed to notify VK user %s about approved review: %s", user_id, e)

    kb = (
        Keyboard(inline=True)
        .add(Callback("🕐 К модерации", payload={"cmd": "admin_pending"}))
        .add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
        .get_json()
    )
    await event.edit_message(f"✅ Отзыв #{rid} от {author} одобрен и опубликован.", keyboard=kb)


async def cmd_review_reject(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    rid = payload.get("id")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT author, user_id FROM reviews WHERE id = ? AND approved = 0", (rid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await event.show_snackbar("Отзыв уже обработан или не найден")
        return
    author, user_id = row
    cur.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    await event.show_snackbar(f"Отзыв #{rid} отклонён")

    if user_id:
        try:
            await bot.api.messages.send(
                peer_id=user_id,
                message="К сожалению, ваш отзыв не прошёл модерацию. Попробуйте написать новый.",
                random_id=0,
            )
        except Exception as e:
            logger.warning("Failed to notify VK user %s about rejected review: %s", user_id, e)

    kb = (
        Keyboard(inline=True)
        .add(Callback("🕐 К модерации", payload={"cmd": "admin_pending"}))
        .add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
        .get_json()
    )
    await event.edit_message(f"❌ Отзыв #{rid} от {author} отклонён и удалён.", keyboard=kb)


# ====================== АДМИН: ЦЕНЫ ======================
async def cmd_admin_prices(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    prices = get_prices_dict()
    text = "💰 ТЕКУЩИЕ ЦЕНЫ (руб/м²)\n\n"
    for t, p in prices.items():
        text += f"- {t}: {p}\n"

    types_list = list(prices.keys())
    kb = Keyboard(inline=True)
    for idx, t in enumerate(types_list):
        kb.row()
        kb.add(Callback(f"✏️ {t}", payload={"cmd": "price_edit", "i": idx}))
    kb.row()
    kb.add(Callback("🔙 В админку", payload={"cmd": "admin_back"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def cmd_price_edit(event: MessageEvent, payload: dict):
    if not is_admin(event.user_id):
        return
    idx = payload.get("i", 0)
    prices = get_prices_dict()
    types_list = list(prices.keys())
    if idx < 0 or idx >= len(types_list):
        await event.show_snackbar("Не найдено")
        return
    fence_type = types_list[idx]
    await bot.state_dispenser.set(event.peer_id, EditPriceStates.WAITING, fence_type=fence_type)
    await event.edit_message(
        f"Введите новую цену для {fence_type} (₽/м²):", keyboard=admin_back_kb()
    )


# ====================== АДМИН: СТАТИСТИКА ======================
async def cmd_admin_stats(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vk_users")
    vk_users_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users")
    tg_users_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM leads")
    leads_total = cur.fetchone()[0]
    cur.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
    by_status = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM vk_works")
    vk_works_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM works")
    tg_works_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 1")
    reviews_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reviews WHERE approved = 0")
    reviews_pending = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM fence_types")
    types_total = cur.fetchone()[0]
    conn.close()

    lines = ["📊 СТАТИСТИКА\n"]
    lines.append(f"👥 Пользователей VK: {vk_users_total}")
    lines.append(f"👥 Пользователей TG: {tg_users_total}")
    lines.append(f"📋 Заявок всего: {leads_total}")
    for s in LEAD_STATUSES:
        lines.append(f"  - {s}: {by_status.get(s, 0)}")
    lines.append(f"📸 Фото VK: {vk_works_total}")
    lines.append(f"📸 Фото TG: {tg_works_total}")
    lines.append(f"⭐ Отзывов: {reviews_total}")
    if reviews_pending > 0:
        lines.append(f"  - На модерации: {reviews_pending}")
    lines.append(f"🏗 Видов заборов: {types_total}")

    await event.edit_message("\n".join(lines), keyboard=admin_back_kb())


# ====================== АДМИН: РАССЫЛКА ======================
async def cmd_admin_broadcast(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    await bot.state_dispenser.set(event.peer_id, BroadcastStates.WAITING)
    await event.edit_message(
        "📢 РАССЫЛКА\n\nПришлите текст сообщения для рассылки всем пользователям VK-бота:",
        keyboard=admin_back_kb(),
    )


async def cmd_broadcast_send(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    state = await bot.state_dispenser.get(event.peer_id)
    if not state:
        return
    data = state.payload or {}
    text = data.get("broadcast_text", "")
    await _clear_state(event.peer_id)

    user_ids = get_all_vk_user_ids()
    await event.edit_message(f"Отправляю {len(user_ids)} пользователям…")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.api.messages.send(peer_id=uid, message=text, random_id=0)
            sent += 1
        except Exception as e:
            logger.info("VK Broadcast failed for %s: %s", uid, e)
            failed += 1
        await asyncio.sleep(0.1)

    await event.send_message(
        message=f"✅ Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}",
        keyboard=admin_back_kb(),
    )


# ====================== АДМИН: ЭКСПОРТ ======================
async def cmd_admin_export(event: MessageEvent):
    if not is_admin(event.user_id):
        return
    await event.show_snackbar("Готовлю файл…")

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
    headers = ["№", "User ID", "Имя", "Телефон", "Адрес", "Комментарий", "Расчёт", "Статус", "Создана"]
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
        doc_uploader = DocMessagesUploader(bot.api)
        filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        doc = await doc_uploader.upload(
            file_source=tmp_path,
            peer_id=event.peer_id,
            title=filename,
        )
        await bot.api.messages.send(
            peer_id=event.peer_id,
            attachment=doc,
            message=f"📥 Экспорт заявок: {len(rows)}",
            random_id=0,
        )
    except Exception as e:
        logger.exception("VK export failed: %s", e)
        await event.send_message(message="❌ Ошибка при экспорте. Попробуйте позже.")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    await bot.api.messages.send(
        peer_id=event.peer_id, message="Готово.", keyboard=admin_back_kb(), random_id=0
    )


# ====================== ДЕФОЛТНЫЙ ОБРАБОТЧИК ======================
@bot.on.private_message()
async def default_handler(message: Message):
    conn = _connect()
    cur = conn.cursor()
    try:
        user_info = await bot.api.users.get(user_ids=[message.from_id])
        first = user_info[0].first_name if user_info else ""
        last = user_info[0].last_name if user_info else ""
    except Exception:
        first = ""
        last = ""
    cur.execute(
        "INSERT OR REPLACE INTO vk_users VALUES (?,?,?,?)",
        (message.from_id, first, last, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    await message.answer(MAIN_TEXT, keyboard=main_menu_kb())


# ====================== ЗАПУСК ======================
def main():
    init_db()
    logger.info("🚀 VK-бот запущен!")
    bot.run_forever()


if __name__ == "__main__":
    main()
