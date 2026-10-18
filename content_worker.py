import os
import json
import time
import base64
import logging
from io import BytesIO
from typing import Any, Dict, Optional

import psycopg
import redis
import requests
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter


# ============================================================
# SCG CONTENT MACHINE
# V1.5.0 — SMART CONTENT GENERATOR
#
# FLOW:
# content_queue
# -> load job + analysis
# -> download original Telegram image
# -> Astra Content Brain
# -> render brief
# -> local deterministic renderer
# -> Telegram preview
#
# IMPORTANT:
# - Does NOT modify V1.4 Vision Worker.
# - Does NOT generatively alter the customer's curtain.
# - Original photo remains the source of truth.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

OPENAI_CONTENT_MODEL = os.getenv(
    "OPENAI_CONTENT_MODEL",
    os.getenv("OPENAI_VISION_MODEL", "gpt-6-astra")
)

OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_CONTENT_REASONING_EFFORT",
    "high"
)

CONTENT_QUEUE = os.getenv(
    "AI_CONTENT_QUEUE",
    "contentmachine:content_queue"
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30
TELEGRAM_TIMEOUT = 30

WORKER_VERSION = "1.5.0"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "content-machine-content-worker"
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
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None,
    files=None
) -> Dict[str, Any]:

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    if files:
        response = requests.post(
            url,
            data=payload or {},
            files=files,
            timeout=TELEGRAM_TIMEOUT
        )
    else:
        response = requests.post(
            url,
            json=payload or {},
            timeout=TELEGRAM_TIMEOUT
        )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error on {method}: "
            f"{data.get('description')}"
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
        logger.exception(
            "Telegram sendMessage failed chat=%s",
            chat_id
        )


def send_photo(
    chat_id: int,
    image_bytes: bytes,
    caption: str,
    reply_markup=None
) -> None:

    payload = {
        "chat_id": str(chat_id),
        "caption": caption
    }

    if reply_markup:
        payload["reply_markup"] = json.dumps(
            reply_markup
        )

    files = {
        "photo": (
            "content-preview.jpg",
            image_bytes,
            "image/jpeg"
        )
    }

    telegram_api(
        "sendPhoto",
        payload=payload,
        files=files
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

    # Never log this URL.
    # It contains the Telegram bot token.
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
# DATABASE
# ============================================================

def load_job_context(
    job_id: str
) -> Dict[str, Any]:

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
                    a.storage_key,
                    a.mime_type,
                    an.analysis
                FROM jobs j

                JOIN assets a
                    ON a.job_id = j.id

                JOIN analyses an
                    ON an.job_id = j.id

                WHERE j.id = %s

                ORDER BY
                    a.created_at ASC,
                    an.created_at DESC

                LIMIT 1
                """,
                (job_id,)
            )

            row = cur.fetchone()

            if not row:
                raise RuntimeError(
                    "Job context not found. "
                    "Job, asset, or analysis may be missing."
                )

            analysis = row[7]

            if isinstance(analysis, str):
                analysis = json.loads(analysis)

            return {
                "job_id": str(row[0]),
                "brand": row[1],
                "format": row[2],
                "objective": row[3],
                "requested_by": row[4],
                "file_path": row[5],
                "mime_type": row[6],
                "analysis": analysis or {}
            }


# ============================================================
# BRAND SYSTEM
# ============================================================

def get_brand_system(
    brand: str
) -> Dict[str, Any]:

    brand = (brand or "").upper()

    if brand == "SCG":
        return {
            "name": "SERBACANTIK GORDEN",
            "short_name": "SCG",
            "primary": "#B37F6F",
            "gold": "#C1A25A",
            "background": "#F6F0EB",
            "dark": "#332C29",
            "white": "#FFFFFF",
            "character": (
                "premium, elegant, warm, editorial interior, "
                "soft selling, refined, spacious"
            ),
            "cta": (
                "Konsultasikan kebutuhan gorden rumah ta’"
            )
        }

    return {
        "name": "ILMI GORDEN",
        "short_name": "ILMI",
        "primary": "#1F6F5C",
        "gold": "#F4C95D",
        "background": "#F7F9F8",
        "dark": "#1F2D2A",
        "white": "#FFFFFF",
        "character": (
            "modern, clean, trusted, practical, "
            "product-first, marketplace friendly"
        ),
        "cta": "Temukan gorden yang cocok untuk rumahmu"
    }


# ============================================================
# CONTENT BRAIN PROMPT
# ============================================================

def build_content_prompt(
    context: Dict[str, Any]
) -> str:

    brand = context["brand"]
    content_format = context["format"]
    objective = context["objective"]
    analysis = context["analysis"]

    brand_system = get_brand_system(
        brand
    )

    safe_analysis = {
        key: value
        for key, value in analysis.items()
        if key != "_meta"
    }

    return f"""
Kamu adalah Senior Creative Director dan
Social Media Art Director untuk bisnis gorden.

Tugasmu BUKAN menganalisis foto dari awal.

Vision Analyzer sebelumnya sudah menghasilkan
analisis berikut:

{json.dumps(
    safe_analysis,
    ensure_ascii=False,
    indent=2
)}

CONTENT REQUEST:

Brand:
{brand}

Format:
{content_format}

Objective:
{objective}

BRAND SYSTEM:

Nama:
{brand_system["name"]}

Karakter:
{brand_system["character"]}

CTA default:
{brand_system["cta"]}

TUGAS:

Buat creative brief yang akan digunakan oleh
renderer otomatis untuk menghasilkan konten
social media dari FOTO ASLI customer.

PENTING:

1. Jangan mengarang harga.
2. Jangan mengarang diskon.
3. Jangan mengarang ukuran.
4. Jangan mengarang bahan kain.
5. Jangan mengarang persentase blackout.
6. Jangan mengarang lokasi customer.
7. Jangan mengarang testimoni.
8. Jangan mengubah fakta produk.
9. Gunakan selling point yang memang didukung
   oleh analisis visual.
10. Copy harus singkat karena akan berada
    di dalam desain.

FORMAT COPY:

headline:
Maksimal 7 kata.
Harus terasa natural dan premium.

supporting_text:
Maksimal 16 kata.
Bukan paragraf.

cta:
Maksimal 8 kata.

LAYOUT STRATEGY:

Pilih salah satu:
- editorial_bottom
- editorial_top
- split_editorial

CROP STRATEGY:

Pilih salah satu:
- center
- upper_product
- lower_product

Untuk foto dengan ceiling terlalu dominan,
utamakan upper_product atau crop yang membuat
produk menjadi focal point.

TEXT PLACEMENT:

Pilih:
- top
- bottom

OUTPUT JSON VALID SAJA.

Tidak boleh markdown.
Tidak boleh ```json.
Tidak boleh penjelasan tambahan.

Gunakan struktur:

{{
  "headline": "string",
  "supporting_text": "string",
  "cta": "string",
  "angle": "string",
  "layout_strategy": "editorial_bottom",
  "crop_strategy": "center",
  "text_placement": "bottom",
  "design_note": "string"
}}
"""


# ============================================================
# JSON CLEANER
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
        raise RuntimeError(
            "Content Brain returned invalid JSON"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Content Brain output must be object"
        )

    return data


# ============================================================
# USAGE
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
# CONTENT BRAIN
# ============================================================

def generate_content_brief(
    context: Dict[str, Any]
) -> Dict[str, Any]:

    prompt = build_content_prompt(
        context
    )

    logger.info(
        "Calling Content Brain model=%s effort=%s",
        OPENAI_CONTENT_MODEL,
        OPENAI_REASONING_EFFORT
    )

    response = openai_client.responses.create(
        model=OPENAI_CONTENT_MODEL,
        reasoning={
            "effort":
                OPENAI_REASONING_EFFORT
        },
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    }
                ]
            }
        ]
    )

    brief = clean_json_output(
        response.output_text
    )

    return {
        "brief": brief,
        "model": getattr(
            response,
            "model",
            OPENAI_CONTENT_MODEL
        ),
        "response_id": getattr(
            response,
            "id",
            None
        ),
        "usage": extract_usage(
            response
        )
    }


# ============================================================
# FONT
# ============================================================

def font_candidates(
    bold: bool = False
):

    if bold:
        return [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf"
        ]

    return [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf"
    ]


def get_font(
    size: int,
    bold: bool = False
):

    for path in font_candidates(
        bold=bold
    ):
        try:
            return ImageFont.truetype(
                path,
                size=size
            )
        except Exception:
            continue

    return ImageFont.load_default()


# ============================================================
# COLOR
# ============================================================

def hex_to_rgb(
    value: str
):

    value = value.lstrip("#")

    return tuple(
        int(
            value[i:i + 2],
            16
        )
        for i in (0, 2, 4)
    )


# ============================================================
# TEXT WRAP
# ============================================================

def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int
):

    words = str(text or "").split()

    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:

        candidate = (
            current + " " + word
        )

        bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=font
        )

        width = (
            bbox[2] - bbox[0]
        )

        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)

    return lines


# ============================================================
# IMAGE CROP
# ============================================================

def cover_crop(
    image: Image.Image,
    target_width: int,
    target_height: int,
    crop_strategy: str
) -> Image.Image:

    image = image.convert("RGB")

    source_width, source_height = (
        image.size
    )

    scale = max(
        target_width / source_width,
        target_height / source_height
    )

    resized_width = int(
        source_width * scale
    )

    resized_height = int(
        source_height * scale
    )

    image = image.resize(
        (
            resized_width,
            resized_height
        ),
        Image.Resampling.LANCZOS
    )

    left = max(
        0,
        (
            resized_width
            - target_width
        ) // 2
    )

    max_top = max(
        0,
        resized_height - target_height
    )

    strategy = (
        crop_strategy or "center"
    ).lower()

    if strategy == "upper_product":
        top = int(
            max_top * 0.72
        )

    elif strategy == "lower_product":
        top = int(
            max_top * 0.28
        )

    else:
        top = (
            max_top // 2
        )

    right = (
        left + target_width
    )

    bottom = (
        top + target_height
    )

    return image.crop(
        (
            left,
            top,
            right,
            bottom
        )
    )


# ============================================================
# IMAGE POLISH
# ============================================================

def polish_image(
    image: Image.Image
) -> Image.Image:

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.03)

    image = ImageEnhance.Color(
        image
    ).enhance(0.98)

    image = ImageEnhance.Sharpness(
        image
    ).enhance(1.05)

    return image


# ============================================================
# RENDER STORY
# ============================================================

def render_story(
    original_bytes: bytes,
    brief: Dict[str, Any],
    brand: str
) -> bytes:

    WIDTH = 1080
    HEIGHT = 1920

    brand_system = get_brand_system(
        brand
    )

    source = Image.open(
        BytesIO(original_bytes)
    )

    crop_strategy = brief.get(
        "crop_strategy",
        "center"
    )

    image = cover_crop(
        source,
        WIDTH,
        HEIGHT,
        crop_strategy
    )

    image = polish_image(
        image
    )

    canvas = image.convert(
        "RGBA"
    )

    overlay = Image.new(
        "RGBA",
        canvas.size,
        (0, 0, 0, 0)
    )

    overlay_draw = ImageDraw.Draw(
        overlay
    )

    placement = brief.get(
        "text_placement",
        "bottom"
    )

    if placement == "top":

        gradient_start = 0
        gradient_end = 850

        for y in range(
            gradient_start,
            gradient_end
        ):

            progress = (
                1
                - (
                    y
                    / gradient_end
                )
            )

            alpha = int(
                170 * progress
            )

            overlay_draw.line(
                [(0, y), (WIDTH, y)],
                fill=(20, 18, 17, alpha)
            )

    else:

        gradient_start = 930
        gradient_height = (
            HEIGHT - gradient_start
        )

        for y in range(
            gradient_start,
            HEIGHT
        ):

            progress = (
                (
                    y
                    - gradient_start
                )
                / gradient_height
            )

            alpha = int(
                190 * progress
            )

            overlay_draw.line(
                [(0, y), (WIDTH, y)],
                fill=(20, 18, 17, alpha)
            )

    canvas = Image.alpha_composite(
        canvas,
        overlay
    )

    draw = ImageDraw.Draw(
        canvas
    )

    headline_font = get_font(
        78,
        bold=True
    )

    supporting_font = get_font(
        32,
        bold=False
    )

    cta_font = get_font(
        29,
        bold=True
    )

    brand_font = get_font(
        25,
        bold=True
    )

    white = hex_to_rgb(
        brand_system["white"]
    )

    gold = hex_to_rgb(
        brand_system["gold"]
    )

    primary = hex_to_rgb(
        brand_system["primary"]
    )

    margin_x = 82
    text_width = (
        WIDTH - (margin_x * 2)
    )

    if placement == "top":
        current_y = 120
    else:
        current_y = 1190

    # Brand eyebrow
    draw.text(
        (
            margin_x,
            current_y
        ),
        brand_system["short_name"],
        font=brand_font,
        fill=gold
    )

    current_y += 65

    # Accent line
    draw.rounded_rectangle(
        (
            margin_x,
            current_y,
            margin_x + 92,
            current_y + 8
        ),
        radius=4,
        fill=primary
    )

    current_y += 48

    # Headline
    headline = str(
        brief.get(
            "headline",
            ""
        )
    ).strip()

    headline_lines = wrap_text(
        draw,
        headline,
        headline_font,
        text_width
    )

    for line in headline_lines[:3]:

        draw.text(
            (
                margin_x,
                current_y
            ),
            line,
            font=headline_font,
            fill=white
        )

        bbox = draw.textbbox(
            (0, 0),
            line,
            font=headline_font
        )

        current_y += (
            bbox[3]
            - bbox[1]
            + 16
        )

    current_y += 22

    # Supporting
    supporting = str(
        brief.get(
            "supporting_text",
            ""
        )
    ).strip()

    supporting_lines = wrap_text(
        draw,
        supporting,
        supporting_font,
        text_width
    )

    for line in supporting_lines[:3]:

        draw.text(
            (
                margin_x,
                current_y
            ),
            line,
            font=supporting_font,
            fill=white
        )

        bbox = draw.textbbox(
            (0, 0),
            line,
            font=supporting_font
        )

        current_y += (
            bbox[3]
            - bbox[1]
            + 12
        )

    current_y += 42

    # CTA pill
    cta = str(
        brief.get(
            "cta",
            brand_system["cta"]
        )
    ).strip()

    cta_bbox = draw.textbbox(
        (0, 0),
        cta,
        font=cta_font
    )

    cta_width = (
        cta_bbox[2]
        - cta_bbox[0]
        + 56
    )

    cta_height = 72

    cta_width = min(
        cta_width,
        text_width
    )

    draw.rounded_rectangle(
        (
            margin_x,
            current_y,
            margin_x + cta_width,
            current_y + cta_height
        ),
        radius=36,
        fill=primary
    )

    draw.text(
        (
            margin_x + 28,
            current_y + 17
        ),
        cta,
        font=cta_font,
        fill=white
    )

    # Export
    output = BytesIO()

    canvas.convert(
        "RGB"
    ).save(
        output,
        format="JPEG",
        quality=92,
        optimize=True
    )

    return output.getvalue()


# ============================================================
# GENERIC RENDER
# ============================================================

def render_content(
    original_bytes: bytes,
    brief: Dict[str, Any],
    brand: str,
    content_format: str
) -> bytes:

    content_format = (
        content_format or ""
    ).upper()

    # V1.5 first milestone:
    # Story renderer is the canonical renderer.
    #
    # Feed/Carousel will receive dedicated
    # canvases in the next iteration.
    return render_story(
        original_bytes=original_bytes,
        brief=brief,
        brand=brand
    )


# ============================================================
# TASK VALIDATION
# ============================================================

def validate_task(
    task: Dict[str, Any]
) -> Dict[str, Any]:

    if not task.get("job_id"):
        raise ValueError(
            "Content task missing job_id"
        )

    if not task.get("chat_id"):
        raise ValueError(
            "Content task missing chat_id"
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

    logger.info(
        "Processing content job=%s",
        job_id
    )

    context = load_job_context(
        job_id
    )

    send_message(
        chat_id,
        (
            "🎨 Content Brain sedang "
            "menyusun desain..."
        )
    )

    original_bytes = (
        download_telegram_image(
            context["file_path"]
        )
    )

    brain_result = (
        generate_content_brief(
            context
        )
    )

    brief = brain_result[
        "brief"
    ]

    logger.info(
        (
            "Content brief generated "
            "job=%s headline=%s"
        ),
        job_id,
        brief.get(
            "headline"
        )
    )

    rendered_bytes = (
        render_content(
            original_bytes=original_bytes,
            brief=brief,
            brand=context["brand"],
            content_format=context["format"]
        )
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text":
                        "✅ Approve",
                    "callback_data":
                        f"content:approve:{job_id}"
                },
                {
                    "text":
                        "✏️ Revisi",
                    "callback_data":
                        f"content:revise:{job_id}"
                }
            ],
            [
                {
                    "text":
                        "❌ Reject",
                    "callback_data":
                        f"content:reject:{job_id}"
                }
            ]
        ]
    }

    caption = (
        "✨ Preview konten berhasil dibuat.\n\n"
        f"🏷 Brand: {context['brand']}\n"
        f"📱 Format: {context['format']}\n"
        f"🎯 Angle: {brief.get('angle', '-')}\n\n"
        f"📝 Headline:\n"
        f"{brief.get('headline', '-')}\n\n"
        "Pilih tindakan:"
    )

    send_photo(
        chat_id=chat_id,
        image_bytes=rendered_bytes,
        caption=caption,
        reply_markup=keyboard
    )

    usage = brain_result.get(
        "usage",
        {}
    )

    logger.info(
        (
            "Content rendered job=%s "
            "model=%s tokens=%s "
            "bytes=%s"
        ),
        job_id,
        brain_result.get(
            "model"
        ),
        usage.get(
            "total_tokens",
            0
        ),
        len(rendered_bytes)
    )


# ============================================================
# WORKER LOOP
# ============================================================

def run_worker():

    logger.info(
        (
            "SCG Content Machine "
            "Content Worker V%s starting"
        ),
        WORKER_VERSION
    )

    logger.info(
        (
            "Model=%s Reasoning=%s "
            "Queue=%s"
        ),
        OPENAI_CONTENT_MODEL,
        OPENAI_REASONING_EFFORT,
        CONTENT_QUEUE
    )

    redis_client.ping()

    logger.info(
        "Redis connected. Waiting for content jobs..."
    )

    while True:

        try:

            item = redis_client.blpop(
                CONTENT_QUEUE,
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
                        "Queue payload must be object"
                    )

            except Exception:

                logger.exception(
                    "Invalid content queue payload"
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
                    "Content generation failed job=%s",
                    job_id
                )

                try:
                    redis_client.setex(
                        (
                            "contentmachine:"
                            "content_error:"
                            f"{job_id}"
                        ),
                        604800,
                        str(exc)[:2000]
                    )
                except Exception:
                    logger.exception(
                        "Failed recording content error"
                    )

                if chat_id:
                    send_message(
                        int(chat_id),
                        (
                            "⚠️ Konten belum berhasil "
                            "dirender.\n\n"
                            "Analisis foto tetap aman "
                            "tersimpan.\n"
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
                "Unexpected content worker error"
            )

            time.sleep(5)


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    run_worker()
