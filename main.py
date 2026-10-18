import logging
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.redis_client import enqueue, claim, release, ping_redis
from app.database import ping_database
from app.telegram import send_message, answer_callback, get_file
from app.services import drafts, sessions
from app.services.jobs import get_job, create_revision, approve_job, reject_job, get_analysis_retry_task, set_job_status
from app.services.events import record_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("content-machine-api")
settings.validate_api()

app = FastAPI(title="SCG Content Machine", version="2.0.0")


def safe_event(job_id: str, event_type: str, actor: str = "system", payload: dict | None = None) -> None:
    try:
        record_event(job_id, event_type, actor=actor, payload=payload)
    except Exception:
        logger.exception("Telemetry event failed open job=%s event=%s", job_id, event_type)


def content_claim_key(job_id: str) -> str:
    return f"contentmachine:content_claim:{job_id}"


def enqueue_intake(chat_id: int) -> None:
    enqueue(settings.intake_queue, {"chat_id": chat_id})


def enqueue_content(job_id: str, chat_id: int, revision_id: str | None = None) -> None:
    enqueue(settings.content_queue, {"job_id": job_id, "chat_id": chat_id, "revision_id": revision_id})


def welcome(chat_id: int) -> None:
    drafts.clear(chat_id)
    sessions.clear(chat_id)
    send_message(
        chat_id,
        "👋 SCG Content Assistant siap.\n\n"
        "Kirim foto atau album foto, lalu tulis perintah seperti ngobrol biasa.\n\n"
        "Contoh:\n"
        "• Upload ini sebagai Story IG SCG. Minimalis, logo saja.\n"
        "• Hasil pemasangan Ibu Cindy. Pilih foto terbaik dan buatkan Feed SCG.\n"
        "• Dari 8 foto ini buat Carousel SCG + beberapa Story. Jangan terlalu selling."
    )


@app.get("/")
def root():
    return {"system": "SCG Content Machine", "status": "ONLINE", "version": "2.0.0", "ux": "conversation-first"}


@app.get("/health")
def health():
    db_ok, redis_ok = ping_database(), ping_redis()
    healthy = db_ok and redis_ok
    return JSONResponse({
        "status": "healthy" if healthy else "unhealthy",
        "service": "content-machine",
        "version": "2.0.0",
        "database": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
    }, status_code=200 if healthy else 503)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    callback = update.get("callback_query")
    if callback:
        callback_id = callback.get("id")
        data = callback.get("data", "")
        chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
        if callback_id:
            try:
                answer_callback(callback_id)
            except Exception:
                logger.exception("Failed answering callback")
        if not chat_id:
            return {"ok": True}

        if data.startswith("content:approve:"):
            job_id = data.split(":", 2)[2]
            job = get_job(job_id)
            if not job or job["requested_by"] != str(chat_id):
                send_message(chat_id, "⛔ Approval tidak valid.")
                return {"ok": True}
            approve_job(job_id, str(chat_id))
            safe_event(job_id, "APPROVED", actor=str(chat_id))
            sessions.save(chat_id, {"status": "APPROVED", "job_id": job_id})
            send_message(chat_id, "✅ Approved. Konten ini tercatat sebagai versi final.\n\nKalau mau bikin konten baru, langsung kirim foto lagi.")
            return {"ok": True}

        if data.startswith("content:reject:"):
            job_id = data.split(":", 2)[2]
            job = get_job(job_id)
            if not job or job["requested_by"] != str(chat_id):
                send_message(chat_id, "⛔ Reject tidak valid.")
                return {"ok": True}
            reject_job(job_id, str(chat_id))
            safe_event(job_id, "REJECTED", actor=str(chat_id))
            sessions.save(chat_id, {"status": "REJECTED", "job_id": job_id})
            send_message(chat_id, "❌ Ditolak. Kalau mau versi baru, kirim foto/perintah baru kapan saja.")
            return {"ok": True}

        if data.startswith("content:revise:"):
            job_id = data.split(":", 2)[2]
            job = get_job(job_id)
            if not job or job["requested_by"] != str(chat_id):
                send_message(chat_id, "⛔ Revisi tidak valid.")
                return {"ok": True}
            sessions.save(chat_id, {"status": "WAITING_REVISION", "job_id": job_id})
            send_message(chat_id, "✏️ Tulis saja revisinya seperti ngobrol biasa.\nContoh: cover pakai foto nomor 4, crop jangan terlalu dekat, caption jangan terlalu selling.")
            return {"ok": True}

        return {"ok": True}

    message = update.get("message")
    if not message:
        return {"ok": True}
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    caption = (message.get("caption") or "").strip()

    if text == "/start":
        welcome(chat_id)
        return {"ok": True}

    session = sessions.load(chat_id)

    # Natural recovery after a terminal worker failure.
    if text and session and text.lower().strip() in {"coba lagi", "retry", "ulang", "lanjut", "lanjutkan"}:
        failed_job_id = session.get("job_id")
        if session.get("status") == "INTAKE_FAILED":
            enqueue_intake(chat_id)
            sessions.save(chat_id, {"status": "PROCESSING_INTAKE"})
            send_message(chat_id, "♻️ Siap bro. Saya lanjutkan dari foto dan perintah yang sudah saya tampung.")
            return {"ok": True}
        if failed_job_id and session.get("status") == "ANALYSIS_FAILED":
            job = get_job(failed_job_id)
            if job and job["requested_by"] == str(chat_id):
                task = get_analysis_retry_task(failed_job_id, chat_id)
                enqueue(settings.analysis_queue, task)
                set_job_status(failed_job_id, "QUEUED_ANALYSIS")
                sessions.save(chat_id, {"status": "PROCESSING", "job_id": failed_job_id})
                send_message(chat_id, "♻️ Siap bro. Saya lanjutkan analisis dari foto yang sudah tersimpan — tidak perlu upload ulang.")
                return {"ok": True}
        if failed_job_id and session.get("status") == "GENERATION_FAILED":
            job = get_job(failed_job_id)
            if job and job["requested_by"] == str(chat_id):
                if claim(content_claim_key(failed_job_id), settings.content_claim_ttl):
                    try:
                        enqueue_content(failed_job_id, chat_id)
                        sessions.save(chat_id, {"status": "PROCESSING", "job_id": failed_job_id})
                        send_message(chat_id, "♻️ Siap bro. Saya lanjutkan render dari analisis yang sudah tersimpan.")
                    except Exception:
                        release(content_claim_key(failed_job_id))
                        raise
                else:
                    send_message(chat_id, "⏳ Job ini sudah sedang diproses. Tunggu preview-nya ya bro.")
                return {"ok": True}

    # Natural approval/reject/revision after a preview.
    if text and session and session.get("status") in {"WAITING_REVISION", "PREVIEW_READY"}:
        current_draft = drafts.load(chat_id)
        if not current_draft.get("photos"):
            job_id = session.get("job_id")
            job = get_job(job_id) if job_id else None
            lower = text.lower().strip()
            if job and job["requested_by"] == str(chat_id) and session.get("status") == "PREVIEW_READY":
                if lower in {"approve", "approved", "acc", "oke acc", "ok acc", "setuju", "bagus approve"}:
                    approve_job(job_id, str(chat_id))
                    safe_event(job_id, "APPROVED", actor=str(chat_id), payload={"via": "natural_text"})
                    sessions.save(chat_id, {"status": "APPROVED", "job_id": job_id})
                    send_message(chat_id, "✅ Approved. Konten ini tercatat sebagai versi final.")
                    return {"ok": True}
                if lower in {"reject", "tolak", "ditolak", "nggak jadi", "ga jadi", "tidak jadi"}:
                    reject_job(job_id, str(chat_id))
                    safe_event(job_id, "REJECTED", actor=str(chat_id), payload={"via": "natural_text"})
                    sessions.save(chat_id, {"status": "REJECTED", "job_id": job_id})
                    send_message(chat_id, "❌ Ditolak. Kalau mau versi baru, langsung kirim foto/perintah baru.")
                    return {"ok": True}
            if job and job["requested_by"] == str(chat_id):
                if not claim(content_claim_key(job_id), settings.content_claim_ttl):
                    send_message(chat_id, "⏳ Versi sebelumnya masih diproses. Tunggu preview selesai dulu ya bro.")
                    return {"ok": True}
                try:
                    revision_id = create_revision(job_id, text, str(chat_id))
                    enqueue_content(job_id, chat_id, revision_id=revision_id)
                    safe_event(job_id, "REVISION_REQUESTED", actor=str(chat_id), payload={"revision_id": revision_id, "instruction": text})
                    sessions.save(chat_id, {"status": "PROCESSING_REVISION", "job_id": job_id})
                    send_message(chat_id, "✏️ Siap. Saya revisi dari versi terakhir, bukan mulai dari nol.")
                except Exception:
                    release(content_claim_key(job_id))
                    raise
                return {"ok": True}

    # Photo / album intake.
    photos = message.get("photo")
    if photos:
        photo = photos[-1]
        file_id = photo.get("file_id")
        if not file_id:
            send_message(chat_id, "❌ Foto Telegram tidak valid.")
            return {"ok": True}
        tg_file = get_file(file_id)
        file_path = tg_file.get("file_path")
        if not file_path:
            send_message(chat_id, "❌ Telegram tidak memberikan file path.")
            return {"ok": True}
        draft = drafts.add_photo(chat_id, {
            "file_id": file_id,
            "file_unique_id": photo.get("file_unique_id"),
            "file_path": file_path,
            "message_id": message.get("message_id"),
            "media_group_id": message.get("media_group_id"),
        }, caption=caption or None)

        # If caption already contains the instruction, the worker waits briefly so the whole album can arrive.
        if draft.get("instruction"):
            enqueue_intake(chat_id)
        else:
            ack_key = f"contentmachine:photo_ack:{chat_id}"
            if claim(ack_key, 5):
                send_message(chat_id, "📸 Foto saya tampung dulu. Kirim semua fotonya, lalu tulis perintahnya dalam satu chat. Maksimal 12 foto per batch.")
        return {"ok": True}

    if text:
        # A clarification answer extends the existing instruction instead of replacing it.
        if session and session.get("status") == "WAITING_CLARIFICATION":
            draft = drafts.append_instruction(chat_id, text)
            sessions.clear(chat_id)
        else:
            draft = drafts.set_instruction(chat_id, text)

        if not draft.get("photos"):
            send_message(chat_id, "Siap. Sekarang kirim foto atau album fotonya bro — perintah ini saya tahan sementara.")
            return {"ok": True}

        enqueue_intake(chat_id)
        return {"ok": True}

    return {"ok": True}
