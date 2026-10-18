import os

import psycopg
import redis
import requests

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse


app = FastAPI(
    title="SCG Content Machine",
    version="1.1.0"
)


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")


def telegram_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    response = requests.post(
        url,
        json=payload,
        timeout=15
    )

    response.raise_for_status()

    return response.json()


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_api("sendMessage", payload)


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "system": "SCG Content Machine",
        "status": "ONLINE",
        "version": "1.1.0"
    }


# =========================================================
# HEALTHCHECK
# =========================================================

@app.get("/health")
def health():
    database_status = "disconnected"
    redis_status = "disconnected"

    # Check PostgreSQL
    try:
        database_url = os.getenv("DATABASE_URL")

        if not database_url:
            raise RuntimeError("DATABASE_URL is missing")

        with psycopg.connect(
            database_url,
            connect_timeout=5
        ) as conn:

            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        database_status = "connected"

    except Exception:
        database_status = "disconnected"

    # Check Redis
    try:
        redis_url = os.getenv("REDIS_URL")

        if not redis_url:
            raise RuntimeError("REDIS_URL is missing")

        redis_client = redis.from_url(
            redis_url,
            socket_connect_timeout=5,
            socket_timeout=5
        )

        redis_client.ping()

        redis_status = "connected"

    except Exception:
        redis_status = "disconnected"

    healthy = (
        database_status == "connected"
        and redis_status == "connected"
    )

    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "service": "content-machine",
        "version": "1.1.0",
        "database": database_status,
        "redis": redis_status
    }

    return JSONResponse(
        payload,
        status_code=200 if healthy else 503
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None)
):
    # Security guard
    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Telegram webhook secret is not configured"
        )

    if x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook secret"
        )

    update = await request.json()

    message = update.get("message")

    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id:
        return {"ok": True}

    text = message.get("text", "")

    # -----------------------------------------------------
    # /start
    # -----------------------------------------------------

    if text == "/start":

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "SCG",
                        "callback_data": "brand:SCG"
                    },
                    {
                        "text": "ILMI",
                        "callback_data": "brand:ILMI"
                    }
                ]
            ]
        }

        send_message(
            chat_id,
            "👋 Selamat datang di SCG Content Machine.\n\n"
            "Pilih brand yang ingin dibuatkan konten:",
            keyboard
        )

        return {"ok": True}

    send_message(
        chat_id,
        "Ketik /start untuk memulai Content Machine."
    )

    return {"ok": True}
@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None)
):
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")

    if not webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_WEBHOOK_SECRET is missing"
        )

    if x_telegram_bot_api_secret_token != webhook_secret:
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook secret"
        )

    update = await request.json()

    print("TELEGRAM UPDATE:", update)

    return {
        "ok": True,
        "received": True
    }
