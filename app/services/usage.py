import json
from app.database import db_connection


def extract_usage(response) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def record_usage(job_id: str, operation: str, model: str, usage: dict, metadata: dict | None = None) -> None:
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usage (job_id, operation, model, input_tokens, output_tokens, total_tokens, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    job_id,
                    operation,
                    model,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("total_tokens", 0),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        conn.commit()
