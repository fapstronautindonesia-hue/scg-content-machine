import json
import time
from app.config import settings
from app.redis_client import get_redis


def _key(chat_id: int) -> str:
    return f"contentmachine:draft:{chat_id}"


def _pref_key(chat_id: int) -> str:
    return f"contentmachine:preferences:{chat_id}"


def load(chat_id: int) -> dict:
    raw = get_redis().get(_key(chat_id))
    if not raw:
        return {"chat_id": chat_id, "photos": [], "instruction": "", "last_update_at": 0.0}
    value = json.loads(raw)
    value.setdefault("photos", [])
    value.setdefault("instruction", "")
    return value


def save(chat_id: int, draft: dict) -> dict:
    draft["chat_id"] = chat_id
    draft["last_update_at"] = time.time()
    get_redis().setex(_key(chat_id), settings.draft_ttl, json.dumps(draft, ensure_ascii=False))
    return draft


def add_photo(chat_id: int, photo: dict, caption: str | None = None) -> dict:
    draft = load(chat_id)
    existing = {p.get("file_unique_id") or p.get("file_id") for p in draft["photos"]}
    identity = photo.get("file_unique_id") or photo.get("file_id")
    if identity not in existing and len(draft["photos"]) < settings.max_batch_photos:
        draft["photos"].append(photo)
    if caption and caption.strip():
        draft["instruction"] = caption.strip()
    return save(chat_id, draft)


def set_instruction(chat_id: int, instruction: str) -> dict:
    draft = load(chat_id)
    draft["instruction"] = instruction.strip()
    return save(chat_id, draft)


def append_instruction(chat_id: int, instruction: str) -> dict:
    draft = load(chat_id)
    old = (draft.get("instruction") or "").strip()
    new = instruction.strip()
    draft["instruction"] = (old + "\n" + new).strip() if old else new
    return save(chat_id, draft)


def clear(chat_id: int) -> None:
    get_redis().delete(_key(chat_id))


def save_preferences(chat_id: int, brand: str | None = None, channel: str | None = None) -> None:
    current = preferences(chat_id)
    if brand:
        current["last_brand"] = brand
    if channel:
        current["last_channel"] = channel
    get_redis().setex(_pref_key(chat_id), 60 * 60 * 24 * 30, json.dumps(current, ensure_ascii=False))


def preferences(chat_id: int) -> dict:
    raw = get_redis().get(_pref_key(chat_id))
    return json.loads(raw) if raw else {}
