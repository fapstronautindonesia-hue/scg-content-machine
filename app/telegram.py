import json
from io import BytesIO
from typing import Any, Dict, Optional
import requests
from app.config import settings


def telegram_api(method: str, payload: Optional[Dict[str, Any]] = None, files=None) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    if files:
        response = requests.post(url, data=payload or {}, files=files, timeout=settings.telegram_timeout)
    else:
        response = requests.post(url, json=payload or {}, timeout=settings.telegram_timeout)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error on {method}: {data.get('description')}")
    return data


def send_message(chat_id: int, text: str, reply_markup=None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)


def answer_callback(callback_query_id: str, text: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    telegram_api("answerCallbackQuery", payload)


def get_file(file_id: str) -> Dict[str, Any]:
    return telegram_api("getFile", {"file_id": file_id})["result"]


def download_file(file_path: str) -> bytes:
    # Never log this URL: it contains the bot token.
    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    response = requests.get(url, timeout=settings.telegram_timeout)
    response.raise_for_status()
    content = response.content
    if not content:
        raise ValueError("Downloaded Telegram file is empty")
    if len(content) > settings.max_image_bytes:
        raise ValueError("Telegram image exceeds worker size limit")
    return content


def send_photo(chat_id: int, image_bytes: bytes, caption: str = "", reply_markup=None, filename: str = "preview.jpg") -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": str(chat_id), "caption": caption}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    files = {"photo": (filename, image_bytes, "image/jpeg")}
    return telegram_api("sendPhoto", payload=payload, files=files)


def extract_sent_photo_file_id(response: Dict[str, Any]) -> Optional[str]:
    result = response.get("result") or {}
    photos = result.get("photo") or []
    if not photos:
        return None
    return photos[-1].get("file_id")
