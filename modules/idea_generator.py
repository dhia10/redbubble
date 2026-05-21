"""
idea_generator.py
==================
Generates original design concepts for a given niche using the OpenAI API.

Each idea contains:
- title         : Short, catchy concept name
- description   : Visual description (2–3 sentences)
- emotion       : Core emotion / message
- colors        : Suggested palette (list of color names or hex)
- best_products : Recommended Redbubble product types

Output is validated against a schema before being returned, so downstream
modules receive clean, typed data.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema validation helper
# ---------------------------------------------------------------------------

_REQUIRED_KEYS: tuple[str, ...] = (
    "title", "description", "emotion", "colors", "best_products"
)


def _validate_idea(idea: dict) -> bool:
    """Return True if the idea dict has all required keys with non-empty values."""
    for key in _REQUIRED_KEYS:
        val = idea.get(key)
        if not val:
            return False
        if isinstance(val, list) and len(val) == 0:
            return False
    return True


# ---------------------------------------------------------------------------
# IdeaGenerator
# ---------------------------------------------------------------------------

class IdeaGenerator:
    """Generates design ideas for a given niche using GPT."""

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
                cfg["idea_generation"]["system"],
                cfg["idea_generation"]["user"],
            )
        except Exception as exc:
            logger.warning("Could not load prompts.yaml (%s) — using defaults.", exc)
            system = (
                "You are a creative director for a print-on-demand business. "
                "Generate original, commercially viable design ideas. "
                "Never use trademarked, copyrighted, or celebrity content."
            )
            user = (
                'Generate {count} unique design ideas for the niche: "{niche}".\n\n'
                "For each idea provide: title, description, emotion, colors (list), "
                "best_products (list).\n\n"
                "Return valid JSON as a list of objects."
            )
            return system, user

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, niche: str, count: int = 3) -> list[dict]:
        """
        Generate `count` design ideas for `niche`.

        Returns
        -------
        list[dict]  – Validated idea dicts; may be fewer than `count`
                      if validation fails for some.
        """
        logger.info("Generating %d ideas for niche: '%s'", count, niche)

        user_msg = self._user_template.format(niche=niche, count=count)

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.85,          # Higher creativity for ideas
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or "[]"
            data = json.loads(raw)

            # Unwrap various shapes the LLM might return
            ideas: list[dict]
            if isinstance(data, list):
                ideas = data
            elif isinstance(data, dict):
                for key in ("ideas", "designs", "concepts", "results"):
                    if key in data and isinstance(data[key], list):
                        ideas = data[key]
                        break
                else:
                    # Fallback: use first list value found
                    ideas = next(
                        (v for v in data.values() if isinstance(v, list)), []
                    )
            else:
                ideas = []

            # Validate each idea
            valid_ideas = [idea for idea in ideas if _validate_idea(idea)]

            if len(valid_ideas) < len(ideas):
                logger.warning(
                    "%d ideas failed validation for niche '%s'.",
                    len(ideas) - len(valid_ideas), niche,
                )

            logger.info(
                "Generated %d valid ideas for niche '%s'.", len(valid_ideas), niche
            )
            return valid_ideas

        except json.JSONDecodeError as exc:
            logger.error("JSON decode error in idea generation: %s", exc)
            return []
        except Exception as exc:
            logger.error("Idea generation failed for niche '%s': %s", niche, exc)
            return []


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    gen = IdeaGenerator()
    ideas = gen.generate("space cat astronomy", count=2)
    for i, idea in enumerate(ideas, 1):
        print(f"\n--- Idea {i} ---")
        print(f"Title:       {idea['title']}")
        print(f"Description: {idea['description']}")
        print(f"Emotion:     {idea['emotion']}")
        print(f"Colors:      {idea['colors']}")
        print(f"Products:    {idea['best_products']}")
