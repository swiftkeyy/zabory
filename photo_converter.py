"""
Конвертер фотографий между Telegram и VK.
Скачивает фото из одной платформы и загружает в другую.
ВАЖНО: Все функции синхронные для совместимости.
"""
import os
import logging
import tempfile
import requests
from typing import Optional

logger = logging.getLogger(__name__)


def download_tg_photo_sync(bot_token: str, file_id: str) -> Optional[str]:
    """
    Скачивает фото из Telegram и возвращает путь к временному файлу (синхронно).
    
    Args:
        bot_token: Telegram bot token
        file_id: Telegram file_id
    
    Returns:
        Путь к временному файлу или None при ошибке
    """
    try:
        # Получаем информацию о файле
        response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id},
            timeout=30
        )
        response.raise_for_status()
        
        file_path = response.json()["result"]["file_path"]
        
        # Скачиваем файл
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        file_response = requests.get(file_url, timeout=30)
        file_response.raise_for_status()
        
        # Создаем временный файл
        fd, temp_path = tempfile.mkstemp(suffix='.jpg', prefix='tg_photo_')
        os.close(fd)
        
        # Сохраняем фото
        with open(temp_path, 'wb') as f:
            f.write(file_response.content)
        
        logger.info(f"✅ Фото скачано из TG: {file_id} -> {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото из TG: {e}")
        return None


def upload_to_vk_sync(vk_token: str, photo_path: str, peer_id: int) -> Optional[str]:
    """
    Загружает фото в VK и возвращает attachment строку (синхронно).
    
    Args:
        vk_token: VK bot token
        photo_path: Путь к файлу фото
        peer_id: ID чата для загрузки (обычно ID админа)
    
    Returns:
        VK attachment строка (photo123_456) или None при ошибке
    """
    try:
        # Получаем upload URL
        response = requests.get(
            "https://api.vk.com/method/photos.getMessagesUploadServer",
            params={
                "access_token": vk_token,
                "peer_id": peer_id,
                "v": "5.131"
            },
            timeout=30
        )
        response.raise_for_status()
        
        upload_url = response.json()["response"]["upload_url"]
        
        # Загружаем фото на сервер VK
        with open(photo_path, 'rb') as f:
            upload_response = requests.post(
                upload_url,
                files={"photo": f},
                timeout=60
            )
        upload_response.raise_for_status()
        
        upload_data = upload_response.json()
        
        # Сохраняем фото
        save_response = requests.get(
            "https://api.vk.com/method/photos.saveMessagesPhoto",
            params={
                "access_token": vk_token,
                "photo": upload_data["photo"],
                "server": upload_data["server"],
                "hash": upload_data["hash"],
                "v": "5.131"
            },
            timeout=30
        )
        save_response.raise_for_status()
        
        photo_data = save_response.json()["response"][0]
        
        attachment = f"photo{photo_data['owner_id']}_{photo_data['id']}"
        if "access_key" in photo_data:
            attachment += f"_{photo_data['access_key']}"
        
        logger.info(f"✅ Фото загружено в VK: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки фото в VK: {e}")
        return None


def sync_photo_tg_to_vk(tg_token: str, vk_token: str, tg_file_id: str, vk_admin_id: int) -> Optional[str]:
    """
    Синхронизирует фото из Telegram в VK (синхронно).
    
    Args:
        tg_token: Telegram bot token
        vk_token: VK bot token
        tg_file_id: Telegram file_id
        vk_admin_id: VK ID админа для загрузки
    
    Returns:
        VK attachment или None при ошибке
    """
    temp_path = None
    try:
        # Скачиваем из TG
        temp_path = download_tg_photo_sync(tg_token, tg_file_id)
        if not temp_path:
            return None
        
        # Загружаем в VK
        vk_attachment = upload_to_vk_sync(vk_token, temp_path, vk_admin_id)
        return vk_attachment
    finally:
        # Удаляем временный файл
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                logger.info(f"🗑️ Временный файл удален: {temp_path}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось удалить временный файл: {e}")


# Экспортируем функции
__all__ = [
    'download_tg_photo_sync',
    'upload_to_vk_sync',
    'sync_photo_tg_to_vk'
]
