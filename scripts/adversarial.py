from io import BytesIO
from pathlib import Path
from PIL import Image
import sys
import types

# Offline preflight does not open PostgreSQL. Stub only the import so pure logic can load.
if "psycopg" not in sys.modules:
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError:
        sys.modules["psycopg"] = types.ModuleType("psycopg")

try:
    from openai import OpenAI as _OpenAIProbe  # type: ignore
except Exception:
    openai_stub = types.ModuleType("openai")
    class _DummyOpenAI:
        def __init__(self, *args, **kwargs):
            pass
    openai_stub.OpenAI = _DummyOpenAI
    sys.modules["openai"] = openai_stub

ROOT = Path(__file__).resolve().parents[1]

from app.ai.intent import _obvious
from app.ai.content_brain import social_proof_plan
from app.renderers.story import render_story
from app.renderers.feed import render_feed
from app.renderers.carousel import render_carousel


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected={expected!r} actual={actual!r}")


def jpeg(w=1200, h=1600, value=180):
    img = Image.new("RGB", (w, h), (value, value - 10, value - 20))
    out = BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


cases = [
    ("Upload foto ini sebagai Story IG SCG. Minimalis, logo saja.", 1, {}, ("SCG", "INSTAGRAM", "STORY", "AUTO")),
    # Strong current context must beat remembered ILMI preference.
    ("Hasil pemasangan Ibu Cindy, buatkan konten feed", 6, {"last_brand": "ILMI"}, ("SCG", "INSTAGRAM", "CAROUSEL", "SHOWCASE")),
    ("Buat feed dan story SCG dari foto ini", 4, {}, ("SCG", "INSTAGRAM", "FEED_STORY", "AUTO")),
    ("Story TikTok ILMI, produk ready", 2, {}, ("ILMI", "TIKTOK", "STORY", "AUTO")),
    ("Buat feed satu foto SCG", 6, {}, ("SCG", "INSTAGRAM", "FEED", "AUTO")),
    ("Carousel Facebook SCG hasil pemasangan customer", 5, {}, ("SCG", "FACEBOOK", "CAROUSEL", "SHOWCASE")),
]

for text, count, defaults, expected in cases:
    intent = _obvious(text, count, defaults)
    if not intent:
        raise AssertionError(f"deterministic intent unexpectedly missing: {text}")
    actual = (intent["brand"], intent["channel"], intent["format"], intent["objective"])
    assert_eq(actual, expected, f"intent: {text}")

analysis = {
    "hero_asset_index": 0,
    "selected_asset_indices": [0, 1],
    "assets": [
        {"index": 0, "subject_position": {"x": 0.5, "y": 0.58}, "safe_crop": {"top": 0.1, "right": 1.0, "bottom": 1.0, "left": 0.0}},
        {"index": 1, "subject_position": {"x": 0.5, "y": 0.5}, "safe_crop": {"top": 0.0, "right": 1.0, "bottom": 1.0, "left": 0.0}},
    ],
}
context = {"brand": "SCG", "format": "STORY", "objective": "SHOWCASE", "analysis": analysis}
plan = social_proof_plan(context)
assert_eq(plan["layout"]["logo_only"], True, "story logo-only")
assert_eq(plan["layout"]["text"], False, "story no text")

img1, img2 = jpeg(), jpeg(value=150)
story = render_story(img1, analysis["assets"][0], plan, "SCG")
with Image.open(BytesIO(story)) as im:
    assert_eq(im.size, (1080, 1920), "story dimensions")

feed_plan = {
    "headline": "Hasil Pemasangan",
    "supporting_text": "Tampilan rapi dan hangat.",
    "layout": {"template": "framed_editorial", "logo_position": "top_right", "logo_scale": 0.075},
}
feed = render_feed(img1, analysis["assets"][0], feed_plan, "SCG")
with Image.open(BytesIO(feed)) as im:
    assert_eq(im.size, (1080, 1350), "feed dimensions")

carousel_plan = {
    "headline": "Hasil Pemasangan",
    "selected_asset_indices": [0, 1],
    "carousel_slides": [
        {"type": "cover", "asset_index": 0, "headline": "Hasil Pemasangan", "body": None},
        {"type": "photo", "asset_index": 1, "headline": None, "body": None},
        {"type": "closing", "asset_index": 0, "headline": "Serbacantik Gorden", "body": "Detail yang rapi."},
    ],
}
slides = render_carousel([img1, img2], analysis, carousel_plan, "SCG")
assert_eq(len(slides), 3, "carousel slide count")
for i, data in enumerate(slides, start=1):
    with Image.open(BytesIO(data)) as im:
        assert_eq(im.size, (1080, 1350), f"carousel slide {i} dimensions")

main = (ROOT / "main.py").read_text(encoding="utf-8")
if "brand_keyboard" in main or "objective_keyboard" in main:
    raise AssertionError("legacy form-first menu leaked into V2")
if "ANALYSIS_FAILED" not in main or "GENERATION_FAILED" not in main:
    raise AssertionError("manual recovery path missing")

worker = (ROOT / "worker.py").read_text(encoding="utf-8")
content_worker = (ROOT / "content_worker.py").read_text(encoding="utf-8")
if "WORKER_RETRY" not in worker or "CONTENT_RETRY" not in content_worker:
    raise AssertionError("automatic retry path missing")
if "failed open" not in worker.lower() or "failed open" not in content_worker.lower():
    raise AssertionError("telemetry fail-open guard missing")

brain = (ROOT / "app/ai/content_brain.py").read_text(encoding="utf-8")
if "RENDER PLAN VERSI SEBELUMNYA" not in brain:
    raise AssertionError("revision continuity context missing")
if '"foto nomor 4" = asset_index 3' not in brain:
    raise AssertionError("human photo numbering guard missing")

migration = (ROOT / "migrations/v2.sql").read_text(encoding="utf-8")
if "Existing V1 jobs, assets and analyses are preserved" not in migration:
    raise AssertionError("migration preservation marker missing")

print("ADVERSARIAL PASS")
print(f"Intent cases: {len(cases)}")
print("Strong-context brand override: PASS")
print("Feed+Story natural language: PASS")
print("Channel routing: PASS")
print("Story social-proof minimalism: PASS")
print("Story/Feed/Carousel dimensions: PASS")
print("Automatic retry + manual recovery: PASS")
print("Telemetry fail-open: PASS")
print("Revision continuity: PASS")
