"""
seo_generator.py
=================
Generates SEO-optimised titles, descriptions, and tags for Redbubble products.

Redbubble SEO rules applied:
- Title    : ≤ 80 characters, primary keyword near the front
- Description: ≤ 500 characters, keyword-rich, ends with a soft CTA
- Tags      : Exactly 50 tags (Redbubble maximum), mix of broad + long-tail

The module also applies local post-processing heuristics to enforce these
constraints even if the LLM returns values outside the limits.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TITLE_MAX       = 80
DESC_MAX        = 500
TARGET_TAG_COUNT = 50


# ---------------------------------------------------------------------------
# SEO helpers
# ---------------------------------------------------------------------------

def _trim_title(title: str) -> str:
    """Ensure title is within TITLE_MAX characters."""
    title = title.strip()
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX].rsplit(" ", 1)[0]
    return title


def _trim_description(desc: str) -> str:
    """Ensure description is within DESC_MAX characters."""
    desc = desc.strip()
    if len(desc) > DESC_MAX:
        desc = desc[:DESC_MAX].rsplit(" ", 1)[0] + "…"
    return desc


def _clean_tags(tags: list[str], title: str, niche: str) -> list[str]:
    """
    Normalise, deduplicate, and pad/trim to exactly TARGET_TAG_COUNT tags.
    """
    # Normalise
    cleaned = [re.sub(r"[^a-z0-9 ]", "", t.strip().lower()) for t in tags]
    cleaned = [t for t in cleaned if len(t) > 1]

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for tag in cleaned:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)

    # Pad with niche-derived tags if we're short
    if len(unique) < TARGET_TAG_COUNT:
        words = (niche + " " + title).lower().split()
        combos = (
            [w for w in words if len(w) > 2]
            + [f"{a} {b}" for a, b in zip(words, words[1:])]
        )
        for combo in combos:
            c = re.sub(r"[^a-z0-9 ]", "", combo.strip())
            if c and c not in seen:
                seen.add(c)
                unique.append(c)
            if len(unique) >= TARGET_TAG_COUNT:
                break

    return unique[:TARGET_TAG_COUNT]


# ---------------------------------------------------------------------------
# SEOGenerator
# ---------------------------------------------------------------------------

class SEOGenerator:
    """Generates titles, descriptions, and tags via GPT."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        prompts_path: str = "config/prompts.yaml",
    ) -> None:
        self.model = model
        self.client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self._system_prompt, self._user_template = self._load_prompts(prompts_path)

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_prompts(path: str) -> tuple[str, str]:
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return (
                cfg["seo_generation"]["system"],
                cfg["seo_generation"]["user"],
            )
        except Exception as exc:
            logger.warning("Could not load prompts.yaml (%s) — using defaults.", exc)
            system = (
                "You are an expert Redbubble SEO specialist. "
                "Write compelling, keyword-rich product listings."
            )
            user = (
                "Generate SEO metadata for a Redbubble product.\n\n"
                "Design: {title}\nDescription: {description}\n"
                "Niche: {niche}\nTheme: {emotion}\n\n"
                "Return JSON with keys: title (≤80 chars), "
                "description (≤500 chars), tags (list of 50 strings)."
            )
            return system, user

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, idea: dict, niche: str) -> dict:
        """
        Generate SEO metadata for one design idea.

        Parameters
        ----------
        idea  : dict with title, description, emotion, colors, best_products
        niche : The parent niche keyword (e.g. 'space cat')

        Returns
        -------
        dict with keys: title, description, tags (list[str])
        """
        design_title = idea.get("title", "")
        design_desc  = idea.get("description", "")
        emotion      = idea.get("emotion", "")

        user_msg = self._user_template.format(
            title=design_title,
            description=design_desc,
            niche=niche,
            emotion=emotion,
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.5,
                max_tokens=1200,
            )
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)

            title = _trim_title(data.get("title", design_title))
            desc  = _trim_description(data.get("description", design_desc))
            tags  = _clean_tags(
                data.get("tags", []),
                title=title,
                niche=niche,
            )

            result = {"title": title, "description": desc, "tags": tags}
            logger.info(
                "SEO generated for '%s': %d tags, title length %d",
                design_title, len(tags), len(title),
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error("SEO JSON decode error: %s", exc)
        except Exception as exc:
            logger.error("SEO generation failed: %s", exc)

        # Fallback — minimal SEO
        fallback_title = _trim_title(f"{design_title} | {niche} Print")
        return {
            "title": fallback_title,
            "description": _trim_description(design_desc),
            "tags": _clean_tags(
                niche.split() + design_title.split(),
                title=fallback_title,
                niche=niche,
            ),
        }


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    seo = SEOGenerator()
    idea = {
        "title": "Galaxy Fox Shaman",
        "description": (
            "A mystical fox with star-filled fur standing under a cosmic aurora, "
            "surrounded by floating crystals and moon phases."
        ),
        "emotion": "Mystery and cosmic wonder",
        "colors": ["deep purple", "midnight blue", "gold"],
        "best_products": ["t-shirt", "poster", "sticker"],
    }
    result = seo.generate(idea, niche="cosmic fox mystical")
    print("\nTitle:", result["title"])
    print("Desc: ", result["description"])
    print(f"Tags ({len(result['tags'])}):", result["tags"][:10], "...")
