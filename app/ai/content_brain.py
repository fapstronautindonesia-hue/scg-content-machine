import json
from openai import OpenAI
from app.config import settings
from app.brands import get_brand
from app.services.usage import extract_usage

client = OpenAI(api_key=settings.openai_api_key)


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
        raise RuntimeError("Content Brain output must be a JSON object")
    return value


def social_proof_plan(context: dict, revision: dict | None = None) -> dict:
    instruction = ((revision or {}).get("instruction") or "").lower()
    selected = context.get("analysis", {}).get("selected_asset_indices") or [context.get("analysis", {}).get("hero_asset_index", 0)]
    layout = {
        "photo_dominance": 0.97,
        "logo_only": True,
        "text": False,
        "show_logo": True,
        "logo_position": "top_right",
        "logo_scale": 0.075,
        "crop_zoom": 1.02,
    }
    if "logo lebih kecil" in instruction or "kecilkan logo" in instruction:
        layout["logo_scale"] = 0.055
    if "logo lebih besar" in instruction or "besarkan logo" in instruction:
        layout["logo_scale"] = 0.10
    if "hilangkan logo" in instruction or "tanpa logo" in instruction:
        layout["show_logo"] = False
    if "kiri" in instruction and "logo" in instruction:
        layout["logo_position"] = "top_left"
    if "bawah" in instruction and "logo" in instruction:
        layout["logo_position"] = "bottom_right" if "kanan" in instruction else "bottom_left"
    if "crop jangan terlalu dekat" in instruction or "crop lebih jauh" in instruction or "zoom out" in instruction:
        layout["crop_zoom"] = 1.0
    if "crop lebih dekat" in instruction or "zoom in" in instruction:
        layout["crop_zoom"] = 1.07
    return {
        "mode": "social_proof",
        "brand": context["brand"],
        "format": "STORY",
        "objective": context["objective"],
        "selected_asset_indices": selected[:5],
        "layout": layout,
        "revision_instruction": (revision or {}).get("instruction"),
        "captions": {},
    }


def generate(context: dict, revision: dict | None = None) -> dict:
    brand = context["brand"].upper()
    fmt = context["format"].upper()
    objective = context["objective"].upper()
    analysis = dict(context["analysis"])
    analysis.pop("_meta", None)
    brand_cfg = get_brand(brand)
    revision_text = (revision or {}).get("instruction")
    instruction = context.get("instruction") or ""
    previous_plan = context.get("previous_plan") or None

    if fmt == "STORY" and objective in {"AUTO", "SHOWCASE"}:
        return {"plan": social_proof_plan(context, revision), "model": None, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}

    prompt = f"""
Kamu adalah Creative Director + Caption Strategist untuk Content Machine.

BRAND: {brand}
BRAND CHARACTER: {brand_cfg['positioning']}
CHANNEL UTAMA: {context.get('channel', 'INSTAGRAM')}
FORMAT: {fmt}
OBJECTIVE: {objective}
PERINTAH ASLI USER: {instruction}
VISION BATCH ANALYSIS:
{json.dumps(analysis, ensure_ascii=False, indent=2)}

INSTRUKSI REVISI USER (jika ada):
{revision_text or 'Tidak ada'}

RENDER PLAN VERSI SEBELUMNYA (jika revisi):
{json.dumps(previous_plan, ensure_ascii=False, indent=2) if previous_plan else 'Tidak ada'}

RULE:
- Ikuti maksud user, bukan menu/form.
- Jangan mengarang harga, diskon, ukuran, bahan kain spesifik, persentase blackout, lokasi, testimoni, atau garansi.
- Nama customer boleh dipakai hanya jika user sendiri menyebutkannya pada PERINTAH ASLI USER.
- SCG = premium, warm, editorial, soft selling.
- ILMI = clean, accessible, product-first, commerce friendly.
- Story AUTO/SHOWCASE harus minimalis dan photo-first.
- Untuk Carousel, gunakan foto berbeda sesuai ranking Vision. Jangan mengulang foto yang sama jika ada alternatif layak.
- asset_index harus merujuk indeks pada VISION BATCH ANALYSIS.assets.
- Saat user menyebut "foto nomor 1", maksudnya asset_index 0; "foto nomor 4" = asset_index 3.
- Jika ini revisi, pertahankan bagian render plan sebelumnya yang tidak diminta berubah. Jangan mendesain ulang tanpa alasan.
- Caption tiap channel natural, tidak identik, dan tidak terlalu selling kecuali user memang meminta promo.
- Jika user meminta "feed" dari banyak foto dan format sudah CAROUSEL, buat 3-6 slide sesuai jumlah foto layak.

OUTPUT JSON VALID SAJA:
{{
  "mode": "editorial|education|promo|testimonial|social_proof",
  "angle": "string",
  "headline": "maksimal 8 kata atau null",
  "supporting_text": "maksimal 20 kata atau null",
  "hero_asset_index": 0,
  "selected_asset_indices": [0],
  "layout": {{
    "template": "full_bleed|framed_editorial|split_editorial|minimal_story",
    "text_position": "top|bottom|none",
    "logo_position": "top_right|top_left|bottom_right|bottom_left",
    "logo_scale": 0.08
  }},
  "carousel_slides": [
    {{"type": "cover|photo|detail|education|closing", "asset_index": 0, "headline": "string atau null", "body": "string atau null"}}
  ],
  "captions": {{
    "instagram": "string atau null",
    "facebook": "string atau null",
    "tiktok": "string atau null",
    "whatsapp": "string atau null"
  }}
}}
"""
    response = client.responses.create(
        model=settings.content_model,
        reasoning={"effort": settings.content_reasoning},
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    return {
        "plan": _clean_json(response.output_text),
        "model": getattr(response, "model", settings.content_model),
        "usage": extract_usage(response),
        "response_id": getattr(response, "id", None),
    }
