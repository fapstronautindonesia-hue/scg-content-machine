from contextlib import contextmanager
import psycopg
from app.config import settings


@contextmanager
def db_connection():
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        yield conn


def ping_database() -> bool:
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False
