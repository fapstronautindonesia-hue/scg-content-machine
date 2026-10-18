import os

import psycopg
import redis
import requests

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="SCG Content Machine",
    version="1.2.0"
)


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")


# =========================================================
# TELEGRAM HELPERS
# =========================================================

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


def answer_callback_query(callback_query_id):
    payload = {
        "callback_query_id": callback_query_id
    }

    return telegram_api("answerCallbackQuery", payload)


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "system": "SCG Content Machine",
        "status": "ONLINE",
        "version": "1.2.0"
    }


# =========================================================
# HEALTHCHECK
# =========================================================

@app.get("/health")
def health():
    database_status = "disconnected"
    redis_status = "disconnected"

    # PostgreSQL
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

    # Redis
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
        "version": "1.2.0",
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

    # -----------------------------------------------------
    # SECURITY
    # -----------------------------------------------------

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

    print("TELEGRAM UPDATE:", update)


    # =====================================================
    # CALLBACK QUERY / BUTTON
    # =====================================================

    callback_query = update.get("callback_query")

    if callback_query:

        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")

        callback_message = callback_query.get("message", {})
        callback_chat = callback_message.get("chat", {})
        callback_chat_id = callback_chat.get("id")

        if callback_id:
            answer_callback_query(callback_id)


        # -------------------------------------------------
        # BRAND: SCG
        # -------------------------------------------------

        if callback_data == "brand:SCG":

            format_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Feed",
                            "callback_data": "format:SCG:FEED"
                        },
                        {
                            "text": "Carousel",
                            "callback_data": "format:SCG:CAROUSEL"
                        }
                    ],
                    [
                        {
                            "text": "Story",
                            "callback_data": "format:SCG:STORY"
                        },
                        {
                            "text": "Feed + Story",
                            "callback_data": "format:SCG:FEED_STORY"
                        }
                    ]
                ]
            }

            send_message(
                callback_chat_id,
                "✅ Brand dipilih: SCG\n\n"
                "Sekarang pilih format konten:",
                format_keyboard
            )


        # -------------------------------------------------
        # BRAND: ILMI
        # -------------------------------------------------

        elif callback_data == "brand:ILMI":

            format_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Feed",
                            "callback_data": "format:ILMI:FEED"
                        },
                        {
                            "text": "Carousel",
                            "callback_data": "format:ILMI:CAROUSEL"
                        }
                    ],
                    [
                        {
                            "text": "Story",
                            "callback_data": "format:ILMI:STORY"
                        },
                        {
                            "text": "Feed + Story",
                            "callback_data": "format:ILMI:FEED_STORY"
                        }
                    ]
                ]
            }

            send_message(
                callback_chat_id,
                "✅ Brand dipilih: ILMI\n\n"
                "Sekarang pilih format konten:",
                format_keyboard
            )


        # -------------------------------------------------
        # FORMAT SELECTED
        # -------------------------------------------------

        elif callback_data.startswith("format:"):

            parts = callback_data.split(":")

            if len(parts) == 3:

                brand = parts[1]
                content_format = parts[2]

                objective_keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🤖 AI Otomatis",
                                "callback_data": f"objective:{brand}:{content_format}:AUTO"
                            }
                        ],
                        [
                            {
                                "text": "📚 Edukasi",
                                "callback_data": f"objective:{brand}:{content_format}:EDUKASI"
                            },
                            {
                                "text": "✨ Showcase",
                                "callback_data": f"objective:{brand}:{content_format}:SHOWCASE"
                            }
                        ],
                        [
                            {
                                "text": "🔥 Promo",
                                "callback_data": f"objective:{brand}:{content_format}:PROMO"
                            },
                            {
                                "text": "💬 Testimoni",
                                "callback_data": f"objective:{brand}:{content_format}:TESTIMONI"
                            }
                        ]
                    ]
                }

                send_message(
                    callback_chat_id,
                    f"✅ Format dipilih: {content_format}\n\n"
                    "Sekarang pilih tujuan konten:",
                    objective_keyboard
                )


        # -------------------------------------------------
        # OBJECTIVE SELECTED
        # -------------------------------------------------

        elif callback_data.startswith("objective:"):

            parts = callback_data.split(":")

            if len(parts) == 4:

                brand = parts[1]
                content_format = parts[2]
                objective = parts[3]

                send_message(
                    callback_chat_id,
                    "✅ Brief konten berhasil dibuat.\n\n"
                    f"🏷 Brand: {brand}\n"
                    f"🖼 Format: {content_format}\n"
                    f"🎯 Objective: {objective}\n\n"
                    "📸 Sekarang kirim foto yang ingin dibuatkan konten."
                )

        return {"ok": True}


    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

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

        brand_keyboard = {
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
            brand_keyboard
        )

        return {"ok": True}


    # -----------------------------------------------------
    # DEFAULT
    # -----------------------------------------------------

    send_message(
        chat_id,
        "Ketik /start untuk memulai Content Machine."
    )

    return {"ok": True}
