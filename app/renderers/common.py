import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageFilter, ImageOps
from app.brands import get_brand


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSET_ROOT = os.path.join(ROOT, "assets")


def open_image(data: bytes) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))


def font(size: int, serif: bool = False, bold: bool = False):
    if serif:
        names = ["DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    else:
        names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"]
    roots = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts"]
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    pass
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = current + " " + word
        box = draw.textbbox((0, 0), candidate, font=fnt)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def enhance(image: Image.Image) -> Image.Image:
    # Conservative to preserve real curtain color.
    image = ImageEnhance.Contrast(image).enhance(1.035)
    image = ImageEnhance.Brightness(image).enhance(1.01)
    image = ImageEnhance.Color(image).enhance(0.99)
    return ImageEnhance.Sharpness(image).enhance(1.05)


def smart_crop(image: Image.Image, target_w: int, target_h: int, analysis: dict, zoom: float = 1.04) -> Image.Image:
    subject = analysis.get("subject_position") or {}
    sx = float(subject.get("x", 0.5) or 0.5)
    sy = float(subject.get("y", 0.5) or 0.5)
    sx, sy = max(0.05, min(0.95, sx)), max(0.05, min(0.95, sy))

    safe = analysis.get("safe_crop") or {}
    left_n = max(0.0, min(0.8, float(safe.get("left", 0.0) or 0.0)))
    right_n = max(left_n + 0.1, min(1.0, float(safe.get("right", 1.0) or 1.0)))
    top_n = max(0.0, min(0.8, float(safe.get("top", 0.0) or 0.0)))
    bottom_n = max(top_n + 0.1, min(1.0, float(safe.get("bottom", 1.0) or 1.0)))

    # First respect the AI safe region.
    l, t = int(image.width * left_n), int(image.height * top_n)
    r, b = int(image.width * right_n), int(image.height * bottom_n)
    safe_img = image.crop((l, t, max(l + 1, r), max(t + 1, b)))

    # Re-map subject to safe-region coordinates.
    safe_sx = (sx - left_n) / max(0.001, right_n - left_n)
    safe_sy = (sy - top_n) / max(0.001, bottom_n - top_n)
    safe_sx, safe_sy = max(0.0, min(1.0, safe_sx)), max(0.0, min(1.0, safe_sy))

    scale = max(target_w / safe_img.width, target_h / safe_img.height) * zoom
    rw, rh = max(target_w, int(safe_img.width * scale)), max(target_h, int(safe_img.height * scale))
    resized = safe_img.resize((rw, rh), Image.Resampling.LANCZOS)

    focus_x, focus_y = safe_sx * rw, safe_sy * rh
    left = int(focus_x - target_w * 0.5)
    top = int(focus_y - target_h * 0.48)
    left = max(0, min(left, rw - target_w))
    top = max(0, min(top, rh - target_h))
    return resized.crop((left, top, left + target_w, top + target_h))


def _remove_white(logo: Image.Image) -> Image.Image:
    rgba = logo.convert("RGBA")
    gray = ImageOps.grayscale(rgba.convert("RGB"))
    alpha = gray.point(lambda p: 0 if p >= 248 else 255 if p <= 225 else int((248 - p) / 23 * 255))
    rgba.putalpha(alpha)
    bbox = alpha.getbbox()
    return rgba.crop(bbox) if bbox else rgba


def load_logo(brand: str) -> Image.Image | None:
    key = brand.lower()
    candidates = [
        os.path.join(ASSET_ROOT, key, "logo.png"),
        os.path.join(ASSET_ROOT, key, "logo.jpg"),
        os.path.join(ASSET_ROOT, f"{key}-logo.png"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return _remove_white(Image.open(path))
            except Exception:
                continue
    return None


def place_logo(canvas: Image.Image, brand: str, position: str = "top_right", scale: float = 0.09) -> Image.Image:
    canvas = canvas.convert("RGBA")
    logo = load_logo(brand)
    if logo is None:
        cfg = get_brand(brand)
        draw = ImageDraw.Draw(canvas)
        fnt = font(32, serif=(brand.upper() == "SCG"), bold=True)
        text = brand.upper()
        box = draw.textbbox((0, 0), text, font=fnt)
        w = box[2] - box[0]
        x = canvas.width - w - 56 if "right" in position else 56
        y = 68 if "top" in position else canvas.height - 110
        draw.text((x, y), text, font=fnt, fill=hex_rgb(cfg["accent"]) + (235,))
        return canvas

    target_w = max(60, int(canvas.width * max(0.05, min(0.18, scale))))
    ratio = target_w / max(1, logo.width)
    logo = logo.resize((target_w, max(1, int(logo.height * ratio))), Image.Resampling.LANCZOS)
    margin = 54
    x = canvas.width - logo.width - margin if "right" in position else margin
    y = 64 if "top" in position else canvas.height - logo.height - 64
    shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
    alpha = logo.getchannel("A").point(lambda p: int(p * 0.22))
    shadow.putalpha(alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    canvas.alpha_composite(shadow, (x + 2, y + 3))
    canvas.alpha_composite(logo, (x, y))
    return canvas


def jpeg_bytes(image: Image.Image, quality: int = 94) -> bytes:
    out = BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()
