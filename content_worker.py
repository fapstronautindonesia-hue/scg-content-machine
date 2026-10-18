import json
import logging
import time
import redis

from app.config import settings
from app.redis_client import get_redis, release, enqueue, claim
from app.telegram import download_file, send_message, send_photo, extract_sent_photo_file_id
from app.ai.content_brain import generate, social_proof_plan
from app.renderers.story import render_story
from app.renderers.feed import render_feed
from app.renderers.carousel import render_carousel
from app.services import sessions
from app.services.jobs import (
    get_job_context, get_revision, get_latest_content_plan,
    save_content_plan, save_render, save_captions,
    next_render_version, mark_revision_done, set_job_status,
)
from app.services.usage import record_usage
from app.services.events import record_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("content-worker")
settings.validate_worker()


def claim_key(job_id: str) -> str:
    return f"contentmachine:content_claim:{job_id}"


def safe_event(job_id: str, event_type: str, payload: dict | None = None) -> None:
    try:
        record_event(job_id, event_type, payload=payload)
    except Exception:
        logger.exception("Telemetry event failed open job=%s event=%s", job_id, event_type)


def safe_usage(job_id: str, operation: str, model: str, usage: dict, metadata: dict | None = None) -> None:
    try:
        record_usage(job_id, operation, model, usage, metadata)
    except Exception:
        logger.exception("Usage ledger failed open job=%s operation=%s", job_id, operation)


def approval_keyboard(job_id: str):
    return {"inline_keyboard": [
        [
            {"text": "✅ Approve", "callback_data": f"content:approve:{job_id}"},
            {"text": "✏️ Revisi", "callback_data": f"content:revise:{job_id}"},
        ],
        [{"text": "❌ Reject", "callback_data": f"content:reject:{job_id}"}],
    ]}


def send_caption_pack(chat_id: int, captions: dict):
    if not captions:
        return
    labels = {"instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok", "whatsapp": "WhatsApp"}
    for key in ["instagram", "facebook", "tiktok", "whatsapp"]:
        body = captions.get(key)
        if body:
            send_message(chat_id, f"📝 Caption {labels[key]}\n\n{str(body)[:3500]}")


def save_and_send(job_id: str, plan_id: str, version: int, chat_id: int, output_type: str, image_bytes: bytes, caption: str, metadata: dict):
    response = send_photo(chat_id, image_bytes, caption=caption)
    file_id = extract_sent_photo_file_id(response)
    save_render(job_id, plan_id, output_type, version, file_id, metadata)


def _asset_analysis(batch_analysis: dict, index: int) -> dict:
    for item in batch_analysis.get("assets") or []:
        try:
            if int(item.get("index", -1)) == int(index):
                return item
        except Exception:
            continue
    return {"subject_position": {"x": 0.5, "y": 0.5}, "safe_crop": {"top": 0, "right": 1, "bottom": 1, "left": 0}}


def _safe_index(value, count: int, fallback: int = 0) -> int:
    try:
        idx = int(value)
    except Exception:
        idx = fallback
    return max(0, min(idx, max(0, count - 1)))


def process(task: dict):
    job_id = str(task["job_id"])
    chat_id = int(task["chat_id"])
    revision_id = task.get("revision_id")
    context = get_job_context(job_id)
    if context["requested_by"] != str(chat_id):
        raise RuntimeError("Content job owner mismatch")

    revision = get_revision(revision_id)
    if revision_id and not revision:
        raise RuntimeError("Revision request not found")
    if revision and revision["job_id"] != job_id:
        raise RuntimeError("Revision does not belong to content job")

    previous = get_latest_content_plan(job_id)
    if previous:
        context["previous_plan"] = previous.get("plan")

    set_job_status(job_id, "GENERATING")
    originals = [download_file(asset["file_path"]) for asset in context["assets"]]
    if not originals:
        raise RuntimeError("Content job has no downloadable photos")

    fmt = context["format"].upper()
    brand = context["brand"].upper()
    objective = context["objective"].upper()
    batch_analysis = context["analysis"]

    send_message(chat_id, "🎨 Saya sedang menyusun komposisi, memilih foto, dan menyiapkan caption...")

    brain = generate(context, revision=revision)
    plan = brain["plan"]
    model = brain.get("model")
    usage = brain.get("usage") or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Save the plan before render so revision history remains auditable.
    plan_id = save_content_plan(job_id, plan, model, revision_id=revision_id)
    captions = plan.get("captions") or {}
    save_captions(job_id, plan_id, captions)
    if model:
        safe_usage(job_id, "CONTENT_BRAIN", model, usage, {"revision_id": revision_id, "response_id": brain.get("response_id")})

    version = next_render_version(job_id)
    metadata_base = {
        "brand": brand,
        "objective": objective,
        "revision_id": revision_id,
        "plan_id": plan_id,
        "photo_count": len(originals),
        "instruction": context.get("instruction", "")[:500],
    }
    hero_idx = _safe_index(plan.get("hero_asset_index", batch_analysis.get("hero_asset_index", 0)), len(originals))

    # Render all images first. This avoids sending half a carousel when Pillow fails mid-render.
    prepared: list[tuple[str, bytes, str, dict]] = []

    if fmt == "STORY":
        selected = plan.get("selected_asset_indices") or batch_analysis.get("selected_asset_indices") or [hero_idx]
        selected = [_safe_index(x, len(originals), hero_idx) for x in selected][:5]
        selected = list(dict.fromkeys(selected)) or [hero_idx]
        for pos, idx in enumerate(selected, start=1):
            image = render_story(originals[idx], _asset_analysis(batch_analysis, idx), plan, brand)
            prepared.append((
                f"STORY_{pos}", image,
                f"✨ Story {pos}/{len(selected)} • {brand}\nSocial proof • minimalis",
                {**metadata_base, "asset_index": idx, "story_index": pos},
            ))

    elif fmt == "FEED":
        image = render_feed(originals[hero_idx], _asset_analysis(batch_analysis, hero_idx), plan, brand)
        prepared.append((
            "FEED", image,
            f"✨ Feed V{version} • {brand}\n🎯 {plan.get('angle', '-')}",
            {**metadata_base, "asset_index": hero_idx},
        ))

    elif fmt == "CAROUSEL":
        images = render_carousel(originals, batch_analysis, plan, brand)
        if not images:
            raise RuntimeError("Carousel renderer returned no slides")
        total = len(images)
        for slide_no, image in enumerate(images, start=1):
            prepared.append((
                f"CAROUSEL_{slide_no}", image,
                f"✨ Carousel V{version} • Slide {slide_no}/{total}",
                {**metadata_base, "slide": slide_no, "total_slides": total},
            ))

    elif fmt == "FEED_STORY":
        feed = render_feed(originals[hero_idx], _asset_analysis(batch_analysis, hero_idx), plan, brand)
        prepared.append(("FEED", feed, f"✨ Feed V{version} • {brand}", {**metadata_base, "asset_index": hero_idx}))
        story_plan = social_proof_plan(context, revision)
        selected = story_plan.get("selected_asset_indices") or [hero_idx]
        selected = list(dict.fromkeys(_safe_index(x, len(originals), hero_idx) for x in selected))[:3]
        for pos, idx in enumerate(selected, start=1):
            story = render_story(originals[idx], _asset_analysis(batch_analysis, idx), story_plan, brand)
            prepared.append((
                f"STORY_{pos}", story,
                f"✨ Story Social Proof {pos}/{len(selected)} • {brand}",
                {**metadata_base, "asset_index": idx, "story_index": pos},
            ))

    else:
        raise RuntimeError(f"Unsupported format: {fmt}")

    for output_type, image_bytes, preview_caption, metadata in prepared:
        save_and_send(job_id, plan_id, version, chat_id, output_type, image_bytes, preview_caption, metadata)

    send_caption_pack(chat_id, captions)
    set_job_status(job_id, "PREVIEW_READY")
    mark_revision_done(revision_id)
    safe_event(job_id, "PREVIEW_READY", payload={"version": version, "plan_id": plan_id, "format": fmt, "outputs": len(prepared)})
    sessions.save(chat_id, {"status": "PREVIEW_READY", "job_id": job_id})
    send_message(
        chat_id,
        f"✅ Preview V{version} siap.\n\nKalau ada yang mau diubah, langsung ketik saja seperti ngobrol biasa — atau pakai tombol di bawah.",
        approval_keyboard(job_id),
    )
    release(claim_key(job_id))
    logger.info("Content ready job=%s format=%s version=%s photos=%s tokens=%s", job_id, fmt, version, len(originals), usage.get("total_tokens", 0))


def _retry_or_fail(task: dict, exc: Exception) -> None:
    job_id = str(task.get("job_id", "UNKNOWN"))
    chat_id = task.get("chat_id")
    attempt = int(task.get("attempt", 0) or 0) + 1

    # Release the generation claim before requeue/manual recovery.
    if job_id != "UNKNOWN":
        release(claim_key(job_id))

    if attempt < settings.worker_max_attempts:
        retry_task = dict(task)
        retry_task["attempt"] = attempt
        time.sleep(settings.retry_delay_seconds)
        if job_id != "UNKNOWN" and not claim(claim_key(job_id), settings.content_claim_ttl):
            logger.warning("Content retry skipped because another worker owns claim job=%s", job_id)
            return
        try:
            enqueue(settings.content_queue, retry_task)
        except Exception:
            if job_id != "UNKNOWN":
                release(claim_key(job_id))
            raise
        if job_id != "UNKNOWN":
            safe_event(job_id, "CONTENT_RETRY", payload={"attempt": attempt, "error": str(exc)[:1000]})
        logger.warning("Content retry queued job=%s attempt=%s/%s", job_id, attempt + 1, settings.worker_max_attempts)
        return

    if job_id != "UNKNOWN":
        try:
            set_job_status(job_id, "GENERATION_FAILED")
        except Exception:
            logger.exception("Failed setting terminal generation status job=%s", job_id)
        safe_event(job_id, "GENERATION_FAILED", payload={"attempts": attempt, "error": str(exc)[:1000]})
    if chat_id:
        try:
            sessions.save(int(chat_id), {"status": "GENERATION_FAILED", "job_id": job_id})
        except Exception:
            logger.exception("Failed saving generation recovery session job=%s", job_id)
        send_message(int(chat_id), f"⚠️ Render belum berhasil setelah beberapa percobaan. Analisis tetap aman.\n🆔 {job_id}\n\nKetik 'coba lagi' untuk melanjutkan tanpa upload ulang.")


def run():
    r = get_redis()
    r.ping()
    logger.info("Content Worker V2.0.0 | model=%s | queue=%s", settings.content_model, settings.content_queue)
    while True:
        try:
            item = r.blpop(settings.content_queue, timeout=30)
            if not item:
                continue
            _, raw = item
            task = json.loads(raw)
            try:
                process(task)
            except Exception as exc:
                logger.exception("Content generation failed job=%s", task.get("job_id", "UNKNOWN"))
                _retry_or_fail(task, exc)
        except redis.exceptions.RedisError:
            logger.exception("Redis error")
            time.sleep(5)
        except Exception:
            logger.exception("Unexpected content worker error")
            time.sleep(5)


if __name__ == "__main__":
    run()
