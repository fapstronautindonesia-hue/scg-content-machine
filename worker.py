import os
import json
import time
import base64
import logging
from typing import Any, Dict

import psycopg
import redis
import requests
from openai import OpenAI


# ============================================================
# SCG CONTENT MACHINE
# V1.4.2 — AI VISION ANALYZER
# GPT-6 ASTRA / MAX REASONING
#
# CHANGE FROM V1.4.1:
# - Preserve working Vision Analyzer
# - Add Telegram action after successful analysis:
#       [✨ Buat Konten]
#       [🔄 Mulai Baru]
# - Content generation is NOT executed here.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

OPENAI_VISION_MODEL = os.getenv(
    "OPENAI_VISION_MODEL",
    "gpt-6-astra"
)

OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT",
    "max"
)

QUEUE_NAME = os.getenv(
    "AI_ANALYSIS_QUEUE",
    "contentmachine:analysis_queue"
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30
TELEGRAM_TIMEOUT = 20


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "content-machine-ai-worker"
)


# ============================================================
# ENV VALIDATION
# ============================================================

required_env = {
    "DATABASE_URL": DATABASE_URL,
    "REDIS_URL": REDIS_URL,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}

missing_env = [
    name
    for name, value in required_env.items()
    if not value
]

if missing_env:
    raise RuntimeError(
        "Missing required environment variables: "
        + ", ".join(missing_env)
    )


# ============================================================
# CLIENTS
# ============================================================

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=35
)

openai_client = OpenAI(
    api_key=OPENAI_API_KEY
)


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def telegram_api(
    method: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:

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
        timeout=TELEGRAM_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error on {method}"
        )

    return data


def send_message(
    chat_id: int,
    text: str,
    reply_markup=None
) -> None:

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        telegram_api(
            "sendMessage",
            payload
        )

    except Exception:
        # Telegram notification failure must NOT
        # roll back completed AI analysis.
        logger.exception(
            "Telegram send failed chat_id=%s",
            chat_id
        )


# ============================================================
# TELEGRAM IMAGE DOWNLOAD
# ============================================================

def download_telegram_image(
    file_path: str
) -> bytes:

    if not file_path:
        raise ValueError(
            "Telegram file_path is empty"
        )

    # Never log this URL because it contains
    # the Telegram bot token.
    url = (
        "https://api.telegram.org/file/"
        f"bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    )

    response = requests.get(
        url,
        timeout=DOWNLOAD_TIMEOUT
    )

    response.raise_for_status()

    image_bytes = response.content

    if not image_bytes:
        raise ValueError(
            "Downloaded image is empty"
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            "Image exceeds worker size limit"
        )

    return image_bytes


# ============================================================
# MIME DETECTION
# ============================================================

def detect_mime_type(
    image_bytes: bytes,
    fallback: str = "image/jpeg"
) -> str:

    if image_bytes.startswith(
        b"\xff\xd8\xff"
    ):
        return "image/jpeg"

    if image_bytes.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        return "image/png"

    if (
        image_bytes.startswith(b"RIFF")
        and b"WEBP" in image_bytes[:16]
    ):
        return "image/webp"

    return fallback or "image/jpeg"


# ============================================================
# BRAND CONTEXT
# ============================================================

def brand_context(
    brand: str
) -> str:

    brand = (
        brand or ""
    ).upper()

    if brand == "SCG":

        return """
BRAND: SERBACANTIK GORDEN / SCG

BUSINESS:
Custom gorden dan instalasi.

TARGET:
Rumah pribadi, terutama wilayah Makassar,
Gowa, dan Maros.

BRAND CHARACTER:
Premium, elegant, warm, editorial interior,
rapi, terpercaya, dan soft selling.

PRODUCT CONTEXT MAY INCLUDE:
- blackout curtain
- vitrase / sheer
- custom curtain
- roller blind
- venetian blind
- curtain accessories

VISUAL PRIORITY:
- hasil pemasangan
- kerapian jahitan/pemasangan yang terlihat
- perpaduan curtain dan vitrase
- warna dengan interior
- transformasi ruangan
- selling point yang benar-benar terlihat
- kekurangan foto yang mengurangi kualitas konten
"""

    if brand == "ILMI":

        return """
BRAND: ILMI GORDEN

POSITIONING:
Toko gorden yang mudah dijangkau.

CHANNEL CHARACTER:
Marketplace dan social commerce.

BRAND CHARACTER:
Modern, clean, trusted, practical,
product-first.

VISUAL PRIORITY:
- produk
- warna
- motif
- fungsi yang dapat disimpulkan secara aman
- kejelasan produk
- daya jual marketplace
- kekurangan visual
"""

    return """
Analisis sebagai aset visual bisnis
gorden/interior secara objektif.
"""


# ============================================================
# ANALYSIS PROMPT
# ============================================================

def build_analysis_prompt(
    brand: str,
    content_format: str,
    objective: str
) -> str:

    return f"""
Kamu adalah Senior Creative Director,
Visual Merchandiser, dan Content Strategist
untuk bisnis gorden.

{brand_context(brand)}

CONTENT BRIEF:
Brand: {brand}
Format: {content_format}
Objective: {objective}

TUGAS UTAMA:

Analisis foto secara teliti untuk menentukan
bagaimana aset ini sebaiknya digunakan sebagai
materi konten.

Gunakan HANYA informasi yang benar-benar
terlihat atau dapat disimpulkan secara aman
dari gambar.

DILARANG MENGARANG:
- harga
- ukuran
- lokasi customer
- nama customer
- merek kain
- persentase blackout
- bahan kain spesifik
- spesifikasi teknis yang tidak terlihat

Jika tidak yakin, gunakan null.

PERHATIKAN SECARA KHUSUS:

1. Produk utama yang terlihat.
2. Jenis ruangan/scene.
3. Warna dominan.
4. Gaya interior bila cukup jelas.
5. Komposisi foto.
6. Sudut pengambilan gambar.
7. Pencahayaan.
8. Distraksi visual.
9. Proporsi ceiling/wall/floor.
10. Seberapa jelas produk menjadi focal point.
11. Kerapian hasil pemasangan yang terlihat.
12. Potensi selling point.
13. Kesesuaian dengan objective user.
14. Kesesuaian dengan format konten.
15. Apakah foto layak dipublikasikan.

SCORING:

1-3:
Tidak layak digunakan tanpa pengambilan
foto ulang.

4-5:
Bisa digunakan tetapi membutuhkan
perbaikan signifikan.

6-7:
Layak digunakan sebagai konten.

8-9:
Sangat kuat untuk konten.

10:
Kualitas visual sangat kuat dan hampir
siap publikasi tanpa koreksi berarti.

RECOMMENDED ANGLE:

Pilih SATU angle utama paling kuat:

- showcase
- edukasi
- promo
- product_detail
- transformation
- trust
- inspiration

Jika objective user kurang cocok dengan foto,
tetap catat objective user tetapi pilih angle
visual yang menurutmu paling kuat.

CONTENT IDEA:

Buat satu ide konten yang konkret.
Jangan generik.

Contoh buruk:
"Posting hasil pemasangan gorden."

Contoh lebih baik:
"Showcase bagaimana kombinasi gorden utama
berwarna netral dan vitrase putih membuat
jendela terlihat lebih rapi dan berlapis."

OUTPUT:

Kembalikan JSON VALID SAJA.

Tidak boleh menggunakan markdown.
Tidak boleh menggunakan ```json.
Tidak boleh menambahkan kalimat sebelum
atau sesudah JSON.

Gunakan struktur berikut:

{{
  "scene": "string atau null",
  "product": "string atau null",
  "curtain_style": "string atau null",

  "dominant_colors": [
    "string"
  ],

  "room_style": "string atau null",

  "visual_quality_score": 1,

  "visual_strengths": [
    "string"
  ],

  "visual_issues": [
    "string"
  ],

  "selling_points": [
    "string"
  ],

  "recommended_angle": "string",

  "content_idea": "string",

  "usable": true
}}

Seluruh isi JSON harus menggunakan
Bahasa Indonesia.
"""


# ============================================================
# CLEAN MODEL OUTPUT
# ============================================================

def clean_json_output(
    output_text: str
) -> Dict[str, Any]:

    if not output_text:
        raise RuntimeError(
            "OpenAI returned empty output"
        )

    cleaned = output_text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]

    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)

    except json.JSONDecodeError as exc:

        logger.error(
            "Invalid JSON returned by AI"
        )

        raise RuntimeError(
            "OpenAI output is not valid JSON"
        ) from exc

    if not isinstance(
        data,
        dict
    ):
        raise RuntimeError(
            "AI output must be a JSON object"
        )

    return data


# ============================================================
# USAGE EXTRACTION
# ============================================================

def extract_usage(
    response
) -> Dict[str, int]:

    usage = getattr(
        response,
        "usage",
        None
    )

    if not usage:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        }

    return {
        "input_tokens": int(
            getattr(
                usage,
                "input_tokens",
                0
            ) or 0
        ),

        "output_tokens": int(
            getattr(
                usage,
                "output_tokens",
                0
            ) or 0
        ),

        "total_tokens": int(
            getattr(
                usage,
                "total_tokens",
                0
            ) or 0
        )
    }


# ============================================================
# OPENAI VISION
# ============================================================

def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    brand: str,
    content_format: str,
    objective: str
) -> Dict[str, Any]:

    encoded_image = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    data_url = (
        f"data:{mime_type};base64,"
        f"{encoded_image}"
    )

    prompt = build_analysis_prompt(
        brand=brand,
        content_format=content_format,
        objective=objective
    )

    logger.info(
        "Calling OpenAI model=%s effort=%s",
        OPENAI_VISION_MODEL,
        OPENAI_REASONING_EFFORT
    )

    response = (
        openai_client.responses.create(
            model=OPENAI_VISION_MODEL,

            reasoning={
                "effort":
                    OPENAI_REASONING_EFFORT
            },

            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type":
                                "input_text",
                            "text":
                                prompt
                        },
                        {
                            "type":
                                "input_image",
                            "image_url":
                                data_url,
                            "detail":
                                "high"
                        }
                    ]
                }
            ]
        )
    )

    analysis = clean_json_output(
        response.output_text
    )

    usage = extract_usage(
        response
    )

    return {
        "analysis":
            analysis,

        "response_id":
            getattr(
                response,
                "id",
                None
            ),

        "model":
            getattr(
                response,
                "model",
                OPENAI_VISION_MODEL
            ),

        "usage":
            usage
    }


# ============================================================
# DATABASE
# ============================================================

def analysis_exists(
    job_id: str
) -> bool:

    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=5
    ) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT 1
                FROM analyses
                WHERE job_id = %s
                LIMIT 1
                """,
                (job_id,)
            )

            return (
                cur.fetchone()
                is not None
            )


def save_analysis(
    job_id: str,
    model: str,
    analysis: Dict[str, Any]
) -> None:

    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=5
    ) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO analyses (
                    job_id,
                    model,
                    analysis
                )
                VALUES (
                    %s,
                    %s,
                    %s::jsonb
                )
                """,
                (
                    job_id,
                    model,
                    json.dumps(
                        analysis,
                        ensure_ascii=False
                    )
                )
            )

        conn.commit()


# ============================================================
# TELEGRAM RESULT FORMATTER
# ============================================================

def format_analysis_message(
    analysis: Dict[str, Any],
    content_format: str
) -> str:

    score = analysis.get(
        "visual_quality_score",
        "-"
    )

    angle = analysis.get(
        "recommended_angle",
        "-"
    )

    idea = analysis.get(
        "content_idea",
        "-"
    )

    product = analysis.get(
        "product",
        "-"
    )

    usable = analysis.get(
        "usable",
        False
    )

    selling_points = analysis.get(
        "selling_points",
        []
    )

    issues = analysis.get(
        "visual_issues",
        []
    )

    strengths = analysis.get(
        "visual_strengths",
        []
    )

    selling_text = "\n".join(
        f"• {item}"
        for item in selling_points[:3]
    )

    strength_text = "\n".join(
        f"• {item}"
        for item in strengths[:3]
    )

    issue_text = "\n".join(
        f"• {item}"
        for item in issues[:3]
    )

    if not selling_text:
        selling_text = "• -"

    if not strength_text:
        strength_text = "• -"

    if not issue_text:
        issue_text = (
            "• Tidak ada masalah visual "
            "utama terdeteksi"
        )

    usable_text = (
        "LAYAK"
        if usable
        else "PERLU DIPERBAIKI"
    )

    return (
        "🧠 AI selesai menganalisis foto."
        "\n\n"

        f"🪟 Produk: {product}\n"
        f"⭐ Asset Quality: {score}/10\n"
        f"✅ Status aset: {usable_text}\n"
        f"🎯 Angle: {angle}\n"
        f"📱 Format: {content_format}"

        "\n\n"
        "✨ Kekuatan visual:\n"
        f"{strength_text}"

        "\n\n"
        "💎 Selling point:\n"
        f"{selling_text}"

        "\n\n"
        "⚠️ Catatan visual:\n"
        f"{issue_text}"

        "\n\n"
        "💡 Ide konten:\n"
        f"{idea}"

        "\n\n"
        "Hasil analisis sudah tersimpan "
        "di Content Machine."
    )


def content_action_keyboard(
    job_id: str
) -> Dict[str, Any]:

    return {
        "inline_keyboard": [
            [
                {
                    "text": "✨ Buat Konten",
                    "callback_data":
                        f"content:create:{job_id}"
                }
            ],
            [
                {
                    "text": "🔄 Mulai Baru",
                    "callback_data":
                        "content:restart"
                }
            ]
        ]
    }


# ============================================================
# TASK VALIDATION
# ============================================================

def validate_task(
    task: Dict[str, Any]
) -> Dict[str, Any]:

    required = [
        "job_id",
        "chat_id",
        "file_path"
    ]

    missing = [
        key
        for key in required
        if not task.get(key)
    ]

    if missing:
        raise ValueError(
            "Queue task missing fields: "
            + ", ".join(missing)
        )

    return task


# ============================================================
# PROCESS TASK
# ============================================================

def process_task(
    task: Dict[str, Any]
) -> None:

    task = validate_task(
        task
    )

    job_id = str(
        task["job_id"]
    )

    chat_id = int(
        task["chat_id"]
    )

    file_path = str(
        task["file_path"]
    )

    brand = str(
        task.get(
            "brand",
            "UNKNOWN"
        )
    ).upper()

    content_format = str(
        task.get(
            "format",
            "UNKNOWN"
        )
    ).upper()

    objective = str(
        task.get(
            "objective",
            "AUTO"
        )
    ).upper()

    mime_hint = str(
        task.get(
            "mime_type",
            "image/jpeg"
        )
    )

    logger.info(
        (
            "Processing job=%s "
            "brand=%s format=%s "
            "objective=%s"
        ),
        job_id,
        brand,
        content_format,
        objective
    )

    # --------------------------------------------------------
    # IDEMPOTENCY
    # --------------------------------------------------------

    if analysis_exists(
        job_id
    ):

        logger.warning(
            (
                "Analysis already exists "
                "job=%s. Duplicate skipped."
            ),
            job_id
        )

        return

    # --------------------------------------------------------
    # USER STATUS
    # --------------------------------------------------------

    send_message(
        chat_id,
        (
            "🧠 AI sedang menganalisis foto "
            "dengan Astra..."
        )
    )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    image_bytes = (
        download_telegram_image(
            file_path
        )
    )

    mime_type = detect_mime_type(
        image_bytes,
        mime_hint
    )

    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------

    result = analyze_image(
        image_bytes=image_bytes,
        mime_type=mime_type,
        brand=brand,
        content_format=content_format,
        objective=objective
    )

    analysis = result[
        "analysis"
    ]

    model = result[
        "model"
    ]

    usage = result[
        "usage"
    ]

    # --------------------------------------------------------
    # STORE AI METADATA
    # --------------------------------------------------------

    stored_analysis = dict(
        analysis
    )

    stored_analysis["_meta"] = {
        "response_id":
            result.get(
                "response_id"
            ),

        "model":
            model,

        "reasoning_effort":
            OPENAI_REASONING_EFFORT,

        "image_detail":
            "high",

        "input_tokens":
            usage.get(
                "input_tokens",
                0
            ),

        "output_tokens":
            usage.get(
                "output_tokens",
                0
            ),

        "total_tokens":
            usage.get(
                "total_tokens",
                0
            ),

        "worker_version":
            "1.4.2"
    }

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    save_analysis(
        job_id=job_id,
        model=model,
        analysis=stored_analysis
    )

    logger.info(
        (
            "Analysis saved "
            "job=%s model=%s tokens=%s"
        ),
        job_id,
        model,
        usage.get(
            "total_tokens",
            0
        )
    )

    # --------------------------------------------------------
    # RESULT + CONTENT ACTION
    # --------------------------------------------------------

    send_message(
        chat_id,
        format_analysis_message(
            analysis=analysis,
            content_format=content_format
        ),
        reply_markup=content_action_keyboard(
            job_id
        )
    )


# ============================================================
# WORKER LOOP
# ============================================================

def run_worker():

    logger.info(
        (
            "SCG Content Machine "
            "AI Worker V1.4.2 starting"
        )
    )

    logger.info(
        (
            "Model=%s "
            "Reasoning=%s "
            "Queue=%s"
        ),
        OPENAI_VISION_MODEL,
        OPENAI_REASONING_EFFORT,
        QUEUE_NAME
    )

    redis_client.ping()

    logger.info(
        "Redis connected. Waiting for jobs..."
    )

    while True:

        try:

            item = redis_client.blpop(
                QUEUE_NAME,
                timeout=30
            )

            if not item:
                continue

            _, raw_payload = item

            try:

                task = json.loads(
                    raw_payload
                )

                if not isinstance(
                    task,
                    dict
                ):
                    raise ValueError(
                        (
                            "Queue payload must "
                            "be a JSON object"
                        )
                    )

            except Exception:

                logger.exception(
                    "Invalid queue payload"
                )

                continue

            try:

                process_task(
                    task
                )

            except Exception as exc:

                job_id = task.get(
                    "job_id",
                    "UNKNOWN"
                )

                chat_id = task.get(
                    "chat_id"
                )

                logger.exception(
                    "Analysis failed job=%s",
                    job_id
                )

                try:

                    redis_client.setex(
                        (
                            "contentmachine:"
                            "analysis_error:"
                            f"{job_id}"
                        ),
                        604800,
                        str(exc)[:2000]
                    )

                except Exception:

                    logger.exception(
                        (
                            "Failed recording "
                            "analysis error"
                        )
                    )

                if chat_id:

                    send_message(
                        int(chat_id),
                        (
                            "⚠️ Analisis AI belum berhasil."
                            "\n\n"
                            "Foto tetap aman tercatat "
                            "di Content Machine.\n"
                            f"🆔 Job: {job_id}"
                        )
                    )

        except redis.exceptions.RedisError:

            logger.exception(
                "Redis connection error"
            )

            time.sleep(5)

        except Exception:

            logger.exception(
                "Unexpected worker loop error"
            )

            time.sleep(5)


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    run_worker()
