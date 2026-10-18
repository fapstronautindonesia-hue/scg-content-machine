import json
from app.config import settings
from app.redis_client import get_redis


def key(chat_id: int) -> str:
    return f"content_session:{chat_id}"


def save(chat_id: int, data: dict) -> None:
    get_redis().setex(key(chat_id), settings.session_ttl, json.dumps(data, ensure_ascii=False))


def load(chat_id: int) -> dict | None:
    raw = get_redis().get(key(chat_id))
    return json.loads(raw) if raw else None


def clear(chat_id: int) -> None:
    get_redis().delete(key(chat_id))
