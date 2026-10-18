from PIL import Image, ImageDraw
from app.brands import get_brand
from app.renderers.common import open_image, smart_crop, enhance, place_logo, jpeg_bytes, hex_rgb, font, wrap


def _asset_analysis(batch_analysis: dict, index: int) -> dict:
    for item in batch_analysis.get("assets") or []:
        if int(item.get("index", -1)) == int(index):
            return item
    return {"subject_position": {"x": 0.5, "y": 0.5}, "safe_crop": {"top": 0, "right": 1, "bottom": 1, "left": 0}}


def _text_slide(slide: dict, brand: str) -> bytes:
    cfg = get_brand(brand)
    canvas = Image.new("RGBA", (1080, 1350), hex_rgb(cfg["background"]) + (255,))
    draw = ImageDraw.Draw(canvas)
    accent = hex_rgb(cfg["accent"])
    dark = hex_rgb(cfg["dark"])
    draw.rounded_rectangle((80, 110, 150, 118), radius=4, fill=accent)
    hf = font(68, serif=(brand.upper() == "SCG"), bold=True)
    bf = font(34, serif=False)
    y = 220
    for line in wrap(draw, slide.get("headline") or "", hf, 900)[:4]:
        draw.text((80, y), line, font=hf, fill=dark)
        y += 84
    y += 30
    for line in wrap(draw, slide.get("body") or "", bf, 900)[:8]:
        draw.text((80, y), line, font=bf, fill=dark)
        y += 50
    canvas = place_logo(canvas, brand, "bottom_right", 0.07)
    return jpeg_bytes(canvas)


def _photo_slide(original: bytes, analysis: dict, slide: dict, brand: str) -> bytes:
    image = enhance(smart_crop(open_image(original), 1080, 1350, analysis, zoom=1.02)).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    headline = slide.get("headline") or ""
    if headline:
        draw.rounded_rectangle((55, 1030, 1025, 1285), radius=28, fill=(20, 18, 17, 125))
        hf = font(52, serif=(brand.upper() == "SCG"), bold=True)
        y = 1080
        for line in wrap(draw, headline, hf, 850)[:2]:
            draw.text((90, y), line, font=hf, fill=(255, 255, 255, 255))
            y += 66
    image = place_logo(image, brand, "top_right", 0.065)
    return jpeg_bytes(image)


def render_carousel(originals: list[bytes], batch_analysis: dict, plan: dict, brand: str) -> list[bytes]:
    slides = plan.get("carousel_slides") or []
    selected = plan.get("selected_asset_indices") or batch_analysis.get("selected_asset_indices") or [0]
    if not slides:
        slides = [{"type": "photo", "asset_index": idx, "headline": plan.get("headline") if pos == 0 else None, "body": None} for pos, idx in enumerate(selected[:6])]
    slides = slides[:6]
    rendered = []
    fallback_index = int(batch_analysis.get("hero_asset_index", 0) or 0)
    for slide in slides:
        if slide.get("type") in {"photo", "detail", "cover"}:
            idx = int(slide.get("asset_index", fallback_index) or 0)
            idx = max(0, min(idx, len(originals) - 1))
            rendered.append(_photo_slide(originals[idx], _asset_analysis(batch_analysis, idx), slide, brand))
        else:
            rendered.append(_text_slide(slide, brand))
    return rendered
