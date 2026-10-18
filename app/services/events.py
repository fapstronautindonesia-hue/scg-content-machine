import json
from app.database import db_connection


def record_event(job_id: str, event_type: str, actor: str = "system", payload: dict | None = None) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (job_id, event_type, actor, payload) VALUES (%s, %s, %s, %s::jsonb)",
                (job_id, event_type, actor, json.dumps(payload or {}, ensure_ascii=False)),
            )
        conn.commit()
