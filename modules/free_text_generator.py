"""
free_text_generator.py
=======================
100% FREE replacement for OpenAI-based text generation.

Handles three tasks without any paid API:
  1. generate_ideas(niche, count)  → list of design idea dicts
  2. build_prompt(idea)            → image-generation prompt string
  3. generate_seo(idea, niche)     → {title, description, tags}

Method: curated template libraries + randomised combination.
The templates are broad enough to cover hundreds of niche/style
combinations and produce varied, commercially-viable content every run.
"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# ── DESIGN IDEA TEMPLATES ────────────────────────────────────────────────
# Each entry: (concept_template, emotion, product_hint)
# {niche} and {adj} are filled in at runtime.
# ---------------------------------------------------------------------------

_IDEA_TEMPLATES: list[tuple[str, str, str]] = [
    # ── Cyberpunk / Techwear ─────────────────────────────────────────────
    ("{adj} {niche} girl in neon-lit cyberpunk alley, techwear jacket",
     "Electric rebellion", "t-shirt,poster,phone-case"),
    ("High contrast {niche} android warrior, glowing circuit tattoos",
     "Digital power", "poster,t-shirt,sticker"),
    ("{niche} hacker in dark server room, neon reflections on visor",
     "Shadowed genius", "t-shirt,poster,phone-case"),
    ("Cyberpunk {niche} skyline, towering holograms and rain-slicked streets",
     "Urban dystopia", "poster,t-shirt,phone-case"),
    ("{adj} {niche} samurai with neon katana in ruined megacity",
     "Neon bushido", "t-shirt,poster,sticker"),
    ("Mecha pilot {niche} portrait, cockpit glowing, intense gaze",
     "Iron will", "poster,t-shirt,phone-case"),
    ("Glitch art {niche} portrait, digital corruption on dark background",
     "Digital fracture", "poster,sticker,phone-case"),

    # ── Solarpunk ────────────────────────────────────────────────────────
    ("{adj} {niche} girl surrounded by bioluminescent vines and solar panels",
     "Radiant future", "poster,t-shirt,phone-case"),
    ("Solarpunk {niche} village built in giant living trees, golden light",
     "Hopeful utopia", "poster,t-shirt,sticker"),
    ("{niche} witch tending a glowing garden powered by crystal energy",
     "Nature and magic", "poster,t-shirt,phone-case"),

    # ── Dieselpunk ───────────────────────────────────────────────────────
    ("Dieselpunk {niche} mechanic girl, grease-stained overalls, amber spotlight",
     "Industrial grit", "t-shirt,poster,sticker"),
    ("{adj} {niche} pilot in retro-futuristic cockpit, art deco gauges",
     "Retro iron sky", "poster,t-shirt,phone-case"),
    ("Dark dieselpunk {niche} city, towering smokestacks and brass machinery",
     "Smoky grandeur", "poster,t-shirt,sticker"),

    # ── Vaporwave / Synthwave ────────────────────────────────────────────
    ("Vaporwave {niche} portrait, pink and cyan grid, retro neon sunset",
     "Nostalgic neon", "poster,t-shirt,phone-case"),
    ("Synthwave {niche} car chase at neon sunset, 80s outrun aesthetic",
     "Retrowave rush", "poster,t-shirt,sticker"),
    ("{adj} {niche} floating in vaporwave space, pastel grid and glitch",
     "Soft retro dream", "poster,phone-case,sticker"),

    # ── Dark / Gothic Anime ──────────────────────────────────────────────
    ("Gothic {niche} girl with dark wings in moonlit graveyard",
     "Beautiful darkness", "poster,t-shirt,phone-case"),
    ("{adj} {niche} witch casting spell, dark magic swirling around her",
     "Arcane power", "poster,t-shirt,sticker"),
    ("Dark fantasy {niche} warrior, obsidian armour and glowing runes",
     "Shadow strength", "t-shirt,poster,phone-case"),
    ("Cursed {niche} with dripping black roses and shattered mirrors",
     "Dark elegance", "poster,sticker,phone-case"),

    # ── Bold Graphic / Grunge ────────────────────────────────────────────
    ("High contrast {niche} portrait, stark black and white bold lines",
     "Raw graphic power", "poster,t-shirt,sticker"),
    ("Grunge {niche} poster, distressed texture, dripping ink, urban decay",
     "Street raw energy", "poster,t-shirt,sticker"),
    ("Bold {niche} face split between neon halves, graphic design impact",
     "Dual nature", "t-shirt,poster,phone-case"),

    # ── Cyberpop / Harajuku ──────────────────────────────────────────────
    ("Harajuku {niche} girl in explosion of neon colours and cute symbols",
     "Chaotic joy", "t-shirt,sticker,phone-case"),
    ("Cyberpop {niche} with oversized accessories and candy neon palette",
     "Sugar and circuits", "t-shirt,sticker,poster"),
    ("Y2K {adj} {niche} aesthetic, chrome text and bubblegum cyber outfits",
     "Millennium nostalgia", "t-shirt,phone-case,sticker"),
]

_ADJECTIVES: list[str] = [
    "fierce", "glowing", "neon", "shadowed", "electric",
    "ethereal", "shattered", "radiant", "corrupted", "blazing",
    "crystalline", "dark", "luminous", "cursed", "chrome",
    "fractured", "spectral", "defiant", "arcane", "vivid",
]

# Cyberpunk / anime colour palettes — high contrast is the brand identity
_COLORS: list[list[str]] = [
    ["neon pink", "electric cyan", "deep black", "white"],
    ["neon green", "midnight blue", "silver", "dark purple"],
    ["hot magenta", "acid yellow", "charcoal black", "white"],
    ["electric orange", "dark teal", "black", "pale gold"],
    ["deep purple", "neon cyan", "white", "dark grey"],
    ["vivid red", "neon blue", "black", "chrome silver"],
    ["bioluminescent green", "dark navy", "gold", "white"],
    ["amber", "rust orange", "dark brown", "ivory"],          # dieselpunk
    ["pastel pink", "lavender", "pale cyan", "white"],        # vaporwave
    ["hot pink", "lime green", "white", "black"],             # cyberpop
]

_PRODUCTS: list[list[str]] = [
    ["t-shirt", "sticker", "mug"],
    ["poster", "phone-case", "notebook"],
    ["hoodie", "tote-bag", "sticker"],
    ["t-shirt", "poster", "mug", "sticker"],
    ["sticker", "notebook", "phone-case"],
]


# ---------------------------------------------------------------------------
# ── IMAGE PROMPT TEMPLATES ───────────────────────────────────────────────
# ---------------------------------------------------------------------------

_PROMPT_STYLES: list[str] = [
    # Core brand style — high contrast anime / cyberpunk
    ("high contrast anime illustration, bold neon outlines on deep black background, "
     "cyberpunk techwear aesthetic, sharp detailed linework, magazine editorial quality"),
    ("dark cyberpunk digital painting, neon-lit atmosphere, cinematic lighting, "
     "anime-influenced character design, ultra-detailed, dramatic shadows"),
    ("bold graphic anime poster style, extreme contrast black and white with neon accents, "
     "strong silhouette, professional print quality"),
    # Solarpunk variant
    ("solarpunk illustration, vibrant greens and warm golds, "
     "bioluminescent plants, hopeful atmosphere, detailed anime style, "
     "nature meets technology aesthetic"),
    # Dieselpunk variant
    ("dieselpunk illustration, amber and rust palette, industrial grit, "
     "art deco influence, detailed mechanical elements, dramatic chiaroscuro lighting"),
    # Vaporwave / synthwave
    ("synthwave retro poster art, neon pink and cyan gradient sky, "
     "80s outrun grid, anime character, high contrast, glowing neon lines"),
    ("vaporwave aesthetic, pastel neon palette, retro-futuristic, "
     "glitch effects, anime style, dreamy yet high contrast"),
    # Dark gothic anime
    ("dark fantasy anime illustration, gothic atmosphere, deep shadows, "
     "dramatic lighting, intricate dark details, professional concept art quality"),
    # Grunge graphic
    ("bold grunge graphic design, distressed ink textures, urban street art aesthetic, "
     "extreme contrast, black and white with vivid colour pop"),
    # Cyberpop / harajuku
    ("cyberpop anime style, explosive neon colour palette, harajuku fashion, "
     "cute yet edgy, thick bold outlines, sticker-art quality"),
]

_PROMPT_QUALITY: str = (
    "transparent or white background, print-ready, ultra-high resolution, "
    "no watermarks, no text unless requested, suitable for merchandise printing, "
    "4500x5400 pixels equivalent quality, crisp clean edges"
)

_PROMPT_NEGATIVE: str = (
    "blurry, low quality, watermark, logo, copyright symbols, "
    "trademarked characters, ugly, deformed, extra limbs, "
    "photographic realism (unless stated), text overlay"
)


# ---------------------------------------------------------------------------
# ── SEO TEMPLATES ────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

_TITLE_TEMPLATES: list[str] = [
    "{adj_title} {niche_title} | High Contrast Anime Art Print",
    "{niche_title} Poster | {adj_title} Cyberpunk Anime Design",
    "{adj_title} {niche_title} | Dark Aesthetic T-Shirt",
    "{niche_title} Wall Art | {adj_title} Anime Illustration",
    "High Contrast {niche_title} | Cyberpunk Anime Poster",
    "{adj_title} {niche_title} Sticker | Dark Anime Aesthetic",
    "{niche_title} Art | {adj_title} Techwear Anime Design",
    "Dark {niche_title} | {adj_title} Anime Phone Case Art",
    "{adj_title} {niche_title} | Anime Cyberpunk Artwork",
    "{niche_title} | High Contrast {adj_title} Anime Print",
]

_DESC_TEMPLATES: list[str] = [
    (
        "High contrast {adj} {niche} art for fans of dark anime aesthetics. "
        "Bold cyberpunk illustration perfect for t-shirts, posters and phone cases. "
        "Original design, print-on-demand quality."
    ),
    (
        "This {adj} {niche} design blends anime character art with cyberpunk atmosphere. "
        "Extreme contrast, neon colours, dark backgrounds — made for those who "
        "wear their aesthetic. Available on premium merchandise worldwide."
    ),
    (
        "{adj_title} {niche} anime illustration with a dark, edgy aesthetic. "
        "Perfect for cyberpunk lovers, anime fans and techwear enthusiasts. "
        "Ships globally on t-shirts, posters, stickers and phone cases."
    ),
    (
        "Original {niche} digital art in high-contrast anime style. "
        "Striking {adj} design that stands out on any product. "
        "Inspired by cyberpunk, techwear and dark anime culture."
    ),
    (
        "Dark and {adj} {niche} artwork for the aesthetics community. "
        "Cyberpunk-meets-anime illustration, professionally printed on demand. "
        "Great for collectors, cosplayers and fans of edgy design."
    ),
]

# Tag building blocks — cyberpunk / anime / dark aesthetic focused
_TAG_POOLS: dict[str, list[str]] = {
    "cyberpunk": [
        "cyberpunk", "techwear", "cyberpunk art", "cyberpunk aesthetic",
        "neon art", "dark futuristic", "dystopian art", "sci fi art",
        "cyber aesthetic", "digital art", "glitch art", "neon poster",
        "cyberpunk poster", "dark anime", "futuristic art",
    ],
    "anime": [
        "anime art", "anime poster", "anime aesthetic", "anime girl",
        "manga art", "anime character", "dark anime", "anime illustration",
        "anime print", "anime sticker", "anime wall art", "anime tshirt",
        "japanese art", "anime fan", "anime lover",
    ],
    "solarpunk": [
        "solarpunk", "solarpunk art", "nature tech", "bioluminescent art",
        "hopeful future", "green aesthetic", "solarpunk aesthetic",
        "eco futurism", "nature anime", "floral tech",
    ],
    "dieselpunk": [
        "dieselpunk", "steampunk art", "industrial art", "retro futurism",
        "art deco design", "mechanical art", "dieselpunk aesthetic",
        "gothic industrial", "brass and steam", "retro sci fi",
    ],
    "vaporwave": [
        "vaporwave", "synthwave", "retrowave", "vaporwave aesthetic",
        "80s aesthetic", "outrun art", "retro neon", "lo fi aesthetic",
        "pastel neon", "nostalgia art", "retro poster",
    ],
    "gothic": [
        "dark aesthetic", "gothic art", "dark fantasy", "gothic anime",
        "dark witch", "occult art", "dark magic", "gothic poster",
        "horror aesthetic", "gothic illustration", "dark academia",
    ],
    "grunge": [
        "grunge art", "bold graphic", "street art", "urban art",
        "high contrast", "black and white art", "drip art", "edgy design",
        "grunge aesthetic", "bold design", "graphic tee", "dark graphic",
    ],
    "general": [
        "original art", "digital illustration", "art print", "wall art",
        "poster art", "t-shirt design", "sticker art", "phone case art",
        "unique design", "indie artist", "dark design", "aesthetic art",
        "fan art", "cool design", "edgy art", "alternative art",
        "birthday gift", "gift idea", "art lover gift", "collector art",
    ],
}

_SEASONAL_TAGS: list[str] = [
    "halloween art", "dark holiday", "gothic christmas",
    "spooky aesthetic", "dark valentines", "dark summer",
]


# ---------------------------------------------------------------------------
# FreeTextGenerator
# ---------------------------------------------------------------------------

class FreeTextGenerator:
    """
    Generates design ideas, image prompts, and SEO metadata
    using curated templates — completely free, no API required.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        """
        Parameters
        ----------
        seed : Optional random seed for reproducibility (useful in testing).
               If None, uses a date-based seed so each day produces fresh content.
        """
        if seed is None:
            # Different content each calendar day
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            seed = int(hashlib.md5(today_str.encode()).hexdigest(), 16) % (2**32)
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # 1. Idea Generation
    # ------------------------------------------------------------------

    def generate_ideas(self, niche: str, count: int = 3) -> list[dict]:
        """
        Generate `count` original design ideas for the given niche.

        Returns
        -------
        list[dict] with keys: title, description, emotion, colors, best_products
        """
        niche_clean = niche.strip().lower()
        ideas: list[dict] = []

        # Sample without replacement from templates to avoid duplicates
        templates = self._rng.sample(
            _IDEA_TEMPLATES,
            min(count * 2, len(_IDEA_TEMPLATES))
        )

        for tpl, emotion, products_hint in templates[:count]:
            adj = self._rng.choice(_ADJECTIVES)
            colors = self._rng.choice(_COLORS)
            products = products_hint.split(",")

            # Fill template
            title = tpl.format(niche=niche_clean, adj=adj).title()
            description = (
                f"{title}. A unique, original illustration perfect for "
                f"{niche_clean} lovers. {self._rng.choice(_ADJECTIVES).title()} "
                f"design featuring {niche_clean} in a fresh and creative style. "
                f"Great for gifts and home decor."
            )

            ideas.append({
                "title":         title,
                "description":   description,
                "emotion":       emotion,
                "colors":        colors,
                "best_products": products,
            })

        return ideas

    # ------------------------------------------------------------------
    # 2. Prompt Building
    # ------------------------------------------------------------------

    def build_prompt(self, idea: dict) -> str:
        """
        Convert a design idea dict into an AI image-generation prompt.

        Returns
        -------
        str – optimised prompt for Leonardo / DALL-E
        """
        title       = idea.get("title", "")
        description = idea.get("description", "")
        colors      = idea.get("colors", [])
        colors_str  = ", ".join(colors) if colors else "vibrant complementary colours"

        style = self._rng.choice(_PROMPT_STYLES)

        prompt = (
            f"{title}. {description} "
            f"Style: {style}. "
            f"Colour palette: {colors_str}. "
            f"{_PROMPT_QUALITY}."
        )

        # Trim to safe length for APIs
        return prompt[:1200]

    def build_negative_prompt(self) -> str:
        """Return standard negative prompt for image generation."""
        return _PROMPT_NEGATIVE

    # ------------------------------------------------------------------
    # 3. SEO Metadata Generation
    # ------------------------------------------------------------------

    def generate_seo(self, idea: dict, niche: str) -> dict:
        """
        Generate SEO-optimised title, description, and 50 tags.

        Returns
        -------
        dict with keys: title (str), description (str), tags (list[str])
        """
        niche_kw = niche.strip().lower()
        idea_title = idea.get("title", niche_kw.title())
        adj_title  = self._rng.choice(_ADJECTIVES).title()

        # --- Title ---
        title_tpl  = self._rng.choice(_TITLE_TEMPLATES)
        raw_title  = title_tpl.format(
            niche_title=niche_kw.title(),
            adj_title=adj_title,
        )
        title = raw_title[:80]

        # --- Description ---
        desc_tpl = self._rng.choice(_DESC_TEMPLATES)
        raw_desc = desc_tpl.format(
            niche=niche_kw,
            adj=self._rng.choice(_ADJECTIVES),
            adj_title=adj_title,
        )
        description = raw_desc[:500]

        # --- Tags (50 total) ---
        tags = self._build_tags(niche_kw, idea_title)

        return {"title": title, "description": description, "tags": tags}

    def _build_tags(self, niche: str, idea_title: str) -> list[str]:
        """Build exactly 50 unique, relevant tags."""
        tags: list[str] = []
        seen: set[str] = set()

        def _add(tag: str) -> None:
            t = re.sub(r"[^a-z0-9 ]", "", tag.strip().lower())
            if t and t not in seen and len(tags) < 50:
                seen.add(t)
                tags.append(t)

        # Core niche tags
        _add(niche)
        niche_words = niche.split()
        for w in niche_words:
            if len(w) > 2:
                _add(w)

        # Niche + keyword combos
        for suffix in [
            "lover", "gift", "art", "design", "print", "fan", "tshirt",
            "sticker", "mug", "poster", "art print", "wall art", "funny",
            "cute", "cool", "gift idea", "lover gift",
        ]:
            _add(f"{niche} {suffix}")

        # Adjective combos
        for adj in self._rng.sample(_ADJECTIVES, 8):
            _add(f"{adj} {niche}")

        # Detect category from niche words and add pool
        categories = self._detect_categories(niche)
        for cat in categories:
            pool = _TAG_POOLS.get(cat, [])
            for tag in pool:
                _add(tag)

        # Always add general tags
        for tag in _TAG_POOLS["general"]:
            _add(tag)

        # Seasonal tags
        for tag in _TAG_POOLS.get("seasonal", _SEASONAL_TAGS):
            _add(tag)

        # Idea-title derived tags
        for word in idea_title.lower().split():
            if len(word) > 3:
                _add(word)

        # Pad to 50 if still short
        extra = [
            "original art", "indie artist", "handmade", "artsy",
            "illustration", "graphic design", "custom design",
            "limited edition", "art lover", "creative gift",
        ]
        for e in extra:
            _add(e)

        return tags[:50]

    @staticmethod
    def _detect_categories(niche: str) -> list[str]:
        """Detect aesthetic category from niche keyword for tag pool selection."""
        cats: list[str] = []
        niche_lower = niche.lower()
        words = set(niche_lower.split())

        cyberpunk_words = {
            "cyberpunk", "techwear", "cyber", "neon", "futuristic",
            "dystopian", "glitch", "android", "mecha", "digital",
            "hacker", "robot", "sci", "tech", "circuit",
        }
        anime_words = {
            "anime", "manga", "kawaii", "chibi", "waifu",
            "otaku", "girl", "warrior", "samurai", "ninja",
        }
        solarpunk_words = {
            "solarpunk", "sunpunk", "bioluminescent", "nature",
            "green", "botanical", "organic", "garden", "forest",
        }
        dieselpunk_words = {
            "dieselpunk", "steampunk", "industrial", "mechanic",
            "gear", "brass", "steam", "clockwork", "retro",
        }
        vaporwave_words = {
            "vaporwave", "synthwave", "retrowave", "outrun",
            "80s", "lofi", "pastel", "nostalgic", "retro",
        }
        gothic_words = {
            "gothic", "dark", "witch", "occult", "horror",
            "shadow", "cursed", "necromancer", "ghost", "doom",
        }
        grunge_words = {
            "grunge", "bold", "street", "urban", "drip",
            "psychedelic", "graphic", "contrast",
        }

        if words & cyberpunk_words:
            cats.append("cyberpunk")
        if words & anime_words:
            cats.append("anime")
        if words & solarpunk_words:
            cats.append("solarpunk")
        if words & dieselpunk_words:
            cats.append("dieselpunk")
        if words & vaporwave_words:
            cats.append("vaporwave")
        if words & gothic_words:
            cats.append("gothic")
        if words & grunge_words:
            cats.append("grunge")

        if not cats:
            cats.append("anime")   # default — all designs have anime influence

        return list(set(cats))


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    gen = FreeTextGenerator()

    print("=" * 60)
    print("NICHE: 'cute axolotl'")
    print("=" * 60)

    ideas = gen.generate_ideas("cute axolotl", count=2)
    for i, idea in enumerate(ideas, 1):
        print(f"\n--- Idea {i} ---")
        print(f"Title:    {idea['title']}")
        print(f"Desc:     {idea['description'][:80]}...")
        print(f"Emotion:  {idea['emotion']}")
        print(f"Colors:   {idea['colors']}")
        print(f"Products: {idea['best_products']}")

        prompt = gen.build_prompt(idea)
        print(f"\nPrompt ({len(prompt)} chars):")
        print(prompt[:200], "...")

        seo = gen.generate_seo(idea, "cute axolotl")
        print(f"\nSEO Title: {seo['title']}")
        print(f"SEO Desc:  {seo['description'][:80]}...")
        print(f"Tags ({len(seo['tags'])}): {seo['tags'][:10]}")
