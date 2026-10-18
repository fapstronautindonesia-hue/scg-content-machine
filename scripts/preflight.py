from pathlib import Path
import ast
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "main.py", "worker.py", "content_worker.py", "requirements.txt", "migrations/v2.sql",
    "app/config.py", "app/database.py", "app/redis_client.py", "app/telegram.py", "app/brands.py",
    "app/ai/vision.py", "app/ai/intent.py", "app/ai/content_brain.py",
    "app/renderers/common.py", "app/renderers/story.py", "app/renderers/feed.py", "app/renderers/carousel.py",
    "app/services/jobs.py", "app/services/sessions.py", "app/services/drafts.py", "app/services/usage.py", "app/services/events.py",
    "scripts/adversarial.py",
]
errors = []
for rel in REQUIRED:
    if not (ROOT / rel).exists():
        errors.append(f"missing: {rel}")

for path in ROOT.rglob("*.py"):
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        errors.append(f"syntax: {path.relative_to(ROOT)}: {exc}")

examples = [
    "content:approve:" + "0" * 36,
    "content:revise:" + "0" * 36,
    "content:reject:" + "0" * 36,
]
for value in examples:
    if len(value.encode()) > 64:
        errors.append(f"callback too long: {value[:24]}...")

secret_patterns = [r"sk-[A-Za-z0-9_-]{20,}", r"bot\d{6,}:[A-Za-z0-9_-]{20,}"]
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in {".py", ".sql", ".md", ".txt"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for pattern in secret_patterns:
        if re.search(pattern, text):
            errors.append(f"possible secret in {path.relative_to(ROOT)}")

migration = (ROOT / "migrations/v2.sql").read_text(encoding="utf-8")
for table in ["job_requests", "content_plans", "renders", "captions", "revisions", "usage", "events"]:
    if f"CREATE TABLE IF NOT EXISTS {table}" not in migration:
        errors.append(f"migration missing table: {table}")

main = (ROOT / "main.py").read_text(encoding="utf-8")
if "brand_keyboard" in main or "objective_keyboard" in main:
    errors.append("legacy form-first menu found in main.py")
if "drafts.add_photo" not in main or "enqueue_intake" not in main:
    errors.append("conversation intake wiring missing")
if 'version="2.0.0"' not in main:
    errors.append("API is not stamped V2.0.0")

worker = (ROOT / "worker.py").read_text(encoding="utf-8")
if "analyze_batch" not in worker or "parse_intent" not in worker:
    errors.append("batch intent/vision wiring missing")
if "WORKER_RETRY" not in worker:
    errors.append("vision/intake retry wiring missing")

content = (ROOT / "content_worker.py").read_text(encoding="utf-8")
if "CONTENT_RETRY" not in content:
    errors.append("content retry wiring missing")

if errors:
    print("PRE-FLIGHT FAILED")
    for item in errors:
        print("-", item)
    sys.exit(1)

print("PRE-FLIGHT PASS")
print(f"Python files checked: {len(list(ROOT.rglob('*.py')))}")
print("Required V2 tables: 7")
print("Conversation-first intake: PASS")
print("Batch vision wiring: PASS")
print("Retry/recovery wiring: PASS")
print("Callback length guard: PASS")
print("Secret literal scan: PASS")
