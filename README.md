# SCG Content Machine V2.0.0 — Conversation First

Telegram is an AI Content Assistant, not a form.

## User experience
Send 1–12 photos, then write a natural instruction, or put the instruction in the Telegram photo/album caption.

Examples:
- `Upload foto ini sebagai Story IG SCG. Minimalis, logo saja.`
- `Hasil pemasangan Ibu Cindy. Pilih foto terbaik dan buatkan Feed SCG.`
- `Dari 8 foto ini buat Carousel SCG + beberapa Story. Jangan terlalu selling.`

The system resolves intent, analyzes the photo batch once, ranks/selects assets, renders content, writes channel captions, and returns previews automatically.

After a preview, revisions are conversational:
- `Cover ganti foto nomor 4.`
- `Crop jangan terlalu dekat.`
- `Caption jangan terlalu selling.`
- `ACC`

## Runtime resources
- `main.py` — Telegram webhook, conversational intake, approval/revision/recovery
- `worker.py` — intake/intent router + Astra batch vision
- `content_worker.py` — creative brain + Story/Feed/Carousel renderer + captions

## V2 migration
Apply `migrations/v2.sql` once before switching production resources to V2.
Existing V1 `jobs`, `assets`, and `analyses` remain intact. V2 adds `job_requests`, `content_plans`, `renders`, `captions`, `revisions`, `usage`, and `events`.

## Key behavior
- No mandatory `/start` flow or brand/format/objective menus.
- Multi-photo Telegram batches become one content job.
- SCG Story AUTO/SHOWCASE = photo-first social proof, small logo, no selling text.
- Feed from 3+ photos may become Carousel when that better matches the instruction.
- Strong current context beats remembered brand preference; e.g. `hasil pemasangan` routes to SCG unless the user explicitly names ILMI.
- Feed + Story requests route to `FEED_STORY`.
- After preview, users can type revisions naturally without pressing a form button.
- Intake/analysis/render retries are automatic; after terminal failure `coba lagi` resumes from stored work without re-uploading.
- Telemetry and usage logging are fail-open against core content processing.
- Anti-hallucination rules prohibit invented prices, discounts, sizes, customer location, fabric specs, blackout percentages, testimonials, or guarantees.

See `RELEASE_NOTES.md` and `DEPLOY_V2.md` before production deployment.
