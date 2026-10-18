# SCG Content Machine V2.0.0

## Release scope
Conversation-first Telegram Content Assistant for SCG and ILMI.

### UX
- Send 1–12 photos first or send an instruction first.
- Natural language replaces mandatory brand/format/objective menus.
- Telegram albums are grouped as one content batch.
- Natural revisions, approve/ACC, reject, and manual `coba lagi` recovery.

### AI pipeline
- Deterministic fast intent route for obvious commands; AI intent fallback for ambiguous commands.
- Batch Vision analyzes all photos once, ranks/selects hero/supporting photos, and returns crop guidance.
- Story AUTO/SHOWCASE uses minimal social-proof rendering without a second copywriting AI call.
- Feed/Carousel/Feed+Story use Content Brain and channel-specific captions.
- Revision requests receive the previous render plan so unchanged decisions are preserved.

### Reliability
- Additive V1 -> V2 migration.
- Idempotent batch creation.
- Intake, analysis and content retry/recovery paths.
- Telemetry/usage logging is fail-open against the content transaction pipeline.
- Content generation renders all images before sending previews to reduce partial-output failures.
- Anti-hallucination instructions for price, discount, size, fabric specs, blackout %, location, testimonial and guarantees.

### Data
Adds: `job_requests`, `content_plans`, `renders`, `captions`, `revisions`, `usage`, `events`.
Existing `jobs`, `assets`, and `analyses` remain the V1 foundation.

## Preflight status
- Python AST/compile: PASS
- Conversation-first guards: PASS
- Callback length: PASS
- Secret literal scan: PASS
- Deterministic intent adversarial cases: PASS
- Strong-context brand override: PASS
- Feed + Story routing: PASS
- Story/Feed/Carousel renderer dimensions: PASS
- Retry/manual recovery wiring: PASS
- Telemetry fail-open wiring: PASS
- Revision continuity wiring: PASS

Live PostgreSQL migration and real Telegram/OpenAI calls are production acceptance tests and must be performed on the VPS after backup.
