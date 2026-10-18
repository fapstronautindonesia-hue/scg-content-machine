import json
import redis
from app.config import settings


def get_redis():
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=35,
    )


def enqueue(queue_name: str, payload: dict) -> None:
    get_redis().rpush(queue_name, json.dumps(payload, ensure_ascii=False))


def claim(key: str, ttl: int) -> bool:
    return bool(get_redis().set(key, "1", nx=True, ex=ttl))


def release(key: str) -> None:
    get_redis().delete(key)


def ping_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
