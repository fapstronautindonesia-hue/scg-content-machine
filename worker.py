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
# V1.4 — AI VISION ANALYZER WORKER
# ============================================================


# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Bisa diganti dari Coolify tanpa mengubah source code.
OPENAI_VISION_MODEL = os.getenv(
    "OPENAI_VISION_MODEL",
    "gpt-6-luna",
)

QUEUE_NAME = os.getenv(
    "AI_ANALYSIS_QUEUE",
    "contentmachine:analysis_queue",
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30
TELEGRAM_TIMEOUT = 20


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("content-machine-worker")


# ------------------------------------------------------------
# ENV VALIDATION
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# CLIENTS
# ------------------------------------------------------------

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

openai_client = OpenAI(
    api_key=OPENAI_API_KEY,
)


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

def telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        json=payload,
        timeout=TELEGRAM_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error on {method}: {data}"
        )

    return data


def send_message(chat_id: int, text: str) -> None:
    try:
        telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )
    except Exception:
        # Telegram gagal tidak boleh membatalkan hasil AI/DB.
        logger.exception(
            "Failed sending Telegram message chat_id=%s",
            chat_id,
        )


# ------------------------------------------------------------
# TELEGRAM IMAGE DOWNLOAD
# ------------------------------------------------------------

def download_telegram_image(file_path: str) -> bytes:
    if not file_path:
        raise ValueError("Telegram file_path is empty")

    # file_path berasal dari Telegram getFile.
    # Jangan log URL ini karena mengandung bot token.
    url = (
        f"https://api.telegram.org/file/"
        f"bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    )

    response = requests.get(
        url,
        timeout=DOWNLOAD_TIMEOUT,
    )

    response.raise_for_status()

    image_bytes = response.content

    if not image_bytes:
        raise ValueError("Downloaded Telegram image is empty")

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image too large: {len(image_bytes)} bytes"
        )

    return image_bytes


# ------------------------------------------------------------
# IMAGE MIME
# ------------------------------------------------------------

def detect_mime_type(
    image_bytes: bytes,
    fallback: str = "image/jpeg",
) -> str:

    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "image/webp"

    return fallback or "image/jpeg"


# ------------------------------------------------------------
# BRAND CONTEXT
# ------------------------------------------------------------

def brand_context(brand: str) -> str:
    brand = (brand or "").upper()

    if brand == "SCG":
        return """
BRAND: SERBACANTIK GORDEN (SCG)

Positioning:
- Custom curtain / gorden dan instalasi.
- Target utama rumah pribadi.
- Area utama Makassar, Gowa, Maros.
- Premium, elegant, warm, editorial interior.
- Soft selling.
- Visual harus terasa rapi dan berkelas.

Produk dapat meliputi:
- blackout curtain
- vitrase / sheer curtain
- custom curtain
- roller blind
- venetian blind
- curtain accessories

Saat menganalisis, prioritaskan:
- kualitas hasil pemasangan
- komposisi gorden dan vitrase
- warna dan kecocokan dengan interior
- kerapian
- daya jual visual
- masalah foto yang mengurangi kualitas konten
"""

    if brand == "ILMI":
        return """
BRAND: ILMI GORDEN

Positioning:
- Toko gorden yang mudah dijangkau.
- Marketplace / social commerce.
- Modern, clean, trusted.
- Product-first.
- Cocok untuk transaksi volume.

Saat menganalisis, prioritaskan:
- produk yang terlihat
- warna
- motif
- fungsi
- ukuran atau penggunaan bila dapat dilihat
- kekuatan visual untuk marketplace/social commerce
- masalah foto yang mengurangi kejelasan produk
"""

    return """
BRAND:
Brand belum dikenali secara spesifik.
Analisis foto secara objektif sebagai aset konten gorden/interior.
"""


# ------------------------------------------------------------
# PROMPT
# ------------------------------------------------------------

def build_analysis_prompt(
    brand: str,
    content_format: str,
    objective: str,
) -> str:

    return f"""
Kamu adalah AI Vision Analyst untuk Content Machine bisnis gorden.

{brand_context(brand)}

CONTENT REQUEST:
- Brand: {brand}
- Format: {content_format}
- Objective: {objective}

TUGAS:

Analisis HANYA berdasarkan apa yang benar-benar terlihat pada foto.

Jangan mengarang:
- jenis kain jika tidak dapat dipastikan
- persentase blackout
- harga
- ukuran
- lokasi
- nama customer
- spesifikasi teknis yang tidak terlihat

Jika sesuatu tidak yakin, gunakan nilai null atau bahasa yang konservatif.

Nilai apakah foto layak digunakan sebagai materi konten.

Berikan analisis dalam JSON VALID SAJA.

Jangan gunakan markdown.
Jangan gunakan ```json.
Jangan beri penjelasan di luar JSON.

Gunakan struktur PERSIS berikut:

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

ATURAN SCORE:

1-3:
Foto buruk / sulit dipakai.

4-5:
Bisa dipakai tetapi membutuhkan perbaikan signifikan.

6-7:
Layak untuk konten.

8-9:
Sangat bagus.

10:
Kualitas visual sangat kuat dan hampir siap publikasi.

RECOMMENDED ANGLE:

Pilih satu angle utama yang paling masuk akal dari foto,
misalnya:
- showcase
- edukasi
- promo
- product_detail
- transformation
- trust
- inspiration

CONTENT IDEA:

Berikan satu ide konten yang konkret dan relevan dengan foto,
brand, format, dan objective yang dipilih user.

Bahasa seluruh isi JSON: Bahasa Indonesia.
"""


# ------------------------------------------------------------
# OPENAI VISION
# ------------------------------------------------------------

def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    brand: str,
    content_format: str,
    objective: str,
) -> Dict[str, Any]:

    encoded = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    data_url = (
        f"data:{mime_type};base64,{encoded}"
    )

    prompt = build_analysis_prompt(
        brand=brand,
        content_format=content_format,
        objective=objective,
    )

    response = openai_client.responses.create(
        model=OPENAI_VISION_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": "high",
                    },
                ],
            }
        ],
    )

    output_text = response.output_text

    if not output_text:
        raise RuntimeError(
            "OpenAI returned empty output_text"
        )

    cleaned = output_text.strip()

    # Defensive cleanup jika model tetap memberi markdown.
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]

    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        analysis = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "OpenAI output is not valid JSON"
        ) from exc

    if not isinstance(analysis, dict):
        raise RuntimeError(
            "OpenAI analysis must be a JSON object"
        )

    return {
        "analysis": analysis,
        "response_id": getattr(
            response,
            "id",
            None,
        ),
        "model": getattr(
            response,
            "model",
            OPENAI_VISION_MODEL,
        ),
        "usage": extract_usage(response),
    }


# ------------------------------------------------------------
# TOKEN USAGE
# ------------------------------------------------------------

def extract_usage(response) -> Dict[str, int]:
    usage = getattr(
        response,
        "usage",
        None,
    )

    if not usage:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    return {
        "input_tokens": int(
            getattr(
                usage,
                "input_tokens",
                0,
            )
            or 0
        ),
        "output_tokens": int(
            getattr(
                usage,
                "output_tokens",
                0,
            )
            or 0
        ),
        "total_tokens": int(
            getattr(
                usage,
                "total_tokens",
                0,
            )
            or 0
        ),
    }


# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

def analysis_exists(job_id: str) -> bool:
    with psycopg.connect(
        DATABASE_URL
    ) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT 1
                FROM analyses
                WHERE job_id = %s
                LIMIT 1
                """,
                (job_id,),
            )

            return cur.fetchone() is not None


def save_analysis(
    job_id: str,
    model: str,
    analysis: Dict[str, Any],
) -> None:

    with psycopg.connect(
        DATABASE_URL
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
                        ensure_ascii=False,
                    ),
                ),
            )

        conn.commit()


# ------------------------------------------------------------
# TELEGRAM RESULT
# ------------------------------------------------------------

def format_analysis_message(
    analysis: Dict[str, Any],
    content_format: str,
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
        False,
    )

    selling_points = analysis.get(
        "selling_points",
        [],
    )

    issues = analysis.get(
        "visual_issues",
        [],
    )

    selling_text = "\n".join(
        f"• {item}"
        for item in selling_points[:3]
    )

    issue_text = "\n".join(
        f"• {item}"
        for item in issues[:3]
    )

    if not selling_text:
        selling_text = "• -"

    if not issue_text:
        issue_text = "• Tidak ada masalah utama terdeteksi"

    usable_text = (
        "LAYAK"
        if usable
        else "PERLU DIPERBAIKI"
    )

    return (
        "🧠 AI selesai menganalisis foto.\n\n"
        f"🪟 Produk: {product}\n"
        f"⭐ Asset Quality: {score}/10\n"
        f"✅ Status aset: {usable_text}\n"
        f"🎯 Angle: {angle}\n"
        f"📱 Format: {content_format}\n\n"
        "💎 Selling point:\n"
        f"{selling_text}\n\n"
        "⚠️ Catatan visual:\n"
        f"{issue_text}\n\n"
        "💡 Ide konten:\n"
        f"{idea}\n\n"
        "Foto sudah dianalisis dan hasilnya "
        "tersimpan di Content Machine."
    )


# ------------------------------------------------------------
# QUEUE PAYLOAD VALIDATION
# ------------------------------------------------------------

def validate_task(
    task: Dict[str, Any]
) -> Dict[str, Any]:

    required = [
        "job_id",
        "chat_id",
        "file_path",
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


# ------------------------------------------------------------
# PROCESS ONE JOB
# ------------------------------------------------------------

def process_task(
    task: Dict[str, Any]
) -> None:

    task = validate_task(task)

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
            "UNKNOWN",
        )
    ).upper()

    content_format = str(
        task.get(
            "format",
            "UNKNOWN",
        )
    ).upper()

    objective = str(
        task.get(
            "objective",
            "AI_AUTO",
        )
    ).upper()

    mime_hint = str(
        task.get(
            "mime_type",
            "image/jpeg",
        )
    )

    logger.info(
        "Processing job=%s brand=%s format=%s objective=%s",
        job_id,
        brand,
        content_format,
        objective,
    )

    # Idempotency guard.
    if analysis_exists(job_id):

        logger.warning(
            "Analysis already exists job=%s. Skipping duplicate.",
            job_id,
        )

        return

    send_message(
        chat_id,
        "🧠 AI sedang menganalisis foto...",
    )

    image_bytes = download_telegram_image(
        file_path
    )

    mime_type = detect_mime_type(
        image_bytes,
        mime_hint,
    )

    result = analyze_image(
        image_bytes=image_bytes,
        mime_type=mime_type,
        brand=brand,
        content_format=content_format,
        objective=objective,
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

    # Simpan metadata AI bersama analysis JSONB.
    # Tidak perlu migrasi tabel baru pada V1.4.
    stored_analysis = dict(
        analysis
    )

    stored_analysis["_meta"] = {
        "response_id": result.get(
            "response_id"
        ),
        "input_tokens": usage.get(
            "input_tokens",
            0,
        ),
        "output_tokens": usage.get(
            "output_tokens",
            0,
        ),
        "total_tokens": usage.get(
            "total_tokens",
            0,
        ),
        "worker_version": "1.4.0",
    }

    save_analysis(
        job_id=job_id,
        model=model,
        analysis=stored_analysis,
    )

    logger.info(
        "Analysis saved job=%s model=%s tokens=%s",
        job_id,
        model,
        usage.get(
            "total_tokens",
            0,
        ),
    )

    send_message(
        chat_id,
        format_analysis_message(
            analysis=analysis,
            content_format=content_format,
        ),
    )


# ------------------------------------------------------------
# WORKER LOOP
# ------------------------------------------------------------

def run_worker() -> None:

    logger.info(
        "SCG Content Machine AI Worker V1.4 starting"
    )

    logger.info(
        "Model=%s Queue=%s",
        OPENAI_VISION_MODEL,
        QUEUE_NAME,
    )

    # Fail fast kalau Redis tidak dapat dijangkau.
    redis_client.ping()

    logger.info(
        "Redis connected. Waiting for jobs..."
    )

    while True:

        try:
            # BLPOP menunggu tanpa polling agresif.
            item = redis_client.blpop(
                QUEUE_NAME,
                timeout=30,
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
                    dict,
                ):
                    raise ValueError(
                        "Queue payload must be JSON object"
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
                    "UNKNOWN",
                )

                chat_id = task.get(
                    "chat_id"
                )

                logger.exception(
                    "Analysis failed job=%s",
                    job_id,
                )

                # Simpan error sederhana di Redis.
                # TTL 7 hari.
                try:
                    redis_client.setex(
                        f"contentmachine:analysis_error:{job_id}",
                        604800,
                        str(exc)[:2000],
                    )
                except Exception:
                    logger.exception(
                        "Failed recording error in Redis"
                    )

                if chat_id:
                    send_message(
                        int(chat_id),
                        "⚠️ Analisis AI belum berhasil.\n\n"
                        "Foto tetap aman tercatat di Content Machine. "
                        "Silakan coba proses ulang setelah sistem diperiksa.",
                    )

        except redis.exceptions.RedisError:

            logger.exception(
                "Redis connection error"
            )

            time.sleep(5)

        except Exception:

            # Worker jangan mati karena satu error tak terduga.
            logger.exception(
                "Unexpected worker loop error"
            )

            time.sleep(5)


# ------------------------------------------------------------
# ENTRYPOINT
# ------------------------------------------------------------

if __name__ == "__main__":
    run_worker()
