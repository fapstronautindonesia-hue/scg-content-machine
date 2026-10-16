import os

import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="SCG Content Machine",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "system": "SCG Content Machine",
        "status": "ONLINE",
        "version": "1.0.0"
    }


@app.get("/health")
def health():
    database_status = "disconnected"
    redis_status = "disconnected"

    # Check PostgreSQL
    try:
        database_url = os.getenv("DATABASE_URL")

        if not database_url:
            raise RuntimeError("DATABASE_URL is missing")

        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        database_status = "connected"

    except Exception:
        database_status = "disconnected"

    # Check Redis
    try:
        redis_url = os.getenv("REDIS_URL")

        if not redis_url:
            raise RuntimeError("REDIS_URL is missing")

        redis_client = redis.from_url(
            redis_url,
            socket_connect_timeout=5,
            socket_timeout=5
        )

        redis_client.ping()
        redis_status = "connected"

    except Exception:
        redis_status = "disconnected"

    healthy = (
        database_status == "connected"
        and redis_status == "connected"
    )

    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "service": "content-machine",
        "version": "1.0.0",
        "database": database_status,
        "redis": redis_status
    }

    return JSONResponse(
        payload,
        status_code=200 if healthy else 503
    )
