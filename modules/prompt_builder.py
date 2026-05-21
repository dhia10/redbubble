"""
prompt_builder.py
==================
Converts a design idea dict into an optimised AI image-generation prompt.

The prompt is crafted to produce:
- High-resolution (4500 × 5400 px), print-ready artwork
- Transparent or clean white background
- Vector-style or high-detail digital illustration aesthetic
- No text (unless critical to the concept)
- No copyrighted / trademarked elements
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Generates image-generation prompts from design idea dicts."""

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
                cfg["prompt_building"]["system"],
                cfg["prompt_building"]["user"],
            )
        except Exception as exc:
            logger.warning("Could not load prompts.yaml (%s) — using defaults.", exc)
            system = (
                "You are an expert AI image prompt engineer for print-on-demand artwork. "
                "Create detailed prompts for high-res, transparent-background illustrations."
            )
            user = (
                "Convert this design idea into an AI image generation prompt.\n\n"
                "Title: {title}\n"
                "Description: {description}\n"
                "Emotion: {emotion}\n"
                "Colors: {colors}\n\n"
                "Requirements: transparent/white background, 4500x5400px target, "
                "vector-like digital illustration, no copyrighted content.\n\n"
                "Return ONLY the prompt string, no JSON, no explanation."
            )
            return system, user

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, idea: dict) -> str:
        """
        Build an image-generation prompt from a design idea dict.

        Parameters
        ----------
        idea : dict with keys title, description, emotion, colors, best_products

        Returns
        -------
        str – The image generation prompt
        """
        title       = idea.get("title", "")
        description = idea.get("description", "")
        emotion     = idea.get("emotion", "")
        colors      = idea.get("colors", [])
        colors_str  = ", ".join(colors) if isinstance(colors, list) else str(colors)

        user_msg = self._user_template.format(
            title=title,
            description=description,
            emotion=emotion,
            colors=colors_str,
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=600,
            )
            prompt_text = (resp.choices[0].message.content or "").strip()

            # Strip any accidental markdown code fences
            if prompt_text.startswith("```"):
                lines = prompt_text.split("\n")
                prompt_text = "\n".join(
                    line for line in lines
                    if not line.startswith("```")
                ).strip()

            # Append technical print-spec suffix
            prompt_text += (
                " | Digital illustration, flat design with depth, "
                "transparent background, ultra high resolution, "
                "4500x5400 pixels, print-ready artwork, crisp edges, "
                "no watermarks, no text overlays."
            )

            logger.info("Built prompt for idea '%s' (%d chars)", title, len(prompt_text))
            return prompt_text

        except Exception as exc:
            logger.error("Prompt building failed for idea '%s': %s", title, exc)
            # Return a fallback minimal prompt
            return (
                f"A beautiful digital illustration of {description}. "
                f"Color palette: {colors_str}. Transparent background, "
                f"print-ready, 4500x5400px."
            )

    def build_batch(self, ideas: list[dict]) -> list[str]:
        """Build prompts for a list of ideas."""
        return [self.build(idea) for idea in ideas]


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    builder = PromptBuilder()
    idea = {
        "title": "Galaxy Fox Shaman",
        "description": (
            "A mystical fox with star-filled fur standing under a cosmic aurora, "
            "surrounded by floating crystals and moon phases."
        ),
        "emotion": "Mystery and wonder",
        "colors": ["deep purple", "midnight blue", "gold", "white"],
        "best_products": ["t-shirt", "poster", "sticker"],
    }
    prompt = builder.build(idea)
    print("Generated prompt:\n")
    print(prompt)
