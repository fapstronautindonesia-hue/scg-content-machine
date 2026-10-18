from PIL import ImageDraw
from app.brands import get_brand
from app.renderers.common import (
    open_image, smart_crop, enhance, place_logo, jpeg_bytes,
    font, wrap, hex_rgb,
)


def render_story(original: bytes, analysis: dict, plan: dict, brand: str) -> bytes:
    layout = plan.get("layout") or {}
    zoom = float(layout.get("crop_zoom", 1.03) or 1.03)
    image = enhance(smart_crop(open_image(original), 1080, 1920, analysis, zoom=zoom)).convert("RGBA")

    if plan.get("mode") != "social_proof":
        # Story remains restrained even for education/promo/testimonial objectives.
        draw = ImageDraw.Draw(image, "RGBA")
        text_position = layout.get("text_position", "bottom")
        if text_position != "none":
            if text_position == "top":
                y0, y1 = 0, 720
                for y in range(y0, y1):
                    alpha = int(145 * (1 - y / max(1, y1)))
                    draw.line([(0, y), (1080, y)], fill=(18, 16, 15, alpha))
                y = 130
            else:
                y0, y1 = 1120, 1920
                for yy in range(y0, y1):
                    alpha = int(170 * ((yy - y0) / max(1, y1 - y0)))
                    draw.line([(0, yy), (1080, yy)], fill=(18, 16, 15, alpha))
                y = 1390
            hf = font(62, serif=(brand.upper() == "SCG"), bold=True)
            bf = font(30, serif=False)
            for line in wrap(draw, plan.get("headline") or "", hf, 900)[:3]:
                draw.text((76, y), line, font=hf, fill=(255, 255, 255, 255))
                y += 76
            if plan.get("supporting_text"):
                y += 16
                for line in wrap(draw, plan.get("supporting_text") or "", bf, 900)[:3]:
                    draw.text((76, y), line, font=bf, fill=(255, 255, 255, 240))
                    y += 42

    if layout.get("show_logo", True):
        image = place_logo(
            image, brand,
            position=layout.get("logo_position", "top_right"),
            scale=float(layout.get("logo_scale", 0.085) or 0.085),
        )
    return jpeg_bytes(image)
