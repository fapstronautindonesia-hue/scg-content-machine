BRANDS = {
    "SCG": {
        "name": "Serbacantik Gorden",
        "positioning": "custom curtain premium, warm, elegant, editorial interior, soft selling",
        "primary": "#B37F6F",
        "accent": "#C1A25A",
        "background": "#F6F0EB",
        "dark": "#332C29",
        "white": "#FFFFFF",
        "font_preferred": "Masqualero",
        "font_fallback": "DejaVuSerif",
        "story_default": "social_proof",
    },
    "ILMI": {
        "name": "ILMI Gorden",
        "positioning": "accessible, clean, trusted, modern, product-first, marketplace friendly",
        "primary": "#1F6F5C",
        "accent": "#E8562F",
        "secondary": "#F4C95D",
        "background": "#F7F9F8",
        "dark": "#1F2D2A",
        "white": "#FFFFFF",
        "font_preferred": "Poppins",
        "font_fallback": "DejaVuSans",
        "story_default": "product_proof",
    },
}


def get_brand(brand: str) -> dict:
    key = (brand or "").upper()
    if key not in BRANDS:
        raise ValueError(f"Unsupported brand: {brand}")
    return BRANDS[key]
