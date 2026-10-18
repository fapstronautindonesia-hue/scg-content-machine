import os
import json
import uuid

import psycopg
import redis
import requests

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse


# =========================================================
# SCG CONTENT MACHINE
# V1.5.0
#
# FLOW:
# Telegram
# -> Brief
# -> Photo
# -> PostgreSQL
# -> analysis_queue
# -> Vision Worker
# -> [Buat Konten]
# -> content_queue
# -> Content Worker
# =========================================================


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="SCG Content Machine",
    version="1.5.0"
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

AI_CONTENT_QUEUE = os.getenv(
    "AI_CONTENT_QUEUE",
    "contentmachine:content_queue"
)


# =========================================================
# REDIS
# =========================================================

def get_redis():

    if not REDIS_URL:
        raise RuntimeError(
            "REDIS_URL is missing"
        )

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


def save_session(
    chat_id,
    data
):

    client = get_redis()

    client.setex(
        session_key(chat_id),
        SESSION_TTL,
        json.dumps(data)
    )


def get_session(
    chat_id
):

    client = get_redis()

    raw = client.get(
        session_key(chat_id)
    )

    if not raw:
        return None

    return json.loads(
        raw
    )


def delete_session(
    chat_id
):

    client = get_redis()

    client.delete(
        session_key(chat_id)
    )


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
# CONTENT GENERATION QUEUE
# =========================================================

def enqueue_content_generation(
    job_id,
    chat_id
):

    client = get_redis()

    task = {
        "job_id": str(job_id),
        "chat_id": int(chat_id)
    }

    client.rpush(
        AI_CONTENT_QUEUE,
        json.dumps(task)
    )

    return task


# =========================================================
# CONTENT QUEUE IDEMPOTENCY
# =========================================================

def content_claim_key(
    job_id
):

    return (
        "contentmachine:"
        f"content_claim:{job_id}"
    )


def claim_content_generation(
    job_id
):

    client = get_redis()

    # Prevent repeated taps from generating
    # the same content multiple times.
    #
    # TTL = 30 minutes.
    claimed = client.set(
        content_claim_key(job_id),
        "1",
        nx=True,
        ex=1800
    )

    return bool(
        claimed
    )


def release_content_generation_claim(
    job_id
):

    try:

        client = get_redis()

        client.delete(
            content_claim_key(job_id)
        )

    except Exception as exc:

        print(
            "CONTENT CLAIM RELEASE ERROR:",
            repr(exc)
        )


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def telegram_api(
    method: str,
    payload: dict
):

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    url = (
        "https://api.telegram.org/"
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


def send_message(
    chat_id,
    text,
    reply_markup=None
):

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:

        payload[
            "reply_markup"
        ] = reply_markup

    return telegram_api(
        "sendMessage",
        payload
    )


def answer_callback_query(
    callback_query_id,
    text=None
):

    payload = {
        "callback_query_id":
            callback_query_id
    }

    if text:

        payload["text"] = text

    return telegram_api(
        "answerCallbackQuery",
        payload
    )


def get_telegram_file(
    file_id
):

    response = telegram_api(
        "getFile",
        {
            "file_id":
                file_id
        }
    )

    result = response.get(
        "result"
    )

    if not result:

        raise RuntimeError(
            "Telegram getFile returned no result"
        )

    return result


# =========================================================
# DATABASE — CREATE JOB + ASSET
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

        raise RuntimeError(
            "DATABASE_URL is missing"
        )

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

            # ---------------------------------------------
            # CREATE JOB
            # ---------------------------------------------

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

            inserted_job = (
                cur.fetchone()
            )

            # ---------------------------------------------
            # DUPLICATE PROTECTION
            # ---------------------------------------------

            if not inserted_job:

                cur.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE source = 'telegram'
                    AND external_id = %s
                    LIMIT 1
                    """,
                    (
                        external_id,
                    )
                )

                existing_job = (
                    cur.fetchone()
                )

                if not existing_job:

                    raise RuntimeError(
                        "Duplicate detected but "
                        "existing job not found"
                    )

                return (
                    str(existing_job[0]),
                    False
                )

            # ---------------------------------------------
            # CREATE ASSET
            # ---------------------------------------------

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
                    json.dumps(
                        metadata
                    )
                )
            )

        conn.commit()

    return (
        str(job_id),
        True
    )


# =========================================================
# DATABASE — CONTENT JOB VALIDATION
# =========================================================

def get_job_for_content(
    job_id,
    chat_id
):

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL is missing"
        )

    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=5
    ) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    j.id,
                    j.brand,
                    j.format,
                    j.objective,
                    j.requested_by,
                    EXISTS (
                        SELECT 1
                        FROM analyses an
                        WHERE an.job_id = j.id
                    ) AS has_analysis,
                    EXISTS (
                        SELECT 1
                        FROM assets a
                        WHERE a.job_id = j.id
                    ) AS has_asset

                FROM jobs j

                WHERE j.id = %s
                LIMIT 1
                """,
                (
                    job_id,
                )
            )

            row = (
                cur.fetchone()
            )

    if not row:

        return None

    requested_by = str(
        row[4] or ""
    )

    # Security:
    # Only the Telegram chat that created
    # the job may request content generation.
    if requested_by != str(
        chat_id
    ):

        return {
            "authorized":
                False
        }

    return {
        "authorized":
            True,

        "job_id":
            str(row[0]),

        "brand":
            row[1],

        "format":
            row[2],

        "objective":
            row[3],

        "has_analysis":
            bool(row[5]),

        "has_asset":
            bool(row[6])
    }


# =========================================================
# BRAND KEYBOARD
# =========================================================

def brand_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "SCG",
                    "callback_data":
                        "brand:SCG"
                },
                {
                    "text":
                        "ILMI",
                    "callback_data":
                        "brand:ILMI"
                }
            ]
        ]
    }


# =========================================================
# FORMAT KEYBOARD
# =========================================================

def format_keyboard(
    brand
):

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "Feed",
                    "callback_data":
                        f"format:{brand}:FEED"
                },
                {
                    "text":
                        "Carousel",
                    "callback_data":
                        f"format:{brand}:CAROUSEL"
                }
            ],
            [
                {
                    "text":
                        "Story",
                    "callback_data":
                        f"format:{brand}:STORY"
                },
                {
                    "text":
                        "Feed + Story",
                    "callback_data":
                        f"format:{brand}:FEED_STORY"
                }
            ]
        ]
    }


# =========================================================
# OBJECTIVE KEYBOARD
# =========================================================

def objective_keyboard(
    brand,
    content_format
):

    prefix = (
        f"objective:{brand}:"
        f"{content_format}:"
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "🤖 AI Otomatis",
                    "callback_data":
                        prefix + "AUTO"
                }
            ],
            [
                {
                    "text":
                        "📚 Edukasi",
                    "callback_data":
                        prefix + "EDUKASI"
                },
                {
                    "text":
                        "✨ Showcase",
                    "callback_data":
                        prefix + "SHOWCASE"
                }
            ],
            [
                {
                    "text":
                        "🔥 Promo",
                    "callback_data":
                        prefix + "PROMO"
                },
                {
                    "text":
                        "💬 Testimoni",
                    "callback_data":
                        prefix + "TESTIMONI"
                }
            ]
        ]
    }


# =========================================================
# START FLOW
# =========================================================

def start_content_flow(
    chat_id
):

    try:

        delete_session(
            chat_id
        )

    except Exception as exc:

        print(
            "SESSION RESET ERROR:",
            repr(exc)
        )

    send_message(
        chat_id,
        (
            "👋 Selamat datang di "
            "SCG Content Machine.\n\n"
            "Pilih brand yang ingin "
            "dibuatkan konten:"
        ),
        brand_keyboard()
    )


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "system":
            "SCG Content Machine",

        "status":
            "ONLINE",

        "version":
            "1.5.0"
    }


# =========================================================
# HEALTHCHECK
# =========================================================

@app.get("/health")
def health():

    database_status = (
        "disconnected"
    )

    redis_status = (
        "disconnected"
    )

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

                cur.execute(
                    "SELECT 1"
                )

                cur.fetchone()

        database_status = (
            "connected"
        )

    except Exception as exc:

        print(
            "DATABASE HEALTH ERROR:",
            repr(exc)
        )

    try:

        client = get_redis()

        client.ping()

        redis_status = (
            "connected"
        )

    except Exception as exc:

        print(
            "REDIS HEALTH ERROR:",
            repr(exc)
        )

    healthy = (
        database_status
        == "connected"

        and

        redis_status
        == "connected"
    )

    payload = {
        "status":
            (
                "healthy"
                if healthy
                else "unhealthy"
            ),

        "service":
            "content-machine",

        "version":
            "1.5.0",

        "database":
            database_status,

        "redis":
            redis_status
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
    x_telegram_bot_api_secret_token:
        str | None = Header(
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

        callback_id = (
            callback_query.get(
                "id"
            )
        )

        callback_data = (
            callback_query.get(
                "data",
                ""
            )
        )

        callback_message = (
            callback_query.get(
                "message",
                {}
            )
        )

        callback_chat = (
            callback_message.get(
                "chat",
                {}
            )
        )

        callback_chat_id = (
            callback_chat.get(
                "id"
            )
        )

        # ---------------------------------------------
        # ACK CALLBACK
        # ---------------------------------------------

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

            return {
                "ok":
                    True
            }

        # =================================================
        # CONTENT — CREATE
        # =================================================

        if callback_data.startswith(
            "content:create:"
        ):

            parts = (
                callback_data.split(
                    ":",
                    2
                )
            )

            if len(parts) != 3:

                send_message(
                    callback_chat_id,
                    (
                        "❌ Permintaan konten "
                        "tidak valid."
                    )
                )

                return {
                    "ok":
                        True
                }

            job_id = (
                parts[2].strip()
            )

            try:

                job = get_job_for_content(
                    job_id,
                    callback_chat_id
                )

                if not job:

                    send_message(
                        callback_chat_id,
                        (
                            "❌ Job tidak ditemukan.\n\n"
                            "Silakan ketik /start "
                            "untuk membuat konten baru."
                        )
                    )

                    return {
                        "ok":
                            True
                    }

                if not job.get(
                    "authorized"
                ):

                    send_message(
                        callback_chat_id,
                        (
                            "⛔ Job ini tidak dapat "
                            "diproses dari chat ini."
                        )
                    )

                    return {
                        "ok":
                            True
                    }

                if not job.get(
                    "has_asset"
                ):

                    send_message(
                        callback_chat_id,
                        (
                            "❌ Foto untuk job ini "
                            "tidak ditemukan."
                        )
                    )

                    return {
                        "ok":
                            True
                    }

                if not job.get(
                    "has_analysis"
                ):

                    send_message(
                        callback_chat_id,
                        (
                            "⏳ Analisis AI untuk foto "
                            "ini belum tersedia.\n\n"
                            "Tunggu sampai analisis "
                            "selesai terlebih dahulu."
                        )
                    )

                    return {
                        "ok":
                            True
                    }

                # -----------------------------------------
                # IDEMPOTENT CLAIM
                # -----------------------------------------

                claimed = (
                    claim_content_generation(
                        job_id
                    )
                )

                if not claimed:

                    send_message(
                        callback_chat_id,
                        (
                            "⏳ Konten untuk job ini "
                            "sudah masuk antrean atau "
                            "sedang dibuat.\n\n"
                            "Tidak perlu klik ulang."
                        )
                    )

                    return {
                        "ok":
                            True
                    }

                # -----------------------------------------
                # QUEUE
                # -----------------------------------------

                try:

                    enqueue_content_generation(
                        job_id=job_id,
                        chat_id=(
                            callback_chat_id
                        )
                    )

                except Exception:

                    release_content_generation_claim(
                        job_id
                    )

                    raise

                print(
                    "CONTENT GENERATION ENQUEUED:",
                    job_id
                )

                send_message(
                    callback_chat_id,
                    (
                        "🎨 Permintaan diterima.\n\n"
                        f"🆔 Job: {job_id}\n"
                        f"🏷 Brand: {job['brand']}\n"
                        f"📱 Format: {job['format']}\n\n"
                        "Content Brain akan "
                        "menyusun desain."
                    )
                )

            except Exception as exc:

                print(
                    "CONTENT CREATE ERROR:",
                    job_id,
                    repr(exc)
                )

                send_message(
                    callback_chat_id,
                    (
                        "⚠️ Konten belum berhasil "
                        "masuk antrean.\n\n"
                        "Analisis foto tetap aman "
                        "tersimpan."
                    )
                )

            return {
                "ok":
                    True
            }

        # =================================================
        # CONTENT — RESTART
        # =================================================

        elif callback_data == (
            "content:restart"
        ):

            start_content_flow(
                callback_chat_id
            )

            return {
                "ok":
                    True
            }

        # =================================================
        # CONTENT — APPROVE
        # =================================================

        elif callback_data.startswith(
            "content:approve:"
        ):

            send_message(
                callback_chat_id,
                (
                    "✅ Konten di-approve.\n\n"
                    "Status approval sudah diterima "
                    "oleh bot.\n\n"
                    "Pada versi berikutnya status ini "
                    "akan disimpan ke ledger konten "
                    "dan diteruskan ke publishing."
                )
            )

            return {
                "ok":
                    True
            }

        # =================================================
        # CONTENT — REVISE
        # =================================================

        elif callback_data.startswith(
            "content:revise:"
        ):

            send_message(
                callback_chat_id,
                (
                    "✏️ Permintaan revisi diterima.\n\n"
                    "Mesin revisi interaktif belum "
                    "diaktifkan pada milestone V1.5.0."
                )
            )

            return {
                "ok":
                    True
            }

        # =================================================
        # CONTENT — REJECT
        # =================================================

        elif callback_data.startswith(
            "content:reject:"
        ):

            send_message(
                callback_chat_id,
                (
                    "❌ Konten ditolak.\n\n"
                    "Antum bisa ketik /start untuk "
                    "membuat konten baru."
                )
            )

            return {
                "ok":
                    True
            }

        # =================================================
        # BRAND SCG
        # =================================================

        elif callback_data == (
            "brand:SCG"
        ):

            send_message(
                callback_chat_id,
                (
                    "✅ Brand dipilih: SCG\n\n"
                    "Sekarang pilih format konten:"
                ),
                format_keyboard(
                    "SCG"
                )
            )

        # =================================================
        # BRAND ILMI
        # =================================================

        elif callback_data == (
            "brand:ILMI"
        ):

            send_message(
                callback_chat_id,
                (
                    "✅ Brand dipilih: ILMI\n\n"
                    "Sekarang pilih format konten:"
                ),
                format_keyboard(
                    "ILMI"
                )
            )

        # =================================================
        # FORMAT
        # =================================================

        elif callback_data.startswith(
            "format:"
        ):

            parts = (
                callback_data.split(
                    ":"
                )
            )

            if len(parts) == 3:

                brand = parts[1]

                content_format = (
                    parts[2]
                )

                send_message(
                    callback_chat_id,
                    (
                        "✅ Format dipilih: "
                        f"{content_format}\n\n"
                        "Sekarang pilih tujuan konten:"
                    ),
                    objective_keyboard(
                        brand,
                        content_format
                    )
                )

        # =================================================
        # OBJECTIVE
        # =================================================

        elif callback_data.startswith(
            "objective:"
        ):

            parts = (
                callback_data.split(
                    ":"
                )
            )

            if len(parts) == 4:

                brand = parts[1]

                content_format = (
                    parts[2]
                )

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

        return {
            "ok":
                True
        }

    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

    message = update.get(
        "message"
    )

    if not message:

        return {
            "ok":
                True
        }

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    if not chat_id:

        return {
            "ok":
                True
        }

    text = message.get(
        "text",
        ""
    )

    # =====================================================
    # /START
    # =====================================================

    if text == "/start":

        start_content_flow(
            chat_id
        )

        return {
            "ok":
                True
        }

    # =====================================================
    # PHOTO RECEIVED
    # =====================================================

    photos = message.get(
        "photo"
    )

    if photos:

        # ---------------------------------------------
        # LOAD SESSION
        # ---------------------------------------------

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

            return {
                "ok":
                    True
            }

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

            return {
                "ok":
                    True
            }

        if (
            session.get(
                "status"
            )
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

            return {
                "ok":
                    True
            }

        # ---------------------------------------------
        # HIGHEST RESOLUTION PHOTO
        # ---------------------------------------------

        photo = photos[-1]

        telegram_file_id = (
            photo.get(
                "file_id"
            )
        )

        telegram_file_unique_id = (
            photo.get(
                "file_unique_id"
            )
        )

        if not telegram_file_id:

            send_message(
                chat_id,
                (
                    "❌ File foto tidak valid. "
                    "Silakan kirim ulang."
                )
            )

            return {
                "ok":
                    True
            }

        # ---------------------------------------------
        # GET TELEGRAM FILE + DATABASE
        # ---------------------------------------------

        try:

            telegram_file = (
                get_telegram_file(
                    telegram_file_id
                )
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

                    message_id=(
                        message.get(
                            "message_id"
                        )
                    ),

                    brand=(
                        session["brand"]
                    ),

                    content_format=(
                        session["format"]
                    ),

                    objective=(
                        session["objective"]
                    ),

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

            # =========================================
            # NEW JOB
            # =========================================

            if created:

                # -------------------------------------
                # ACKNOWLEDGE INGESTION FIRST
                # -------------------------------------

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

                # -------------------------------------
                # AI QUEUE
                # -------------------------------------

                try:

                    enqueue_analysis(
                        job_id=job_id,

                        chat_id=chat_id,

                        file_path=(
                            telegram_file_path
                        ),

                        brand=(
                            session["brand"]
                        ),

                        content_format=(
                            session["format"]
                        ),

                        objective=(
                            session["objective"]
                        ),

                        mime_type=(
                            "image/jpeg"
                        )
                    )

                    print(
                        "AI ANALYSIS ENQUEUED:",
                        job_id
                    )

                except Exception as queue_exc:

                    # Job + asset are already committed.
                    # Queue failure must not pretend that
                    # the photo was lost.

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

                # Session is complete after ingestion.
                delete_session(
                    chat_id
                )

            # =========================================
            # DUPLICATE
            # =========================================

            else:

                send_message(
                    chat_id,
                    (
                        "ℹ️ Foto ini sudah pernah "
                        "diterima.\n\n"
                        f"🆔 Job: {job_id}"
                    )
                )

            return {
                "ok":
                    True
            }

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

            return {
                "ok":
                    True
            }

    # =====================================================
    # DEFAULT
    # =====================================================

    send_message(
        chat_id,
        (
            "Ketik /start untuk memulai "
            "Content Machine."
        )
    )

    return {
        "ok":
            True
    }
