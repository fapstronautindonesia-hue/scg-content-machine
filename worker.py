import json
import logging
import time
import redis

from app.config import settings
from app.database import db_connection
from app.redis_client import get_redis, enqueue, claim, release
from app.telegram import download_file, send_message
from app.ai.intent import parse as parse_intent
from app.ai.vision import analyze_batch
from app.services import drafts, sessions
from app.services.jobs import create_job_with_assets, has_analysis, set_job_status, get_job
from app.services.usage import record_usage
from app.services.events import record_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("vision-worker")
settings.validate_worker()


def safe_event(job_id: str, event_type: str, actor: str = "system", payload: dict | None = None) -> None:
    try:
        record_event(job_id, event_type, actor=actor, payload=payload)
    except Exception:
        logger.exception("Telemetry event failed open job=%s event=%s", job_id, event_type)


def safe_usage(job_id: str, operation: str, model: str, usage: dict, metadata: dict | None = None) -> None:
    try:
        record_usage(job_id, operation, model, usage, metadata)
    except Exception:
        logger.exception("Usage ledger failed open job=%s operation=%s", job_id, operation)


def save_analysis(job_id: str, model: str, analysis: dict, meta: dict) -> None:
    stored = dict(analysis)
    stored["_meta"] = meta
    with db_connection() as conn:
        with conn.cursor() as cur:
            # A retry after a DB commit must not create duplicate analyses.
            cur.execute("SELECT 1 FROM analyses WHERE job_id=%s LIMIT 1", (job_id,))
            if cur.fetchone():
                return
            cur.execute(
                "INSERT INTO analyses (job_id, model, analysis) VALUES (%s, %s, %s::jsonb)",
                (job_id, model, json.dumps(stored, ensure_ascii=False)),
            )
        conn.commit()


def _wait_for_draft_to_settle(chat_id: int) -> dict:
    deadline = time.time() + 12
    while True:
        draft = drafts.load(chat_id)
        age = time.time() - float(draft.get("last_update_at") or 0)
        if age >= settings.draft_settle_seconds or time.time() >= deadline:
            return draft
        time.sleep(min(0.6, max(0.15, settings.draft_settle_seconds - age)))


def _content_claim_key(job_id: str) -> str:
    return f"contentmachine:content_claim:{job_id}"


def _enqueue_content_if_needed(job_id: str, chat_id: int) -> bool:
    job = get_job(job_id)
    if not job:
        raise RuntimeError("Job disappeared before content generation")
    if job["status"] in {"PREVIEW_READY", "APPROVED", "REJECTED"}:
        return False
    key = _content_claim_key(job_id)
    if not claim(key, settings.content_claim_ttl):
        return False
    try:
        set_job_status(job_id, "GENERATING")
        enqueue(settings.content_queue, {"job_id": job_id, "chat_id": chat_id, "revision_id": None, "attempt": 0})
        return True
    except Exception:
        release(key)
        raise


def process_intake(task: dict) -> None:
    chat_id = int(task["chat_id"])
    lock = f"contentmachine:intake_claim:{chat_id}"
    if not claim(lock, settings.intake_claim_ttl):
        return
    try:
        draft = _wait_for_draft_to_settle(chat_id)
        photos = draft.get("photos") or []
        instruction = (draft.get("instruction") or "").strip()
        if not photos or not instruction:
            return

        parsed = parse_intent(instruction, len(photos), drafts.preferences(chat_id))
        intent = parsed["intent"]
        if intent.get("needs_clarification") or not intent.get("brand"):
            sessions.save(chat_id, {"status": "WAITING_CLARIFICATION"})
            question = intent.get("clarification_question") or "Ini untuk SCG atau ILMI, bro?"
            send_message(chat_id, f"Saya sudah pegang {len(photos)} foto. 👍\n\n{question}")
            return

        job_id, created = create_job_with_assets(chat_id, photos, intent, instruction)

        if not created:
            # Recovery path: the DB transaction may have committed before a queue/network failure.
            job = get_job(job_id)
            if not job:
                raise RuntimeError("Duplicate job exists but cannot be loaded")
            if has_analysis(job_id):
                queued = _enqueue_content_if_needed(job_id, chat_id)
                drafts.clear(chat_id)
                if queued:
                    send_message(chat_id, "♻️ Job lama ditemukan. Analisisnya sudah aman; saya lanjutkan render yang sempat terputus.")
                else:
                    send_message(chat_id, "ℹ️ Batch foto ini sudah pernah diproses.")
                return

            set_job_status(job_id, "QUEUED_ANALYSIS")
            enqueue(settings.analysis_queue, {
                "job_id": job_id,
                "chat_id": chat_id,
                "brand": intent["brand"],
                "format": intent["format"],
                "objective": intent.get("objective", "AUTO"),
                "instruction": instruction,
                "photos": [{**p, "batch_index": i} for i, p in enumerate(photos)],
                "attempt": 0,
            })
            drafts.clear(chat_id)
            sessions.save(chat_id, {"status": "PROCESSING", "job_id": job_id})
            send_message(chat_id, "♻️ Job lama ditemukan. Saya lanjutkan analisis yang sempat terputus.")
            return

        if parsed.get("model") != "deterministic":
            safe_usage(
                job_id,
                "INTENT_ROUTER",
                parsed.get("model") or settings.intent_model,
                parsed.get("usage") or {},
                {"response_id": parsed.get("response_id")},
            )

        # Queue core work BEFORE clearing the conversational draft.
        enqueue(settings.analysis_queue, {
            "job_id": job_id,
            "chat_id": chat_id,
            "brand": intent["brand"],
            "format": intent["format"],
            "objective": intent.get("objective", "AUTO"),
            "instruction": instruction,
            "photos": [{**p, "batch_index": i} for i, p in enumerate(photos)],
            "attempt": 0,
        })

        try:
            drafts.save_preferences(chat_id, brand=intent.get("brand"), channel=intent.get("channel"))
        except Exception:
            logger.exception("Preference save failed open chat=%s", chat_id)
        drafts.clear(chat_id)
        sessions.save(chat_id, {"status": "PROCESSING", "job_id": job_id})
        safe_event(job_id, "INTENT_RESOLVED", actor=str(chat_id), payload={"intent": intent, "photo_count": len(photos)})

        fmt_label = "Carousel Feed" if intent["format"] == "CAROUSEL" else intent["format"].replace("_", " + ").title()
        send_message(
            chat_id,
            f"Siap bro. Saya menemukan {len(photos)} foto.\n\n"
            f"🏷 {intent['brand']}\n📱 {intent.get('channel', 'INSTAGRAM')} • {fmt_label}\n"
            f"🎯 {intent.get('objective', 'AUTO')}\n\n"
            "Saya analisis semua foto, pilih yang paling kuat, lalu langsung buat kontennya."
        )
    finally:
        release(lock)


def process_analysis(task: dict) -> None:
    job_id = str(task["job_id"])
    chat_id = int(task["chat_id"])

    if has_analysis(job_id):
        logger.warning("Existing analysis recovered job=%s", job_id)
        _enqueue_content_if_needed(job_id, chat_id)
        return

    set_job_status(job_id, "ANALYZING")
    photo_tasks = task.get("photos") or []
    if not photo_tasks:
        raise RuntimeError("Analysis task contains no photos")

    images = []
    for idx, photo in enumerate(photo_tasks):
        images.append({
            "bytes": download_file(photo["file_path"]),
            "mime_type": "image/jpeg",
            "index": photo.get("batch_index", idx),
        })

    result = analyze_batch(
        images,
        task.get("brand", ""),
        task.get("format", ""),
        task.get("objective", "AUTO"),
        task.get("instruction", ""),
    )
    meta = {
        "response_id": result.get("response_id"),
        "model": result["model"],
        "reasoning_effort": settings.vision_reasoning,
        "image_detail": "high",
        "photo_count": len(images),
        **result["usage"],
        "worker_version": "2.0.0",
    }
    save_analysis(job_id, result["model"], result["analysis"], meta)
    set_job_status(job_id, "ANALYZED")

    # Telemetry must never block the transaction pipeline.
    safe_usage(
        job_id,
        "VISION_BATCH_ANALYSIS",
        result["model"],
        result["usage"],
        {"response_id": result.get("response_id"), "photo_count": len(images)},
    )
    safe_event(
        job_id,
        "ANALYSIS_COMPLETED",
        payload={"photo_count": len(images), "hero_asset_index": result["analysis"].get("hero_asset_index")},
    )

    selected = result["analysis"].get("selected_asset_indices") or []
    send_message(chat_id, f"🧠 Analisis selesai. Saya pilih {max(1, len(selected))} foto paling kuat. Sekarang saya susun preview...")
    _enqueue_content_if_needed(job_id, chat_id)
    logger.info("Batch analysis complete job=%s photos=%s tokens=%s", job_id, len(images), result["usage"].get("total_tokens", 0))


def _retry_or_fail(queue_name: str, task: dict, exc: Exception) -> None:
    attempt = int(task.get("attempt", 0) or 0) + 1
    job_id = str(task.get("job_id", "UNKNOWN"))
    chat_id = task.get("chat_id")

    if attempt < settings.worker_max_attempts:
        retry_task = dict(task)
        retry_task["attempt"] = attempt
        logger.warning("Retrying queue=%s job=%s attempt=%s/%s", queue_name, job_id, attempt + 1, settings.worker_max_attempts)
        time.sleep(settings.retry_delay_seconds)
        enqueue(queue_name, retry_task)
        if job_id != "UNKNOWN":
            safe_event(job_id, "WORKER_RETRY", payload={"queue": queue_name, "attempt": attempt, "error": str(exc)[:1000]})
        return

    if job_id != "UNKNOWN":
        try:
            set_job_status(job_id, "ANALYSIS_FAILED")
        except Exception:
            logger.exception("Failed setting terminal analysis status job=%s", job_id)
        safe_event(job_id, "ANALYSIS_FAILED", payload={"queue": queue_name, "attempts": attempt, "error": str(exc)[:1000]})
    if chat_id:
        try:
            if job_id != "UNKNOWN":
                sessions.save(int(chat_id), {"status": "ANALYSIS_FAILED", "job_id": job_id})
            else:
                sessions.save(int(chat_id), {"status": "INTAKE_FAILED"})
        except Exception:
            logger.exception("Failed saving recovery session job=%s", job_id)
        send_message(
            int(chat_id),
            "⚠️ Proses AI belum berhasil setelah beberapa percobaan. Foto/perintah tetap aman. Kirim 'coba lagi' untuk melanjutkan tanpa upload ulang.",
        )


def run():
    r = get_redis()
    r.ping()
    logger.info(
        "Vision/Intake Worker V2.0.0 | intent=%s | vision=%s | queues=%s,%s",
        settings.intent_model, settings.vision_model, settings.intake_queue, settings.analysis_queue,
    )
    while True:
        try:
            item = r.blpop([settings.intake_queue, settings.analysis_queue], timeout=30)
            if not item:
                continue
            queue_name, raw = item
            task = json.loads(raw)
            try:
                if queue_name == settings.intake_queue:
                    process_intake(task)
                else:
                    process_analysis(task)
            except Exception as exc:
                logger.exception("Worker task failed queue=%s job=%s", queue_name, task.get("job_id", "UNKNOWN"))
                # Intake keeps its Redis draft; analysis keeps DB job/assets.
                _retry_or_fail(queue_name, task, exc)
        except redis.exceptions.RedisError:
            logger.exception("Redis error")
            time.sleep(5)
        except Exception:
            logger.exception("Unexpected worker error")
            time.sleep(5)


if __name__ == "__main__":
    run()
