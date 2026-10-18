import json
import re
from app.config import settings
from app.services.usage import extract_usage


def _clean_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    value = json.loads(cleaned.strip())
    if not isinstance(value, dict):
        raise RuntimeError("Intent output must be a JSON object")
    return value


def _obvious(text: str, photo_count: int, defaults: dict) -> dict | None:
    """Fast deterministic route for common conversational commands.

    Priority is explicit instruction > strong business context > remembered preference.
    This prevents an old ILMI preference from overriding a new 'hasil pemasangan' SCG job.
    """
    t = (text or "").lower()

    explicit_brand = (
        "SCG" if re.search(r"\bscg\b|serbacantik", t)
        else "ILMI" if re.search(r"\bilmi\b", t)
        else None
    )
    contextual_brand = None
    if re.search(r"hasil pemasangan|rumah (ibu|bapak|pak|bu)|customer|custom gorden|survei|survey", t):
        contextual_brand = "SCG"
    elif re.search(r"shopee|marketplace|produk ready|produk jadi|ready stock", t):
        contextual_brand = "ILMI"
    brand = explicit_brand or contextual_brand or defaults.get("last_brand")

    if re.search(r"\big\b|instagram", t):
        channel = "INSTAGRAM"
    elif re.search(r"facebook|\bfb\b", t):
        channel = "FACEBOOK"
    elif re.search(r"tiktok|tik tok", t):
        channel = "TIKTOK"
    elif re.search(r"whatsapp|\bwa\b", t):
        channel = "WHATSAPP"
    else:
        channel = defaults.get("last_channel") or "INSTAGRAM"

    wants_story = bool(re.search(r"\bstory\b|stories|instastory", t))
    wants_feed = bool(re.search(r"\bfeed\b", t))
    wants_carousel = bool(re.search(r"carousel|karusel", t))

    if wants_story and (wants_feed or wants_carousel):
        fmt = "FEED_STORY"
    elif wants_story:
        fmt = "STORY"
    elif wants_carousel:
        fmt = "CAROUSEL"
    elif wants_feed:
        fmt = "CAROUSEL" if photo_count >= 3 and not re.search(r"single|satu foto|1 foto|feed tunggal", t) else "FEED"
    else:
        fmt = None

    objective = (
        "PROMO" if re.search(r"promo|diskon|sale|potongan harga", t)
        else "TESTIMONI" if re.search(r"testimoni|review|ulasan", t)
        else "EDUKASI" if re.search(r"edukasi|tips|cara|kenapa|panduan", t)
        else "SHOWCASE" if re.search(r"hasil pemasangan|showcase|portfolio|portofolio|social proof", t)
        else "AUTO"
    )

    if brand and fmt:
        return {
            "brand": brand,
            "channel": channel,
            "format": fmt,
            "objective": objective,
            "customer_context": text.strip() or None,
            "creative_instruction": text.strip(),
            "needs_clarification": False,
            "clarification_question": None,
            "confidence": 0.97,
            "assumptions": [],
        }
    return None


def parse(instruction: str, photo_count: int, defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    quick = _obvious(instruction, photo_count, defaults)
    if quick:
        return {
            "intent": quick,
            "model": "deterministic",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    prompt = f"""
Kamu adalah Intent Router untuk AI Content Assistant bisnis gorden.
User berbicara natural seperti kepada ChatGPT. Jangan paksa user mengisi form.

INSTRUKSI USER:
{instruction}

JUMLAH FOTO: {photo_count}
DEFAULT DARI PERCAKAPAN SEBELUMNYA:
{json.dumps(defaults, ensure_ascii=False)}

Tugasmu mengubah perintah menjadi struktur kerja.
- Brand hanya SCG atau ILMI.
- Prioritas brand: brand yang disebut eksplisit > konteks bisnis kuat > last_brand.
- Konteks hasil pemasangan/custom rumah/survei biasanya SCG.
- Konteks marketplace/Shopee/ready stock biasanya ILMI.
- Channel default INSTAGRAM jika user tidak menyebut channel.
- Format: STORY, FEED, CAROUSEL, atau FEED_STORY.
- Jika user meminta Feed dan Story sekaligus, gunakan FEED_STORY.
- Jika user bilang feed dan ada >=3 foto, boleh pilih CAROUSEL bila lebih masuk akal, kecuali user jelas meminta satu gambar.
- hasil pemasangan biasanya SHOWCASE/social proof, bukan promo.
- Story SCG AUTO/SHOWCASE default minimalis: foto dominan + logo, tanpa selling text.
- Jangan mengarang identitas customer. Nama customer hanya boleh dipakai jika user sendiri menuliskannya.
- Hanya minta klarifikasi jika BRAND benar-benar tidak dapat ditentukan. Jangan bertanya format bila AI dapat mengambil keputusan yang masuk akal.

OUTPUT JSON VALID SAJA:
{{
  "brand": "SCG|ILMI|null",
  "channel": "INSTAGRAM|FACEBOOK|TIKTOK|WHATSAPP",
  "format": "STORY|FEED|CAROUSEL|FEED_STORY",
  "objective": "AUTO|SHOWCASE|EDUKASI|PROMO|TESTIMONI",
  "customer_context": "string atau null",
  "creative_instruction": "string",
  "needs_clarification": false,
  "clarification_question": "string atau null",
  "confidence": 0.0,
  "assumptions": ["string"]
}}
"""
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.intent_model,
        reasoning={"effort": settings.intent_reasoning},
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    return {
        "intent": _clean_json(response.output_text),
        "model": getattr(response, "model", settings.intent_model),
        "response_id": getattr(response, "id", None),
        "usage": extract_usage(response),
    }
