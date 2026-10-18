import json
import uuid
from typing import Optional
from app.database import db_connection


def create_job_with_assets(chat_id: int, photos: list[dict], intent: dict, instruction: str) -> tuple[str, bool]:
    if not photos:
        raise ValueError("At least one photo is required")
    job_id = uuid.uuid4()
    message_ids = sorted(int(p.get("message_id") or 0) for p in photos)
    external_id = f"telegram:{chat_id}:batch:{message_ids[0]}-{message_ids[-1]}"
    brand = (intent.get("brand") or "SCG").upper()
    fmt = (intent.get("format") or "FEED").upper()
    objective = (intent.get("objective") or "AUTO").upper()

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (id, source, external_id, brand, format, objective, status, requested_by)
                VALUES (%s, 'telegram', %s, %s, %s, %s, 'QUEUED_ANALYSIS', %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (job_id, external_id, brand, fmt, objective, str(chat_id)),
            )
            inserted = cur.fetchone()
            if not inserted:
                cur.execute("SELECT id FROM jobs WHERE source='telegram' AND external_id=%s LIMIT 1", (external_id,))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Duplicate batch detected but existing job not found")
                return str(row[0]), False

            for idx, photo in enumerate(photos):
                asset_id = uuid.uuid4()
                file_path = photo.get("file_path")
                metadata = {
                    "telegram_file_id": photo.get("file_id"),
                    "telegram_file_unique_id": photo.get("file_unique_id"),
                    "telegram_file_path": file_path,
                    "telegram_chat_id": chat_id,
                    "telegram_message_id": photo.get("message_id"),
                    "telegram_media_group_id": photo.get("media_group_id"),
                    "batch_index": idx,
                }
                filename = file_path.split("/")[-1] if file_path else None
                cur.execute(
                    """
                    INSERT INTO assets
                        (id, job_id, asset_type, storage_provider, storage_key, original_filename, mime_type, metadata)
                    VALUES (%s, %s, 'IMAGE', 'telegram', %s, %s, 'image/jpeg', %s::jsonb)
                    """,
                    (asset_id, job_id, file_path, filename, json.dumps(metadata, ensure_ascii=False)),
                )

            cur.execute(
                """
                INSERT INTO job_requests (job_id, channel, instruction, intent)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (job_id) DO UPDATE
                SET channel=EXCLUDED.channel, instruction=EXCLUDED.instruction, intent=EXCLUDED.intent
                """,
                (job_id, intent.get("channel") or "INSTAGRAM", instruction, json.dumps(intent, ensure_ascii=False)),
            )
        conn.commit()
    return str(job_id), True


def get_job(job_id: str) -> Optional[dict]:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, brand, format, objective, status, requested_by FROM jobs WHERE id=%s LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "job_id": str(row[0]), "brand": row[1], "format": row[2],
        "objective": row[3], "status": row[4], "requested_by": str(row[5] or ""),
    }


def get_job_context(job_id: str) -> dict:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.id, j.brand, j.format, j.objective, j.status, j.requested_by,
                       an.analysis, jr.channel, jr.instruction, jr.intent
                FROM jobs j
                JOIN analyses an ON an.job_id=j.id
                LEFT JOIN job_requests jr ON jr.job_id=j.id
                WHERE j.id=%s
                ORDER BY an.created_at DESC
                LIMIT 1
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Job context is incomplete: job or analysis missing")
            cur.execute(
                """
                SELECT id, storage_key, mime_type, metadata
                FROM assets WHERE job_id=%s
                ORDER BY COALESCE((metadata->>'batch_index')::int, 0), created_at ASC
                """,
                (job_id,),
            )
            asset_rows = cur.fetchall()

    analysis = row[6]
    intent = row[9] or {}
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    if isinstance(intent, str):
        intent = json.loads(intent)
    assets = []
    for asset in asset_rows:
        metadata = asset[3] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        assets.append({
            "asset_id": str(asset[0]), "file_path": asset[1], "mime_type": asset[2], "metadata": metadata,
        })
    if not assets:
        raise RuntimeError("Job has no assets")
    return {
        "job_id": str(row[0]), "brand": row[1], "format": row[2], "objective": row[3],
        "status": row[4], "requested_by": str(row[5] or ""), "analysis": analysis or {},
        "channel": row[7] or "INSTAGRAM", "instruction": row[8] or "", "intent": intent,
        "assets": assets,
    }


def has_analysis(job_id: str) -> bool:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM analyses WHERE job_id=%s LIMIT 1", (job_id,))
            return cur.fetchone() is not None


def set_job_status(job_id: str, status: str) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status=%s WHERE id=%s", (status, job_id))
        conn.commit()


def create_revision(job_id: str, instruction: str, requested_by: str) -> str:
    revision_id = uuid.uuid4()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO revisions (id, job_id, instruction, requested_by, status) VALUES (%s, %s, %s, %s, 'PENDING')",
                (revision_id, job_id, instruction, requested_by),
            )
            cur.execute("UPDATE jobs SET status='REVISION' WHERE id=%s", (job_id,))
        conn.commit()
    return str(revision_id)


def get_revision(revision_id: str | None) -> Optional[dict]:
    if not revision_id:
        return None
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, job_id, instruction, status FROM revisions WHERE id=%s LIMIT 1", (revision_id,))
            row = cur.fetchone()
    if not row:
        return None
    return {"revision_id": str(row[0]), "job_id": str(row[1]), "instruction": row[2], "status": row[3]}


def mark_revision_done(revision_id: str | None) -> None:
    if not revision_id:
        return
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE revisions SET status='APPLIED', applied_at=now() WHERE id=%s", (revision_id,))
        conn.commit()


def save_content_plan(job_id: str, plan: dict, model: str | None, revision_id: str | None = None) -> str:
    plan_id = uuid.uuid4()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO content_plans (id, job_id, revision_id, model, plan, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, 'ACTIVE')
                """,
                (plan_id, job_id, revision_id, model, json.dumps(plan, ensure_ascii=False)),
            )
        conn.commit()
    return str(plan_id)


def save_render(job_id: str, plan_id: str, output_type: str, version: int, telegram_file_id: str | None, metadata: dict) -> str:
    render_id = uuid.uuid4()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO renders
                    (id, job_id, content_plan_id, output_type, version, storage_provider, storage_key, status, metadata)
                VALUES (%s, %s, %s, %s, %s, 'telegram', %s, 'PREVIEW_READY', %s::jsonb)
                """,
                (render_id, job_id, plan_id, output_type, version, telegram_file_id, json.dumps(metadata, ensure_ascii=False)),
            )
        conn.commit()
    return str(render_id)


def next_render_version(job_id: str) -> int:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(version),0)+1 FROM renders WHERE job_id=%s", (job_id,))
            return int(cur.fetchone()[0])


def save_captions(job_id: str, plan_id: str, captions: dict) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            for channel, body in captions.items():
                if not body:
                    continue
                cur.execute(
                    "INSERT INTO captions (job_id, content_plan_id, channel, body, status) VALUES (%s, %s, %s, %s, 'DRAFT')",
                    (job_id, plan_id, channel.upper(), str(body)),
                )
        conn.commit()


def approve_job(job_id: str, actor: str) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM content_plans WHERE job_id=%s ORDER BY created_at DESC LIMIT 1", (job_id,))
            row = cur.fetchone()
            latest_plan_id = row[0] if row else None
            cur.execute("UPDATE renders SET status='APPROVED' WHERE job_id=%s AND version=(SELECT MAX(version) FROM renders WHERE job_id=%s)", (job_id, job_id))
            if latest_plan_id:
                cur.execute("UPDATE captions SET status='APPROVED' WHERE content_plan_id=%s", (latest_plan_id,))
            cur.execute("UPDATE jobs SET status='APPROVED' WHERE id=%s", (job_id,))
        conn.commit()


def reject_job(job_id: str, actor: str) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE renders SET status='REJECTED' WHERE job_id=%s AND version=(SELECT MAX(version) FROM renders WHERE job_id=%s)", (job_id, job_id))
            cur.execute("UPDATE jobs SET status='REJECTED' WHERE id=%s", (job_id,))
        conn.commit()


def get_analysis_retry_task(job_id: str, chat_id: int) -> dict:
    """Rebuild an analysis queue payload from committed DB rows after a terminal worker failure."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.brand, j.format, j.objective, j.requested_by, jr.instruction
                FROM jobs j
                LEFT JOIN job_requests jr ON jr.job_id=j.id
                WHERE j.id=%s
                LIMIT 1
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Retry job not found")
            if str(row[3] or "") != str(chat_id):
                raise RuntimeError("Retry job owner mismatch")
            cur.execute(
                """
                SELECT storage_key, mime_type, metadata
                FROM assets
                WHERE job_id=%s
                ORDER BY COALESCE((metadata->>'batch_index')::int, 0), created_at ASC
                """,
                (job_id,),
            )
            assets = cur.fetchall()
    if not assets:
        raise RuntimeError("Retry job has no assets")
    photos = []
    for idx, asset in enumerate(assets):
        metadata = asset[2] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        photos.append({
            "file_path": asset[0],
            "file_id": metadata.get("telegram_file_id"),
            "file_unique_id": metadata.get("telegram_file_unique_id"),
            "message_id": metadata.get("telegram_message_id"),
            "media_group_id": metadata.get("telegram_media_group_id"),
            "batch_index": metadata.get("batch_index", idx),
        })
    return {
        "job_id": job_id,
        "chat_id": chat_id,
        "brand": row[0],
        "format": row[1],
        "objective": row[2],
        "instruction": row[4] or "",
        "photos": photos,
        "attempt": 0,
    }


def get_latest_content_plan(job_id: str) -> Optional[dict]:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, plan, model, revision_id, created_at FROM content_plans WHERE job_id=%s ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    plan = row[1] or {}
    if isinstance(plan, str):
        plan = json.loads(plan)
    return {
        "content_plan_id": str(row[0]),
        "plan": plan,
        "model": row[2],
        "revision_id": str(row[3]) if row[3] else None,
        "created_at": row[4].isoformat() if row[4] else None,
    }
