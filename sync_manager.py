"""
Менеджер синхронизации данных между Telegram и VK ботами.
Обеспечивает единое хранилище для работ, отзывов и других данных.
"""
import logging
from datetime import datetime
from typing import Optional, List, Tuple
from database import _connect, _placeholder, DB_TYPE

logger = logging.getLogger(__name__)


# ====================== РАБОТЫ (ФОТОГРАФИИ) ======================

def add_work(file_id: Optional[str], vk_attachment: Optional[str], caption: str, platform: str) -> int:
    """
    Добавляет работу (фото) в общую таблицу.
    
    Args:
        file_id: Telegram file_id (или None для VK)
        vk_attachment: VK attachment (или None для TG)
        caption: Подпись к фото
        platform: 'tg' или 'vk'
    
    Returns:
        ID добавленной работы
    """
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    
    try:
        if DB_TYPE == "postgresql":
            cur.execute(
                f"INSERT INTO works_unified (tg_file_id, vk_attachment, caption, platform, added_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}) RETURNING id",
                (file_id, vk_attachment, caption, platform, datetime.now().isoformat())
            )
            work_id = cur.fetchone()[0]
        else:
            cur.execute(
                f"INSERT INTO works_unified (tg_file_id, vk_attachment, caption, platform, added_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                (file_id, vk_attachment, caption, platform, datetime.now().isoformat())
            )
            work_id = cur.lastrowid
        
        conn.commit()
        logger.info(f"Работа добавлена: ID={work_id}, platform={platform}")
        return work_id
    except Exception as e:
        logger.error(f"Ошибка добавления работы: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def get_works_unified(offset: int, limit: int, platform: Optional[str] = None) -> Tuple[List, int]:
    """
    Получает список работ из общей таблицы.
    
    Args:
        offset: Смещение
        limit: Лимит
        platform: Фильтр по платформе ('tg', 'vk' или None для всех)
    
    Returns:
        (список работ, общее количество)
    """
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    
    try:
        if platform:
            cur.execute(f"SELECT COUNT(*) FROM works_unified WHERE platform = {ph}", (platform,))
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT id, tg_file_id, vk_attachment, caption, platform, added_at "
                f"FROM works_unified WHERE platform = {ph} ORDER BY id DESC LIMIT {ph} OFFSET {ph}",
                (platform, limit, offset)
            )
        else:
            cur.execute("SELECT COUNT(*) FROM works_unified")
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT id, tg_file_id, vk_attachment, caption, platform, added_at "
                f"FROM works_unified ORDER BY id DESC LIMIT {ph} OFFSET {ph}",
                (limit, offset)
            )
        
        rows = cur.fetchall()
        return rows, total
    finally:
        conn.close()


def delete_work_unified(work_id: int) -> bool:
    """Удаляет работу из общей таблицы"""
    conn = _connect()
    cur = conn.cursor()
    ph = _placeholder()
    
    try:
        cur.execute(f"DELETE FROM works_unified WHERE id = {ph}", (work_id,))
        conn.commit()
        logger.info(f"Работа удалена: ID={work_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления работы: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


# ====================== МИГРАЦИЯ ДАННЫХ ======================

def migrate_old_works():
    """
    Мигрирует данные из старых таблиц works и vk_works в новую works_unified.
    Вызывается автоматически при инициализации БД.
    """
    conn = _connect()
    cur = conn.cursor()
    
    try:
        # Проверяем, есть ли данные в старых таблицах
        cur.execute("SELECT COUNT(*) FROM works")
        tg_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM vk_works")
        vk_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM works_unified")
        unified_count = cur.fetchone()[0]
        
        # Если в новой таблице уже есть данные, не мигрируем
        if unified_count > 0:
            logger.info(f"Миграция не требуется: works_unified уже содержит {unified_count} записей")
            return
        
        migrated = 0
        
        # Мигрируем из works (Telegram)
        if tg_count > 0:
            cur.execute("SELECT file_id, caption, added_at FROM works ORDER BY id")
            for file_id, caption, added_at in cur.fetchall():
                ph = _placeholder()
                cur.execute(
                    f"INSERT INTO works_unified (tg_file_id, vk_attachment, caption, platform, added_at) "
                    f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                    (file_id, None, caption or "", "tg", added_at)
                )
                migrated += 1
        
        # Мигрируем из vk_works (VK)
        if vk_count > 0:
            cur.execute("SELECT attachment, caption, added_at FROM vk_works ORDER BY id")
            for attachment, caption, added_at in cur.fetchall():
                ph = _placeholder()
                cur.execute(
                    f"INSERT INTO works_unified (tg_file_id, vk_attachment, caption, platform, added_at) "
                    f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                    (None, attachment, caption or "", "vk", added_at)
                )
                migrated += 1
        
        conn.commit()
        
        if migrated > 0:
            logger.info(f"Миграция завершена: перенесено {migrated} работ (TG: {tg_count}, VK: {vk_count})")
        else:
            logger.info("Нет данных для миграции")
            
    except Exception as e:
        logger.error(f"Ошибка миграции: {e}")
        conn.rollback()
    finally:
        conn.close()


# ====================== ИНИЦИАЛИЗАЦИЯ ======================

def init_sync_tables():
    """Создает таблицы для синхронизации данных между ботами"""
    conn = _connect()
    cur = conn.cursor()
    
    try:
        # Создаем единую таблицу для работ (фотографий)
        if DB_TYPE == "postgresql":
            sql = '''CREATE TABLE IF NOT EXISTS works_unified (
                id SERIAL PRIMARY KEY,
                tg_file_id TEXT,
                vk_attachment TEXT,
                caption TEXT,
                platform TEXT NOT NULL,
                added_at TEXT NOT NULL,
                CONSTRAINT check_platform CHECK (platform IN ('tg', 'vk'))
            )'''
            cur.execute(sql)
        else:
            sql = '''CREATE TABLE IF NOT EXISTS works_unified (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_file_id TEXT,
                vk_attachment TEXT,
                caption TEXT,
                platform TEXT NOT NULL CHECK (platform IN ('tg', 'vk')),
                added_at TEXT NOT NULL
            )'''
            cur.execute(sql)
        
        # Создаем индексы для быстрого поиска
        cur.execute("CREATE INDEX IF NOT EXISTS idx_works_platform ON works_unified(platform)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_works_added_at ON works_unified(added_at DESC)")
        
        conn.commit()
        logger.info("Таблицы синхронизации созданы")
        
        # Мигрируем старые данные
        migrate_old_works()
        
    except Exception as e:
        logger.error(f"Ошибка создания таблиц синхронизации: {e}")
        conn.rollback()
    finally:
        conn.close()


# Экспортируем функции
__all__ = [
    'add_work',
    'get_works_unified',
    'delete_work_unified',
    'init_sync_tables',
    'migrate_old_works'
]
