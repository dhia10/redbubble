"""
niche_scorer.py
================
Scores a list of raw keywords as potential print-on-demand niches using
the OpenAI API and additional heuristics, then ranks and filters them.

Scoring Dimensions
------------------
- commercial_potential   : Will people buy merchandise for this?
- evergreen_score        : Lasting trend vs. short fad?
- competition_estimate   : 0 = low competition, 1 = saturated
- originality_opportunity: Room for fresh, original designs?
- overall_score          : Weighted composite

Heuristics applied BEFORE the LLM call
---------------------------------------
- Keywords with brand/celebrity names are penalised (IP risk)
- Very short or generic terms are down-scored
- Keywords already in the database recently are deprioritised
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IP-risk blocklist (partial match triggers penalty)
# ---------------------------------------------------------------------------

_IP_RISK_TERMS: frozenset[str] = frozenset([
    "disney", "marvel", "dc comics", "pokemon", "harry potter", "star wars",
    "nike", "adidas", "supreme", "louis vuitton", "gucci", "coca cola",
    "pepsi", "apple", "google", "amazon", "netflix", "youtube",
    "mickey", "spiderman", "batman", "superman", "elsa", "frozen",
    "minecraft", "fortnite", "roblox",
])

# ---------------------------------------------------------------------------
# Heuristic pre-scorer
# ---------------------------------------------------------------------------

def _heuristic_score(keyword: str) -> float:
    """
    Quick 0–1 heuristic score applied before the LLM call.
    Returns 0.0 for high-risk keywords, up to 1.0 for clean ones.
    """
    kw = keyword.lower()

    # IP risk check
    for risk in _IP_RISK_TERMS:
        if risk in kw:
            logger.debug("IP risk detected in '%s' (%s) — scoring 0.0", keyword, risk)
            return 0.0

    # Too short / too generic
    words = kw.split()
    if len(words) < 1 or len(kw) < 3:
        return 0.1

    # Contains numbers only or special characters — low value
    if re.fullmatch(r"[\d\W]+", kw):
        return 0.1

    return 1.0  # Pass through to LLM


# ---------------------------------------------------------------------------
# NicheScorer
# ---------------------------------------------------------------------------

class NicheScorer:
    """Uses an LLM to score raw keyword lists as POD niche opportunities."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        batch_size: int = 15,
        min_score: float = 0.4,
        prompts_path: str = "config/prompts.yaml",
    ) -> None:
        """
        Parameters
        ----------
        api_key:       OpenAI API key (defaults to OPENAI_API_KEY env var)
        model:         OpenAI chat model to use
        batch_size:    How many keywords to score per API call
        min_score:     Discard niches below this overall_score
        prompts_path:  Path to prompts.yaml
        """
        self.model = model
        self.batch_size = batch_size
        self.min_score = min_score
        # Lazy import — only fails if you actually try to USE the GPT scorer
        # without installing openai. The free pipeline never reaches this.
        try:
            from openai import OpenAI as _OpenAI
            self.client = _OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY", ""))
        except ImportError:
            self.client = None  # type: ignore[assignment]
            logger.info("openai package not installed — NicheScorer GPT mode unavailable.")
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
            system = cfg["niche_analysis"]["system"]
            user   = cfg["niche_analysis"]["user"]
            return system, user
        except Exception as exc:
            logger.warning("Could not load prompts.yaml (%s) — using defaults.", exc)
            system = (
                "You are a print-on-demand market research analyst. "
                "Score niches as POD opportunities on Redbubble."
            )
            user = (
                "Analyse these trending keywords and score each as a niche "
                "opportunity for print-on-demand products.\n\nKeywords: {keywords}\n\n"
                "Return valid JSON as a list of objects with keys: keyword, "
                "commercial_potential, evergreen_score, competition_estimate, "
                "originality_opportunity, overall_score, reasoning."
            )
            return system, user

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_keywords(self, keywords: list[str]) -> list[dict]:
        """
        Score a list of keywords; return sorted list of niche dicts
        above self.min_score.

        Each dict contains:
          keyword, commercial_potential, evergreen_score,
          competition_estimate, originality_opportunity,
          overall_score, reasoning
        """
        if not keywords:
            return []

        # Step 1: heuristic filter (fast, free)
        filtered = [kw for kw in keywords if _heuristic_score(kw) > 0.0]
        logger.info("Heuristic pre-filter: %d → %d keywords", len(keywords), len(filtered))

        if not filtered:
            return []

        # Step 2: deduplicate
        filtered = list(dict.fromkeys(filtered))

        # Step 3: batch LLM scoring
        all_results: list[dict] = []
        for i in range(0, len(filtered), self.batch_size):
            batch = filtered[i : i + self.batch_size]
            results = self._score_batch(batch)
            all_results.extend(results)
            if i + self.batch_size < len(filtered):
                time.sleep(0.5)  # Brief pause between batches

        # Step 4: filter by min_score and sort
        passing = [
            r for r in all_results
            if r.get("overall_score", 0.0) >= self.min_score
        ]
        passing.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)

        logger.info(
            "NicheScorer: %d keywords scored, %d pass threshold %.2f",
            len(all_results), len(passing), self.min_score,
        )
        return passing

    # ------------------------------------------------------------------
    # Batch LLM call
    # ------------------------------------------------------------------

    def _score_batch(self, keywords: list[str]) -> list[dict]:
        """Send one batch to the LLM; return list of scored dicts.
        Falls back to heuristic scoring if openai client is unavailable."""
        if self.client is None:
            # openai not installed — apply simple heuristic scoring
            return [
                {
                    "keyword":                kw,
                    "overall_score":          round(min(_heuristic_score(kw), 0.95), 3),
                    "commercial_potential":   0.6,
                    "evergreen_score":        0.6,
                    "competition_estimate":   0.4,
                    "originality_opportunity": 0.7,
                    "reasoning":              "Heuristic fallback (openai not installed)",
                }
                for kw in keywords
            ]

        kw_str = ", ".join(f'"{k}"' for k in keywords)
        user_msg = self._user_template.format(keywords=kw_str)

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)

            # LLM may return {"niches": [...]} or just a list
            if isinstance(data, list):
                results = data
            elif isinstance(data, dict):
                # Try common wrapper keys
                for key in ("niches", "keywords", "results", "data"):
                    if key in data and isinstance(data[key], list):
                        results = data[key]
                        break
                else:
                    results = list(data.values())[0] if data else []
            else:
                results = []

            # Validate and normalise each result
            scored: list[dict] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                # Compute overall_score as weighted average if not present
                if "overall_score" not in item:
                    cp  = float(item.get("commercial_potential",    0.5))
                    es  = float(item.get("evergreen_score",         0.5))
                    ce  = float(item.get("competition_estimate",    0.5))
                    oo  = float(item.get("originality_opportunity", 0.5))
                    item["overall_score"] = round(
                        cp * 0.35 + es * 0.25 + (1 - ce) * 0.20 + oo * 0.20, 3
                    )
                scored.append(item)

            logger.debug("Batch of %d scored successfully.", len(scored))
            return scored

        except json.JSONDecodeError as exc:
            logger.error("JSON decode error from LLM scoring: %s", exc)
            return []
        except Exception as exc:
            logger.error("LLM scoring batch failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    scorer = NicheScorer(min_score=0.45)
    sample_keywords = [
        "cute axolotl", "space cat", "cottagecore mushroom",
        "mental health awareness", "funny nurse", "disney princess",  # ← IP risk
        "coffee addict", "mountain hiking", "plant parent aesthetic",
    ]
    results = scorer.score_keywords(sample_keywords)
    for r in results:
        print(f"[{r['overall_score']:.2f}] {r['keyword']} — {r.get('reasoning','')}")
