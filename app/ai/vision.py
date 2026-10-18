import base64
import json
from openai import OpenAI
from app.config import settings
from app.brands import get_brand
from app.services.usage import extract_usage

client = OpenAI(api_key=settings.openai_api_key)


def detect_mime(image_bytes: bytes, fallback: str = "image/jpeg") -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "image/webp"
    return fallback or "image/jpeg"


def _clean_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    result = json.loads(cleaned.strip())
    if not isinstance(result, dict):
        raise RuntimeError("Vision output must be a JSON object")
    return result


def analyze_batch(images: list[dict], brand: str, content_format: str, objective: str, instruction: str = "") -> dict:
    if not images:
        raise ValueError("Vision batch is empty")
    brand_cfg = get_brand(brand)
    prompt = f"""
Kamu adalah Vision Brain + Photo Editor untuk Content Machine bisnis gorden.

BRAND: {brand}
KARAKTER BRAND: {brand_cfg['positioning']}
FORMAT: {content_format}
OBJECTIVE: {objective}
INSTRUKSI USER: {instruction or '-'}
JUMLAH FOTO: {len(images)}

Semua gambar setelah prompt ini berurutan sebagai FOTO 0, FOTO 1, FOTO 2, dst.
Analisis seluruh batch SEKALI. Ranking foto berdasarkan kegunaan nyata untuk konten, bukan sekadar estetika.
Pilih hero terbaik dan foto pendukung yang tidak repetitif.

DILARANG MENGARANG: harga, diskon, ukuran, identitas/lokasi customer, bahan kain spesifik,
persentase blackout, testimoni, garansi, atau spesifikasi yang tidak terlihat/tidak diberikan user.
Nama customer yang tertulis oleh user boleh dipertahankan hanya sebagai konteks caption, bukan fakta visual.

Untuk SETIAP foto keluarkan subject_position dan safe_crop normalized 0..1.
Jangan memotong rel/batang, ring, lipatan utama, vitrase, atau bagian produk penting bila bisa dihindari.

OUTPUT JSON VALID SAJA:
{{
  "scene": "ringkasan batch",
  "product": "string atau null",
  "dominant_colors": ["string"],
  "room_style": "string atau null",
  "usable": true,
  "hero_asset_index": 0,
  "selected_asset_indices": [0],
  "recommended_angle": "showcase|edukasi|promo|product_detail|transformation|trust|inspiration",
  "recommended_formats": ["STORY", "FEED", "CAROUSEL"],
  "selling_points": ["string"],
  "content_idea": "string",
  "assets": [
    {{
      "index": 0,
      "visual_quality_score": 1,
      "role": "hero|wide|detail|support|skip",
      "product_focus": "upper|center|lower|full",
      "subject_position": {{"x": 0.5, "y": 0.5}},
      "safe_crop": {{"top": 0.0, "right": 1.0, "bottom": 1.0, "left": 0.0}},
      "ceiling_ratio": 0.0,
      "floor_ratio": 0.0,
      "visual_strengths": ["string"],
      "visual_issues": ["string"],
      "visual_distractions": ["string"]
    }}
  ]
}}
Seluruh teks dalam Bahasa Indonesia.
"""
    content = [{"type": "input_text", "text": prompt}]
    for item in images:
        mime = detect_mime(item["bytes"], item.get("mime_type") or "image/jpeg")
        data_url = f"data:{mime};base64,{base64.b64encode(item['bytes']).decode('utf-8')}"
        content.append({"type": "input_image", "image_url": data_url, "detail": "high"})

    response = client.responses.create(
        model=settings.vision_model,
        reasoning={"effort": settings.vision_reasoning},
        input=[{"role": "user", "content": content}],
    )
    return {
        "analysis": _clean_json(response.output_text),
        "model": getattr(response, "model", settings.vision_model),
        "response_id": getattr(response, "id", None),
        "usage": extract_usage(response),
    }
