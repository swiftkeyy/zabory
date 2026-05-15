"""
Универсальный модуль для работы с базой данных.
Поддерживает SQLite (локально) и PostgreSQL (Railway).
Автоматически определяет тип БД по переменной окружения DATABASE_URL.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Определяем тип БД
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_TYPE = "postgresql" if DATABASE_URL else "sqlite"

logger.info(f"🗄️ Используется БД: {DB_TYPE}")


def _connect():
    """Создает подключение к БД (SQLite или PostgreSQL)"""
    if DB_TYPE == "postgresql":
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    else:
        import sqlite3
        db_path = os.getenv("DB_PATH", "fence_bot.db")
        return sqlite3.connect(db_path)


def _placeholder():
    """Возвращает placeholder для SQL запросов (%s для PostgreSQL, ? для SQLite)"""
    return "%s" if DB_TYPE == "postgresql" else "?"


def init_db():
    """Инициализирует все таблицы БД"""
    from datetime import datetime
    
    conn = _connect()
    cur = conn.cursor()

    # Основные таблицы
    if DB_TYPE == "postgresql":
        cur.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TEXT
            )"""
        )

        cur.execute(
            """CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                name TEXT,
                phone TEXT,
                address TEXT,
                comment TEXT,
                calc_data TEXT,
                status TEXT DEFAULT 'Новая',
                created_at TEXT,
                platform TEXT DEFAULT 'tg'
            )"""
        )

        cur.execute(
            """CREATE TABLE IF NOT EXISTS works (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                description TEXT,
                created_at TEXT
            )"""
        )

        cur.execute(
            """CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                author TEXT,
                text TEXT,
                created_at TEXT,
                approved INTEGER DEFAULT 1,
                user_id BIGINT
            )"""
        )

        cur.execute(
            """CREATE TABLE IF NOT EXISTS vk_users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT
            )"""
        )

        cur.execute(
            """CREATE TABLE IF NOT EXISTS vk_works (
                id SERIAL PRIMARY KEY,
                attachment TEXT UNIQUE,
                caption TEXT,
                added_at TEXT
            )"""
        )
    else:
        # SQLite
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
                created_at TEXT,
                platform TEXT DEFAULT 'tg'
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

    # Миграции: добавляем колонки если их нет
    if DB_TYPE == "sqlite":
        try:
            cur.execute("ALTER TABLE reviews ADD COLUMN approved INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE reviews ADD COLUMN user_id INTEGER")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE leads ADD COLUMN platform TEXT DEFAULT 'tg'")
        except Exception:
            pass

    # Дефолтные цены
    default_prices = {
        "Профнастил": 2500,
        "Металлический штакетник": 3200,
        "3D-сетка": 1800,
        "Рабица": 1200,
        "Дерево": 2800,
        "Комбинированный": 3500,
    }
    ph = _placeholder()
    for t, p in default_prices.items():
        if DB_TYPE == "postgresql":
            cur.execute(f"INSERT INTO prices (fence_type, price_per_m2) VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING", (t, p))
        else:
            cur.execute(f"INSERT OR IGNORE INTO prices VALUES ({ph}, {ph})", (t, p))

    # Дефолтные виды заборов
    default_types = [
        (
            "Профнастил",
            "🔩 <b>Забор из профнастила</b>\n\n"
            "Самый популярный вариант. Лист профилированной стали с полимерным покрытием на металлических столбах и лагах.\n\n"
            "• Срок службы: <b>20–30 лет</b>\n"
            "• Глухой, защищает от пыли и любопытных глаз\n"
            "• Большой выбор цветов по RAL\n"
            "• Не требует ухода",
        ),
        (
            "Металлический штакетник",
            "🪵 <b>Металлический штакетник (евроштакетник)</b>\n\n"
            "Стальные планки с полимерным покрытием — выглядит как классический деревянный штакетник, но не гниёт и не требует покраски.\n\n"
            "• Срок службы: <b>25–30 лет</b>\n"
            "• Полупрозрачный или глухой монтаж\n"
            "• Аккуратный современный вид\n"
            "• Хорошо продувается ветром",
        ),
        (
            "3D-сетка",
            "🔳 <b>3D-сетка (сварные панели)</b>\n\n"
            "Жёсткие сварные панели из прутка с полимерным покрытием. Часто ставят на дачах и придомовых территориях.\n\n"
            "• Срок службы: <b>15–25 лет</b>\n"
            "• Не парусит, выдерживает ветровые нагрузки\n"
            "• Быстрый монтаж\n"
            "• Пропускает свет на участок",
        ),
        (
            "Рабица",
            "🕸 <b>Забор из сетки-рабицы</b>\n\n"
            "Бюджетное решение для дачи, садового участка или зонирования территории.\n\n"
            "• Срок службы: <b>10–15 лет</b>\n"
            "• Самый доступный вариант\n"
            "• Пропускает свет\n"
            "• Можно с оцинковкой или ПВХ-покрытием",
        ),
        (
            "Дерево",
            "🌲 <b>Деревянный забор</b>\n\n"
            "Классика и натуральный вид. Доска или штакетник из обработанной древесины.\n\n"
            "• Срок службы: <b>10–20 лет</b> (с обработкой)\n"
            "• Тёплый, «домашний» внешний вид\n"
            "• Возможна покраска в любой цвет\n"
            "• Требует периодического обновления покрытия",
        ),
        (
            "Комбинированный",
            "🧱 <b>Комбинированный забор</b>\n\n"
            "Сочетание кирпичных/каменных столбов с пролётами из профнастила, штакетника или ковки. Премиум-вариант.\n\n"
            "• Срок службы: <b>40+ лет</b>\n"
            "• Самый презентабельный вид\n"
            "• Высокая прочность и шумоизоляция\n"
            "• Подходит для частных домов",
        ),
    ]
    now = datetime.now().isoformat()
    for name, desc in default_types:
        if DB_TYPE == "postgresql":
            cur.execute(
                f"INSERT INTO fence_types (name, description, created_at) VALUES ({ph}, {ph}, {ph}) ON CONFLICT DO NOTHING",
                (name, desc, now),
            )
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO fence_types (name, description, created_at) VALUES ({ph}, {ph}, {ph})",
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
                f"INSERT INTO reviews (author, text, created_at) VALUES ({ph}, {ph}, {ph})",
                (author, text, now),
            )

    conn.commit()
    conn.close()
    
    # Инициализируем таблицы синхронизации
    from sync_manager import init_sync_tables
    init_sync_tables()
    
    logger.info("✅ База данных инициализирована")


# Экспортируем функции
__all__ = ['_connect', '_placeholder', 'init_db', 'DB_TYPE']
