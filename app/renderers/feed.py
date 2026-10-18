from PIL import Image, ImageDraw
from app.brands import get_brand
from app.renderers.common import open_image, smart_crop, enhance, place_logo, jpeg_bytes, hex_rgb, font, wrap


def render_feed(original: bytes, analysis: dict, plan: dict, brand: str) -> bytes:
    cfg = get_brand(brand)
    source = open_image(original)
    template = (plan.get("layout") or {}).get("template", "framed_editorial")

    if template == "full_bleed":
        canvas = enhance(smart_crop(source, 1080, 1350, analysis, zoom=1.03)).convert("RGBA")
        draw = ImageDraw.Draw(canvas, "RGBA")
        for y in range(760, 1350):
            alpha = int(175 * ((y - 760) / 590))
            draw.line([(0, y), (1080, y)], fill=(20, 18, 17, alpha))
        text_color = (255, 255, 255, 255)
        x, y = 72, 970
    else:
        bg = hex_rgb(cfg["background"])
        canvas = Image.new("RGBA", (1080, 1350), bg + (255,))
        photo = enhance(smart_crop(source, 920, 850, analysis, zoom=1.01))
        canvas.paste(photo.convert("RGBA"), (80, 120))
        text_color = hex_rgb(cfg["dark"]) + (255,)
        x, y = 80, 1020

    draw = ImageDraw.Draw(canvas)
    headline = plan.get("headline") or ""
    supporting = plan.get("supporting_text") or ""
    hf = font(64, serif=(brand.upper() == "SCG"), bold=True)
    bf = font(30, serif=False, bold=False)
    for line in wrap(draw, headline, hf, 900)[:3]:
        draw.text((x, y), line, font=hf, fill=text_color)
        y += 78
    if supporting:
        y += 8
        for line in wrap(draw, supporting, bf, 900)[:2]:
            draw.text((x, y), line, font=bf, fill=text_color)
            y += 42
    canvas = place_logo(canvas, brand, position="top_right", scale=0.075)
    return jpeg_bytes(canvas)
