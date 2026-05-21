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
    # Animals & Pets
    ("{adj} {niche} with galaxy fur floating in space",
     "Cosmic wonder", "t-shirt,poster,sticker"),
    ("{adj} {niche} wearing a tiny wizard hat in a magical forest",
     "Whimsy and magic", "sticker,mug,t-shirt"),
    ("Watercolour portrait of a {niche} surrounded by wildflowers",
     "Soft beauty", "poster,mug,notebook"),
    ("{adj} {niche} reading a book under a mushroom",
     "Cosy charm", "sticker,t-shirt,mug"),
    ("Geometric low-poly {niche} on a mountain landscape",
     "Modern minimalism", "poster,t-shirt,phone-case"),
    ("{niche} astronaut floating among stars and planets",
     "Adventure and wonder", "t-shirt,hoodie,poster"),
    ("Vintage retro {niche} in 80s neon colours",
     "Nostalgic fun", "t-shirt,sticker,phone-case"),
    ("{adj} {niche} surrounded by tropical leaves and flowers",
     "Tropical joy", "tote-bag,poster,mug"),
    ("Kawaii chibi {niche} with big sparkly eyes",
     "Cute and sweet", "sticker,t-shirt,mug"),
    ("Steampunk {niche} with brass gears and goggles",
     "Industrial fantasy", "t-shirt,poster,sticker"),
    # Nature / Places
    ("Minimalist line art {niche} on a white background",
     "Clean elegance", "poster,t-shirt,notebook"),
    ("{adj} {niche} scene at golden hour sunset",
     "Peaceful warmth", "poster,mug,phone-case"),
    ("Abstract watercolour splash forming a {niche} silhouette",
     "Artistic freedom", "poster,t-shirt,notebook"),
    ("Botanical illustration of {niche} with latin labels",
     "Scientific beauty", "poster,notebook,tote-bag"),
    ("Night sky {niche} with aurora borealis background",
     "Majestic nature", "poster,t-shirt,phone-case"),
    # Hobbies / Lifestyle
    ("Retro badge / emblem celebrating {niche} culture",
     "Community pride", "t-shirt,sticker,mug"),
    ("Funny {niche} pun with cartoon illustration",
     "Humour and joy", "t-shirt,sticker,mug"),
    ("{adj} {niche} mandala pattern",
     "Meditative harmony", "poster,t-shirt,phone-case"),
    ("Hand-lettered motivational quote about {niche}",
     "Inspiration", "poster,mug,notebook"),
    ("Flat-design icons pack celebrating {niche} lifestyle",
     "Modern fun", "sticker,t-shirt,tote-bag"),
    # Seasons / Occasions
    ("Cosy autumn {niche} scene with falling leaves and candles",
     "Warm nostalgia", "mug,poster,t-shirt"),
    ("Winter {niche} with snowflakes and fairy lights",
     "Festive magic", "mug,poster,sticker"),
    ("Spring {niche} with cherry blossoms and soft pastels",
     "Fresh renewal", "poster,t-shirt,notebook"),
    ("Summer {niche} with sun, waves and tropical vibes",
     "Carefree energy", "t-shirt,tote-bag,phone-case"),
    # Pop-culture safe / retro
    ("Pixel art 8-bit style {niche} character",
     "Retro gaming fun", "t-shirt,sticker,poster"),
    ("Art Deco {niche} geometric illustration in gold and black",
     "Timeless luxury", "poster,notebook,phone-case"),
    ("Psychedelic 70s {niche} poster with groovy typography",
     "Retro freedom", "poster,t-shirt,sticker"),
]

_ADJECTIVES: list[str] = [
    "magical", "mystical", "adorable", "majestic", "whimsical",
    "celestial", "serene", "vibrant", "enchanted", "dreamy",
    "cosmic", "ancient", "tiny", "fierce", "fluffy",
    "glowing", "ethereal", "radiant", "mysterious", "playful",
]

_COLORS: list[list[str]] = [
    ["deep purple", "midnight blue", "gold", "white"],
    ["coral pink", "sky blue", "cream", "mint green"],
    ["forest green", "earth brown", "warm beige", "terracotta"],
    ["electric blue", "hot pink", "neon yellow", "black"],
    ["dusty rose", "sage green", "lavender", "ivory"],
    ["burnt orange", "teal", "sand", "deep burgundy"],
    ["navy blue", "silver", "light grey", "white"],
    ["sunshine yellow", "cobalt blue", "red", "white"],
    ["soft peach", "lilac", "mint", "light gold"],
    ["charcoal", "forest green", "cream", "rust orange"],
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
    "flat vector illustration, clean lines, bold colours, digital art",
    "watercolour illustration, soft brush strokes, dreamy atmosphere",
    "kawaii chibi style, cute rounded shapes, pastel palette",
    "vintage retro poster art, halftone texture, limited colour palette",
    "geometric low-poly art, sharp facets, modern minimalism",
    "detailed pen-and-ink illustration with watercolour wash",
    "sticker art style, thick black outline, vibrant colours, white background",
    "art nouveau illustration, ornate borders, flowing organic lines",
    "pixel art, 8-bit retro game style, clean pixel grid",
    "hand-drawn sketch style, pencil texture, charming imperfections",
    "bold graphic design, strong contrast, silhouette art",
    "oil painting style, rich texture, impressionist colour mixing",
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
    "{niche_title} Gift | {adj_title} {niche_title} Design",
    "{adj_title} {niche_title} Art Print | {niche_title} Lover Gift",
    "{niche_title} Sticker | Funny {niche_title} Fan",
    "{niche_title} Poster | {adj_title} {niche_title} Wall Art",
    "Cute {niche_title} | {adj_title} {niche_title} T-Shirt Design",
    "{niche_title} Fan Gift | {adj_title} {niche_title} Illustration",
    "{adj_title} {niche_title} Mug Design | {niche_title} Lover",
    "Funny {niche_title} | {niche_title} Humor Print",
    "{niche_title} Art | Minimalist {niche_title} Design",
    "{adj_title} {niche_title} | {niche_title} Themed Gift",
]

_DESC_TEMPLATES: list[str] = [
    (
        "Perfect for {niche} lovers! This {adj} design makes a great gift for "
        "anyone obsessed with {niche}. Available on t-shirts, stickers, mugs, "
        "posters and more. Original artwork, professionally printed."
    ),
    (
        "Show off your love for {niche} with this {adj} design! "
        "Great as a gift for birthdays, holidays, or just because. "
        "Printed on demand on premium products."
    ),
    (
        "This {adj} {niche} design is perfect for fans and enthusiasts alike. "
        "Whether you're buying for yourself or as a gift, this unique artwork "
        "stands out on any product."
    ),
    (
        "A {adj} and original {niche} illustration for true fans. "
        "Makes a fantastic gift — available as a t-shirt, mug, sticker, "
        "phone case and many more. Designed with love."
    ),
    (
        "Unique {niche} art for those who appreciate {adj} design. "
        "Ships worldwide on quality merchandise. Perfect gift for "
        "{niche} enthusiasts of all ages."
    ),
]

# Tag building blocks per theme category
_TAG_POOLS: dict[str, list[str]] = {
    "animal": [
        "animal lover", "pet gift", "wildlife art", "nature lover",
        "cute animal", "animal print", "pet owner gift", "animal illustration",
        "funny animal", "adorable pet", "animal art", "animal tshirt",
    ],
    "space": [
        "space lover", "astronomy gift", "galaxy art", "cosmic design",
        "space art", "astronaut", "planet art", "nebula", "star gazer",
        "space exploration", "universe", "sci fi art",
    ],
    "nature": [
        "nature lover", "botanical art", "plant lover", "outdoor gift",
        "hiking gift", "mountain art", "forest art", "flower art",
        "garden lover", "eco friendly", "nature print", "wildlife",
    ],
    "food": [
        "food lover", "foodie gift", "kitchen art", "chef gift",
        "cooking lover", "baking gift", "restaurant", "gourmet",
        "food illustration", "yummy", "delicious", "food print",
    ],
    "hobby": [
        "hobby gift", "hobbyist", "passion print", "enthusiast gift",
        "fan art", "collector", "niche gift", "unique gift",
        "special interest", "funny hobby", "hobby mug", "hobby tshirt",
    ],
    "general": [
        "gift idea", "unique gift", "funny gift", "cool design",
        "original art", "hand drawn", "illustrated", "graphic tee",
        "art print", "wall art", "home decor", "novelty gift",
        "birthday gift", "christmas gift", "graduation gift",
        "stocking stuffer", "aesthetic", "trendy design", "modern art",
        "minimalist", "retro design", "vintage style", "cute design",
        "kawaii", "funny", "humorous", "witty", "clever design",
    ],
}

_SEASONAL_TAGS: list[str] = [
    "christmas gift", "holiday gift", "birthday gift", "anniversary gift",
    "valentines day", "mothers day", "fathers day", "graduation gift",
    "new year", "halloween", "thanksgiving", "easter",
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
        """Detect broad category from niche keyword for tag pool selection."""
        cats: list[str] = []
        niche_lower = niche.lower()

        animal_words = {
            "cat", "dog", "fox", "wolf", "bear", "rabbit", "bunny",
            "bird", "owl", "parrot", "fish", "turtle", "frog", "axolotl",
            "dragon", "unicorn", "horse", "cow", "pig", "deer", "lion",
            "tiger", "elephant", "penguin", "panda", "koala", "sloth",
        }
        space_words = {
            "space", "galaxy", "cosmos", "star", "planet", "moon",
            "astronaut", "nebula", "universe", "celestial", "astronomy",
        }
        nature_words = {
            "flower", "plant", "tree", "forest", "mountain", "ocean",
            "beach", "garden", "botanical", "nature", "leaf", "mushroom",
            "hiking", "camping", "outdoor", "wildlife",
        }
        food_words = {
            "coffee", "pizza", "food", "cake", "sushi", "burger",
            "cooking", "baking", "chef", "kitchen", "tea", "wine",
        }

        for w in niche_lower.split():
            if w in animal_words:
                cats.append("animal")
            if w in space_words:
                cats.append("space")
            if w in nature_words:
                cats.append("nature")
            if w in food_words:
                cats.append("food")

        if not cats:
            cats.append("hobby")

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
