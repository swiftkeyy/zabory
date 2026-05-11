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
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, FSInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("8705623484:AAHuEOSwTpEa6VlXcHwOoxk9H-ao2ChmK7w")
ADMINS_STR = os.getenv("ADMINS", "5118405789, 5635535380")

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавь его в Variables на Railway.")

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
        user_id INTEGER, name TEXT, phone TEXT, address TEXT, comment TEXT,
        calc_data TEXT, status TEXT DEFAULT 'Новая', created_at TEXT)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS works (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        file_id TEXT UNIQUE, caption TEXT, added_at TEXT)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS prices (
        fence_type TEXT PRIMARY KEY, price_per_m2 INTEGER)''')

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

def get_prices_dict():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT fence_type, price_per_m2 FROM prices")
    prices = dict(cur.fetchall())
    conn.close()
    return prices

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

class EditPriceStates(StatesGroup):
    waiting_price = State()

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
        "🔨 <b>Заборы под ключ в Ижевске</b>\n\n"
        "Качественно • Быстро • С гарантией\nБесплатный выезд замерщика",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("🛠 <b>Админ-панель</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())

# ====================== УПРАВЛЕНИЕ ЦЕНАМИ ======================
@router.callback_query(F.data == "admin_prices")
async def admin_prices_menu(call: CallbackQuery):
    if call.from_user.id not in ADMINS: return
    prices = get_prices_dict()
    text = "💰 <b>Текущие цены (руб/м²)</b>\n\n"
    for t, p in prices.items():
        text += f"• {t}: <b>{p}</b>\n"
    
    b = InlineKeyboardBuilder()
    for t in prices.keys():
        b.button(text=f"Изменить {t}", callback_data=f"edit_price_{t}")
    b.button(text="🔙 Назад", callback_data="admin_back")
    b.adjust(1)
    
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("edit_price_"))
async def edit_price_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS: return
    fence_type = call.data.split("_", 2)[2]
    await state.update_data(fence_type=fence_type)
    await call.message.edit_text(f"Введите новую цену для <b>{fence_type}</b>:", parse_mode=ParseMode.HTML)
    await state.set_state(EditPriceStates.waiting_price)

@router.message(EditPriceStates.waiting_price)
async def save_new_price(message: Message, state: FSMContext):
    if message.from_user.id not in ADMINS: return
    try:
        new_price = int(message.text)
        data = await state.get_data()
        fence_type = data['fence_type']
        
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("UPDATE prices SET price_per_m2 = ? WHERE fence_type = ?", (new_price, fence_type))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Цена обновлена!\n{fence_type} → {new_price} ₽/м²")
        await state.clear()
    except:
        await message.answer("❌ Введите число!")

# ====================== ЗАПУСК ======================
async def main():
    init_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Бот запущен успешно!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
