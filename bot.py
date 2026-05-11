import asyncio
import logging
import sqlite3
import os
from datetime import datetime

import openpyxl
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("8705623484:AAHuEOSwTpEa6VlXcHwOoxk9H-ao2ChmK7w")
ADMINS_STR = os.getenv("ADMINS", "5118405789, 5635535380")

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в переменных Railway!")

ADMINS = [int(x.strip()) for x in ADMINS_STR.split(",") if x.strip()]

PHOTOS_PER_PAGE = 6

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = "fence_bot.db"

# ====================== БАЗА ДАННЫХ ======================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, created_at TEXT)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        phone TEXT,
        address TEXT,
        comment TEXT,
        calc_data TEXT,
        status TEXT DEFAULT 'Новая',
        created_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS works (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id TEXT UNIQUE,
        caption TEXT,
        added_at TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS prices (
        fence_type TEXT PRIMARY KEY,
        price_per_m2 INTEGER
    )''')

    default_prices = {
        "Профнастил": 2500,
        "Металлический штакетник": 3200,
        "3D-сетка": 1800,
        "Рабица": 1200,
        "Дерево": 2800,
        "Комбинированный": 3500
    }
    for t, p in default_prices.items():
        cur.execute("INSERT OR IGNORE INTO prices VALUES (?, ?)", (t, p))

    conn.commit()
    conn.close()

# ====================== FSM ======================
class CalculatorStates(StatesGroup):
    length = State()
    height = State()
    fence_type = State()

class LeadStates(StatesGroup):
    name = State()
    phone = State()
    address = State()
    comment = State()

class AddWorkStates(StatesGroup):
    photo = State()
    caption = State()

class BroadcastStates(StatesGroup):
    text = State()

# ====================== КЛАВИАТУРЫ ======================
def main_menu():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data="calc_start"))
    b.row(InlineKeyboardButton(text="📸 Наши работы", callback_data="works"))
    b.row(InlineKeyboardButton(text="🏗 Виды заборов", callback_data="types"))
    b.row(InlineKeyboardButton(text="💰 Цены", callback_data="prices"))
    b.row(InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews"))
    b.row(InlineKeyboardButton(text="📍 Заказать замер", callback_data="lead_start"))
    return b.as_markup()

def admin_menu():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📋 Все заявки", callback_data="admin_leads_1"))
    b.row(InlineKeyboardButton(text="📸 Управление работами", callback_data="admin_works"))
    b.row(InlineKeyboardButton(text="💰 Управление ценами", callback_data="admin_prices"))
    b.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    b.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"))
    b.row(InlineKeyboardButton(text="📥 Экспорт Excel", callback_data="admin_export"))
    b.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close"))
    return b.as_markup()

# ====================== РОУТЕР ======================
router = Router()

# ====================== СТАРТ ======================
@router.message(CommandStart())
async def start(message: Message):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?)",
                (message.from_user.id, message.from_user.username, message.from_user.full_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    await message.answer(
        "🔨 <b>Заборы под ключ — Ижевск</b>\n\n"
        "Качественные заборы • Честные цены • Гарантия\n"
        "Бесплатный выезд замерщика",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("🛠 <b>Админ-панель</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())

# ====================== КАЛЬКУЛЯТОР ======================
@router.callback_query(F.data == "calc_start")
async def calc_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("📏 Введите длину забора в метрах:")
    await state.set_state(CalculatorStates.length)

@router.message(CalculatorStates.length)
async def process_length(message: Message, state: FSMContext):
    try:
        length = float(message.text.replace(',', '.'))
        if length <= 0: raise ValueError
        await state.update_data(length=length)
        await message.answer("📐 Выберите высоту:", reply_markup=height_kb())
        await state.set_state(CalculatorStates.height)
    except:
        await message.answer("❌ Введите корректное число!")

def height_kb():
    b = InlineKeyboardBuilder()
    for h in ["1.5 м", "1.8 м", "2.0 м", "2.1 м", "Другая"]:
        b.button(text=h, callback_data=f"height_{h}")
    b.adjust(2)
    return b.as_markup()

# ====================== ЗАЯВКИ (Полное управление) ======================
@router.callback_query(F.data.in_(["lead_start", "lead_from_calc"]))
async def lead_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("👤 Введите ваше имя:")
    await state.set_state(LeadStates.name)

# ... (LeadStates handlers)
@router.message(LeadStates.name)
async def lead_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📱 Введите телефон:")
    await state.set_state(LeadStates.phone)

@router.message(LeadStates.phone)
async def lead_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer("📍 Адрес / СНТ:")
    await state.set_state(LeadStates.address)

@router.message(LeadStates.address)
async def lead_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)
    await message.answer("💬 Комментарий (можно пропустить):")
    await state.set_state(LeadStates.comment)

@router.message(LeadStates.comment)
async def lead_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""INSERT INTO leads 
        (user_id, name, phone, address, comment, calc_data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message.from_user.id, data.get('name'), data.get('phone'),
         data.get('address'), message.text, str(data), datetime.now().isoformat()))
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()

    await message.answer("✅ <b>Заявка принята!</b>\nСкоро с вами свяжутся.", parse_mode=ParseMode.HTML)
    await state.clear()

    for admin in ADMINS:
        try:
            await message.bot.send_message(admin, f"🔔 Новая заявка #{lead_id}")
        except:
            pass

# ====================== НАШИ РАБОТЫ (Добавление + Удаление) ======================
@router.callback_query(F.data == "works")
async def show_works(call: CallbackQuery):
    await show_works_page(call, 1)

async def show_works_page(call: CallbackQuery, page: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM works")
    total = cur.fetchone()[0]
    offset = (page - 1) * PHOTOS_PER_PAGE
    cur.execute("SELECT id, file_id, caption FROM works ORDER BY id DESC LIMIT ? OFFSET ?", 
                (PHOTOS_PER_PAGE, offset))
    works = cur.fetchall()
    conn.close()

    if not works:
        await call.message.edit_text("📸 Пока нет выполненных работ.")
        return

    media = [InputMediaPhoto(media=w[1], caption=w[2] if i == 0 else None) for i, w in enumerate(works)]
    await call.message.answer_media_group(media)

    total_pages = (total + PHOTOS_PER_PAGE - 1) // PHOTOS_PER_PAGE
    b = InlineKeyboardBuilder()
    if page > 1: b.button(text="⬅", callback_data=f"works_page_{page-1}")
    if page < total_pages: b.button(text="➡", callback_data=f"works_page_{page+1}")
    b.button(text="🏠 Главное меню", callback_data="main_menu")
    await call.message.answer(f"📸 Наши работы {page}/{total_pages}", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("works_page_"))
async def works_pagination(call: CallbackQuery):
    page = int(call.data.split("_")[-1])
    await show_works_page(call, page)

# Админ — Добавление и удаление работ
@router.callback_query(F.data == "admin_works")
async def admin_works_menu(call: CallbackQuery):
    if call.from_user.id not in ADMINS: return
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить работу", callback_data="add_work")
    b.button(text="🗑 Удалить работу", callback_data="list_works_delete")
    b.button(text="🔙 Назад", callback_data="admin_back")
    await call.message.edit_text("📸 <b>Управление Нашими работами</b>", parse_mode=ParseMode.HTML, reply_markup=b.as_markup())

@router.callback_query(F.data == "add_work")
async def add_work_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS: return
    await call.message.edit_text("Отправьте фото работы:")
    await state.set_state(AddWorkStates.photo)

@router.message(AddWorkStates.photo, F.photo)
async def process_work_photo(message: Message, state: FSMContext):
    await state.update_data(file_id=message.photo[-1].file_id)
    await message.answer("Введите подпись к фото:")
    await state.set_state(AddWorkStates.caption)

@router.message(AddWorkStates.caption)
async def save_work(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO works (file_id, caption, added_at) VALUES (?,?,?)",
                (data['file_id'], message.text, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await message.answer("✅ Фото успешно добавлено!")
    await state.clear()

@router.callback_query(F.data == "list_works_delete")
async def list_works_delete(call: CallbackQuery):
    if call.from_user.id not in ADMINS: return
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, caption FROM works ORDER BY id DESC")
    works = cur.fetchall()
    conn.close()

    b = InlineKeyboardBuilder()
    for wid, caption in works:
        text = (caption[:30] + "...") if caption else "Без подписи"
        b.button(text=f"🗑 {text}", callback_data=f"del_work_{wid}")
    b.button(text="🔙 Назад", callback_data="admin_works")
    await call.message.edit_text("Выберите работу для удаления:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("del_work_"))
async def delete_work_handler(call: CallbackQuery):
    if call.from_user.id not in ADMINS: return
    work_id = int(call.data.split("_")[-1])
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM works WHERE id = ?", (work_id,))
    conn.commit()
    conn.close()
    await call.answer("✅ Работа удалена")
    await list_works_delete(call)

# ====================== ЗАПУСК ======================
async def main():
    init_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Бот запущен — все функции активны!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
