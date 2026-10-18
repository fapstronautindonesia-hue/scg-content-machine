import os
import json
import uuid

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
    version="1.4.0"
)


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")

SESSION_TTL = 3600

AI_ANALYSIS_QUEUE = os.getenv(
    "AI_ANALYSIS_QUEUE",
    "contentmachine:analysis_queue"
)


# =========================================================
# REDIS
# =========================================================

def get_redis():
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is missing")

    return redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5
    )


# =========================================================
# SESSION
# =========================================================

def session_key(chat_id):
    return f"content_session:{chat_id}"


def save_session(chat_id, data):
    client = get_redis()

    client.setex(
        session_key(chat_id),
        SESSION_TTL,
        json.dumps(data)
    )


def get_session(chat_id):
    client = get_redis()

    raw = client.get(session_key(chat_id))

    if not raw:
        return None

    return json.loads(raw)


def delete_session(chat_id):
    client = get_redis()
    client.delete(session_key(chat_id))


# =========================================================
# AI ANALYSIS QUEUE
# =========================================================

def enqueue_analysis(
    job_id,
    chat_id,
    file_path,
    brand,
    content_format,
    objective,
    mime_type="image/jpeg"
):
    """
    Masukkan task ke Redis.

    Webhook TIDAK menjalankan AI secara langsung.
    Worker V1.4 akan mengambil task ini secara asynchronous.
    """

    client = get_redis()

    task = {
        "job_id": str(job_id),
        "chat_id": int(chat_id),
        "file_path": file_path,
        "brand": brand,
        "format": content_format,
        "objective": objective,
        "mime_type": mime_type
    }

    client.rpush(
        AI_ANALYSIS_QUEUE,
        json.dumps(task)
    )

    return task


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def telegram_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        json=payload,
        timeout=15
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error on {method}"
        )

    return data


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_api(
        "sendMessage",
        payload
    )


def answer_callback_query(callback_query_id):
    return telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id
        }
    )


def get_telegram_file(file_id):
    response = telegram_api(
        "getFile",
        {
            "file_id": file_id
        }
    )

    result = response.get("result")

    if not result:
        raise RuntimeError(
            "Telegram getFile returned no result"
        )

    return result


# =========================================================
# DATABASE
# =========================================================

def create_job_and_asset(
    chat_id,
    message_id,
    brand,
    content_format,
    objective,
    telegram_file_id,
    telegram_file_unique_id,
    telegram_file_path
):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing")

    job_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    external_id = (
        f"telegram:{chat_id}:{message_id}"
    )

    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=5
    ) as conn:

        with conn.cursor() as cur:

            # -------------------------------------------------
            # CREATE JOB
            # -------------------------------------------------

            cur.execute(
                """
                INSERT INTO jobs (
                    id,
                    source,
                    external_id,
                    brand,
                    format,
                    objective,
                    status,
                    requested_by
                )
                VALUES (
                    %s,
                    'telegram',
                    %s,
                    %s,
                    %s,
                    %s,
                    'RECEIVED',
                    %s
                )
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    job_id,
                    external_id,
                    brand,
                    content_format,
                    objective,
                    str(chat_id)
                )
            )

            inserted_job = cur.fetchone()

            # -------------------------------------------------
            # DUPLICATE PROTECTION
            # -------------------------------------------------

            if not inserted_job:

                cur.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE source = 'telegram'
                    AND external_id = %s
                    LIMIT 1
                    """,
                    (external_id,)
                )

                existing_job = cur.fetchone()

                if not existing_job:
                    raise RuntimeError(
                        "Duplicate detected but "
                        "existing job not found"
                    )

                return str(existing_job[0]), False

            # -------------------------------------------------
            # CREATE ASSET
            # -------------------------------------------------

            metadata = {
                "telegram_file_id":
                    telegram_file_id,
                "telegram_file_unique_id":
                    telegram_file_unique_id,
                "telegram_file_path":
                    telegram_file_path,
                "telegram_chat_id":
                    chat_id,
                "telegram_message_id":
                    message_id
            }

            original_filename = (
                telegram_file_path.split("/")[-1]
                if telegram_file_path
                else None
            )

            cur.execute(
                """
                INSERT INTO assets (
                    id,
                    job_id,
                    asset_type,
                    storage_provider,
                    storage_key,
                    original_filename,
                    mime_type,
                    metadata
                )
                VALUES (
                    %s,
                    %s,
                    'IMAGE',
                    'telegram',
                    %s,
                    %s,
                    %s,
                    %s::jsonb
                )
                """,
                (
                    asset_id,
                    job_id,
                    telegram_file_path,
                    original_filename,
                    "image/jpeg",
                    json.dumps(metadata)
                )
            )

        conn.commit()

    return str(job_id), True


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "system": "SCG Content Machine",
        "status": "ONLINE",
        "version": "1.4.0"
    }


# =========================================================
# HEALTHCHECK
# =========================================================

@app.get("/health")
def health():

    database_status = "disconnected"
    redis_status = "disconnected"

    try:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is missing"
            )

        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=5
        ) as conn:

            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        database_status = "connected"

    except Exception as exc:
        print(
            "DATABASE HEALTH ERROR:",
            repr(exc)
        )

    try:
        client = get_redis()
        client.ping()

        redis_status = "connected"

    except Exception as exc:
        print(
            "REDIS HEALTH ERROR:",
            repr(exc)
        )

    healthy = (
        database_status == "connected"
        and redis_status == "connected"
    )

    payload = {
        "status":
            "healthy"
            if healthy
            else "unhealthy",
        "service": "content-machine",
        "version": "1.4.0",
        "database": database_status,
        "redis": redis_status
    }

    return JSONResponse(
        payload,
        status_code=(
            200
            if healthy
            else 503
        )
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None
    )
):

    # =====================================================
    # SECURITY
    # =====================================================

    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail=(
                "Telegram webhook secret "
                "is not configured"
            )
        )

    if (
        x_telegram_bot_api_secret_token
        != TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook secret"
        )

    update = await request.json()

    print(
        "TELEGRAM UPDATE RECEIVED"
    )

    # =====================================================
    # CALLBACK QUERY
    # =====================================================

    callback_query = update.get(
        "callback_query"
    )

    if callback_query:

        callback_id = callback_query.get(
            "id"
        )

        callback_data = callback_query.get(
            "data",
            ""
        )

        callback_message = callback_query.get(
            "message",
            {}
        )

        callback_chat = callback_message.get(
            "chat",
            {}
        )

        callback_chat_id = callback_chat.get(
            "id"
        )

        if callback_id:
            try:
                answer_callback_query(
                    callback_id
                )
            except Exception as exc:
                print(
                    "CALLBACK ANSWER ERROR:",
                    repr(exc)
                )

        if not callback_chat_id:
            return {"ok": True}

        # =================================================
        # BRAND SCG
        # =================================================

        if callback_data == "brand:SCG":

            format_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Feed",
                            "callback_data":
                                "format:SCG:FEED"
                        },
                        {
                            "text": "Carousel",
                            "callback_data":
                                "format:SCG:CAROUSEL"
                        }
                    ],
                    [
                        {
                            "text": "Story",
                            "callback_data":
                                "format:SCG:STORY"
                        },
                        {
                            "text": "Feed + Story",
                            "callback_data":
                                "format:SCG:FEED_STORY"
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

        # =================================================
        # BRAND ILMI
        # =================================================

        elif callback_data == "brand:ILMI":

            format_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Feed",
                            "callback_data":
                                "format:ILMI:FEED"
                        },
                        {
                            "text": "Carousel",
                            "callback_data":
                                "format:ILMI:CAROUSEL"
                        }
                    ],
                    [
                        {
                            "text": "Story",
                            "callback_data":
                                "format:ILMI:STORY"
                        },
                        {
                            "text": "Feed + Story",
                            "callback_data":
                                "format:ILMI:FEED_STORY"
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

        # =================================================
        # FORMAT
        # =================================================

        elif callback_data.startswith(
            "format:"
        ):

            parts = callback_data.split(":")

            if len(parts) == 3:

                brand = parts[1]
                content_format = parts[2]

                objective_keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text":
                                    "🤖 AI Otomatis",
                                "callback_data":
                                    (
                                        f"objective:"
                                        f"{brand}:"
                                        f"{content_format}:"
                                        "AUTO"
                                    )
                            }
                        ],
                        [
                            {
                                "text":
                                    "📚 Edukasi",
                                "callback_data":
                                    (
                                        f"objective:"
                                        f"{brand}:"
                                        f"{content_format}:"
                                        "EDUKASI"
                                    )
                            },
                            {
                                "text":
                                    "✨ Showcase",
                                "callback_data":
                                    (
                                        f"objective:"
                                        f"{brand}:"
                                        f"{content_format}:"
                                        "SHOWCASE"
                                    )
                            }
                        ],
                        [
                            {
                                "text":
                                    "🔥 Promo",
                                "callback_data":
                                    (
                                        f"objective:"
                                        f"{brand}:"
                                        f"{content_format}:"
                                        "PROMO"
                                    )
                            },
                            {
                                "text":
                                    "💬 Testimoni",
                                "callback_data":
                                    (
                                        f"objective:"
                                        f"{brand}:"
                                        f"{content_format}:"
                                        "TESTIMONI"
                                    )
                            }
                        ]
                    ]
                }

                send_message(
                    callback_chat_id,
                    (
                        "✅ Format dipilih: "
                        f"{content_format}\n\n"
                        "Sekarang pilih tujuan konten:"
                    ),
                    objective_keyboard
                )

        # =================================================
        # OBJECTIVE
        # =================================================

        elif callback_data.startswith(
            "objective:"
        ):

            parts = callback_data.split(":")

            if len(parts) == 4:

                brand = parts[1]
                content_format = parts[2]
                objective = parts[3]

                save_session(
                    callback_chat_id,
                    {
                        "brand":
                            brand,
                        "format":
                            content_format,
                        "objective":
                            objective,
                        "status":
                            "WAITING_PHOTO"
                    }
                )

                send_message(
                    callback_chat_id,
                    (
                        "✅ Brief konten berhasil dibuat."
                        "\n\n"
                        f"🏷 Brand: {brand}\n"
                        f"🖼 Format: {content_format}\n"
                        f"🎯 Objective: {objective}"
                        "\n\n"
                        "📸 Sekarang kirim foto yang "
                        "ingin dibuatkan konten."
                    )
                )

        return {"ok": True}

    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

    message = update.get("message")

    if not message:
        return {"ok": True}

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get("id")

    if not chat_id:
        return {"ok": True}

    text = message.get(
        "text",
        ""
    )

    # =====================================================
    # /START
    # =====================================================

    if text == "/start":

        try:
            delete_session(chat_id)

        except Exception as exc:
            print(
                "SESSION RESET ERROR:",
                repr(exc)
            )

        brand_keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "SCG",
                        "callback_data":
                            "brand:SCG"
                    },
                    {
                        "text": "ILMI",
                        "callback_data":
                            "brand:ILMI"
                    }
                ]
            ]
        }

        send_message(
            chat_id,
            (
                "👋 Selamat datang di "
                "SCG Content Machine.\n\n"
                "Pilih brand yang ingin "
                "dibuatkan konten:"
            ),
            brand_keyboard
        )

        return {"ok": True}

    # =====================================================
    # PHOTO RECEIVED
    # =====================================================

    photos = message.get("photo")

    if photos:

        # -------------------------------------------------
        # LOAD SESSION
        # -------------------------------------------------

        try:
            session = get_session(
                chat_id
            )

        except Exception as exc:
            print(
                "SESSION READ ERROR:",
                repr(exc)
            )

            send_message(
                chat_id,
                (
                    "❌ Sistem gagal membaca brief "
                    "konten.\n\n"
                    "Silakan ketik /start "
                    "dan coba lagi."
                )
            )

            return {"ok": True}

        if not session:

            send_message(
                chat_id,
                (
                    "⚠️ Brief konten tidak ditemukan "
                    "atau sudah kedaluwarsa.\n\n"
                    "Ketik /start untuk membuat "
                    "brief baru."
                )
            )

            return {"ok": True}

        if (
            session.get("status")
            != "WAITING_PHOTO"
        ):

            send_message(
                chat_id,
                (
                    "⚠️ Sistem belum menunggu foto."
                    "\n\n"
                    "Ketik /start untuk memulai ulang."
                )
            )

            return {"ok": True}

        # -------------------------------------------------
        # HIGHEST RESOLUTION PHOTO
        # -------------------------------------------------

        photo = photos[-1]

        telegram_file_id = photo.get(
            "file_id"
        )

        telegram_file_unique_id = photo.get(
            "file_unique_id"
        )

        if not telegram_file_id:

            send_message(
                chat_id,
                (
                    "❌ File foto tidak valid. "
                    "Silakan kirim ulang."
                )
            )

            return {"ok": True}

        # -------------------------------------------------
        # GET TELEGRAM FILE + DATABASE
        # -------------------------------------------------

        try:

            telegram_file = get_telegram_file(
                telegram_file_id
            )

            telegram_file_path = (
                telegram_file.get(
                    "file_path"
                )
            )

            if not telegram_file_path:
                raise RuntimeError(
                    "Telegram file_path is missing"
                )

            job_id, created = (
                create_job_and_asset(
                    chat_id=chat_id,
                    message_id=message.get(
                        "message_id"
                    ),
                    brand=session["brand"],
                    content_format=session["format"],
                    objective=session["objective"],
                    telegram_file_id=(
                        telegram_file_id
                    ),
                    telegram_file_unique_id=(
                        telegram_file_unique_id
                    ),
                    telegram_file_path=(
                        telegram_file_path
                    )
                )
            )

            # =================================================
            # NEW JOB
            # =================================================

            if created:

                # ---------------------------------------------
                # ACKNOWLEDGE INGESTION FIRST
                # ---------------------------------------------

                send_message(
                    chat_id,
                    (
                        "✅ Foto berhasil diterima."
                        "\n\n"
                        f"🆔 Job: {job_id}\n"
                        f"🏷 Brand: "
                        f"{session['brand']}\n"
                        f"🖼 Format: "
                        f"{session['format']}\n"
                        f"🎯 Objective: "
                        f"{session['objective']}"
                        "\n\n"
                        "🧠 Foto sudah masuk ke "
                        "Content Machine.\n"
                        "AI akan menganalisis foto."
                    )
                )

                # ---------------------------------------------
                # AI QUEUE
                # ---------------------------------------------

                try:

                    enqueue_analysis(
                        job_id=job_id,
                        chat_id=chat_id,
                        file_path=telegram_file_path,
                        brand=session["brand"],
                        content_format=session["format"],
                        objective=session["objective"],
                        mime_type="image/jpeg"
                    )

                    print(
                        "AI ANALYSIS ENQUEUED:",
                        job_id
                    )

                except Exception as queue_exc:

                    # PENTING:
                    # Job + asset sudah committed.
                    # Queue gagal TIDAK boleh membuat
                    # user mengira foto gagal disimpan.

                    print(
                        "AI QUEUE ERROR:",
                        job_id,
                        repr(queue_exc)
                    )

                    send_message(
                        chat_id,
                        (
                            "⚠️ Foto sudah aman tersimpan, "
                            "tetapi antrean analisis AI "
                            "belum berhasil dibuat.\n\n"
                            f"🆔 Job: {job_id}"
                        )
                    )

                # Session dihapus setelah ingestion selesai.
                delete_session(
                    chat_id
                )

            # =================================================
            # DUPLICATE
            # =================================================

            else:

                send_message(
                    chat_id,
                    (
                        "ℹ️ Foto ini sudah pernah "
                        "diterima.\n\n"
                        f"🆔 Job: {job_id}"
                    )
                )

            return {"ok": True}

        except Exception as exc:

            print(
                "PHOTO PROCESSING ERROR:",
                repr(exc)
            )

            send_message(
                chat_id,
                (
                    "❌ Foto diterima Telegram, "
                    "tetapi gagal dimasukkan ke "
                    "Content Machine.\n\n"
                    "Silakan coba lagi beberapa saat."
                )
            )

            return {"ok": True}

    # =====================================================
    # DEFAULT
    # =====================================================

    send_message(
        chat_id,
        "Ketik /start untuk memulai Content Machine."
    )

    return {"ok": True}
