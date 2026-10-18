import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    redis_url: str = os.getenv("REDIS_URL", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    vision_model: str = os.getenv("OPENAI_VISION_MODEL", "gpt-6-astra")
    content_model: str = os.getenv("OPENAI_CONTENT_MODEL", os.getenv("OPENAI_VISION_MODEL", "gpt-6-astra"))
    intent_model: str = os.getenv("OPENAI_INTENT_MODEL", os.getenv("OPENAI_CONTENT_MODEL", os.getenv("OPENAI_VISION_MODEL", "gpt-6-astra")))

    vision_reasoning: str = os.getenv("OPENAI_REASONING_EFFORT", "max")
    content_reasoning: str = os.getenv("OPENAI_CONTENT_REASONING_EFFORT", "high")
    intent_reasoning: str = os.getenv("OPENAI_INTENT_REASONING_EFFORT", "medium")

    intake_queue: str = os.getenv("AI_INTAKE_QUEUE", "contentmachine:intake_queue")
    analysis_queue: str = os.getenv("AI_ANALYSIS_QUEUE", "contentmachine:analysis_queue")
    content_queue: str = os.getenv("AI_CONTENT_QUEUE", "contentmachine:content_queue")

    session_ttl: int = int(os.getenv("SESSION_TTL", "3600"))
    draft_ttl: int = int(os.getenv("DRAFT_TTL", "3600"))
    draft_settle_seconds: float = float(os.getenv("DRAFT_SETTLE_SECONDS", "2.5"))
    content_claim_ttl: int = int(os.getenv("CONTENT_CLAIM_TTL", "1800"))
    intake_claim_ttl: int = int(os.getenv("INTAKE_CLAIM_TTL", "120"))
    telegram_timeout: int = int(os.getenv("TELEGRAM_TIMEOUT", "30"))
    max_image_bytes: int = int(os.getenv("MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
    max_batch_photos: int = int(os.getenv("MAX_BATCH_PHOTOS", "12"))
    worker_max_attempts: int = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
    retry_delay_seconds: float = float(os.getenv("RETRY_DELAY_SECONDS", "2"))

    def validate_api(self) -> None:
        missing = [name for name, value in {
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_WEBHOOK_SECRET": self.telegram_webhook_secret,
        }.items() if not value]
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    def validate_worker(self) -> None:
        missing = [name for name, value in {
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "OPENAI_API_KEY": self.openai_api_key,
        }.items() if not value]
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


settings = Settings()
