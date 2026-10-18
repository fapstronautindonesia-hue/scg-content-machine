# V2.0.0 Deployment Runbook

Do not expose secrets in screenshots or chat.

## Pre-deploy
1. Back up the `contentmachine` PostgreSQL database.
2. Keep the current V1 commit SHA available for rollback.
3. Apply `migrations/v2.sql` once to the `contentmachine` database.
4. Run `python scripts/preflight.py` in the release source.

## Resource commands
Use the same V2.0.0 commit for all three Coolify resources:

- API `scg-content-machine`
  - Start: `uvicorn main:app --host 0.0.0.0 --port 8000`
  - Healthcheck: `/health`

- Vision/Intake worker `scg-content-ai-worker`
  - Start: `python worker.py`
  - Healthcheck: disabled

- Content renderer `scg-content-render-worker`
  - Start: `python content_worker.py`
  - Healthcheck: disabled

## Required environment variables
Existing values can be reused:
- `DATABASE_URL`
- `REDIS_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET` (API only)
- `OPENAI_API_KEY` (workers)
- `OPENAI_VISION_MODEL`
- `OPENAI_CONTENT_MODEL` (optional; falls back to vision model)
- `OPENAI_INTENT_MODEL` (optional; falls back to content/vision model)

Optional tuning:
- `OPENAI_REASONING_EFFORT=max`
- `OPENAI_CONTENT_REASONING_EFFORT=high`
- `OPENAI_INTENT_REASONING_EFFORT=medium`
- `MAX_BATCH_PHOTOS=12`
- `DRAFT_SETTLE_SECONDS=2.5`
- `WORKER_MAX_ATTEMPTS=3`
- `RETRY_DELAY_SECONDS=2`

## Acceptance test after deployment
1. 1 photo: `Upload foto ini sebagai Story IG SCG. Minimalis, logo saja.`
2. 6 photos: `Hasil pemasangan Ibu Cindy, buatkan konten feed SCG. Jangan terlalu selling.`
3. Revision: `Cover ganti foto nomor 4, caption jangan terlalu selling.`
4. Natural approval: `ACC`.
5. Confirm `/health` reports V2.0.0 with DB and Redis connected.

If a worker reaches terminal retry failure, type `coba lagi`; the job should resume without re-uploading photos.
