"""
trend_detector.py
==================
Collects trending keywords from multiple sources and returns a unified
list of raw keyword strings for the niche scorer to evaluate.

Sources
-------
1. Google Trends   – pytrends (no API key required)
2. Redbubble       – Scrape the /trending page with httpx + BeautifulSoup
3. Pinterest       – Scrape the /ideas search with httpx + BeautifulSoup
4. Fallback seeds  – Curated evergreen niches always included
"""

from __future__ import annotations

import logging
import time
import random
from typing import Optional

import httpx
from bs4 import BeautifulSoup

try:
    from pytrends.request import TrendReq
    PYTRENDS_AVAILABLE = True
except ImportError:
    PYTRENDS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Evergreen seed niches always considered
_EVERGREEN_SEEDS: list[str] = [
    "funny cat", "dog lover", "space astronomy", "hiking outdoors",
    "mental health awareness", "witch aesthetic", "cottagecore",
    "retro 80s", "music lover", "book reader", "plant parent",
    "ocean waves", "mountain life", "nurse doctor", "teacher gift",
    "engineer nerd", "gamer", "pizza lover", "coffee addict",
    "sunflower", "butterfly nature", "dinosaur kids",
]

# Seasonal niches (triggered by month)
_SEASONAL_NICHES: dict[int, list[str]] = {
    1:  ["new year motivation", "winter cozy"],
    2:  ["valentines day love", "galentines"],
    3:  ["st patricks day", "spring garden"],
    4:  ["easter bunny", "earth day"],
    5:  ["mothers day gift", "graduation"],
    6:  ["pride month rainbow", "fathers day", "summer vibes"],
    7:  ["fourth of july", "summer beach"],
    8:  ["back to school", "late summer"],
    9:  ["fall autumn leaves", "harvest"],
    10: ["halloween spooky", "october"],
    11: ["thanksgiving", "fall cozy"],
    12: ["christmas holiday", "winter wonderland"],
}


# ---------------------------------------------------------------------------
# TrendDetector
# ---------------------------------------------------------------------------

class TrendDetector:
    """Aggregates trending keywords from multiple sources."""

    def __init__(
        self,
        sources: Optional[list[str]] = None,
        timeframe: str = "now 7-d",
        geo: str = "US",
        timeout: int = 20,
    ) -> None:
        """
        Parameters
        ----------
        sources:   list from ['google_trends', 'redbubble', 'pinterest']
        timeframe: pytrends timeframe string
        geo:       ISO country code for Google Trends
        timeout:   HTTP request timeout in seconds
        """
        self.sources = sources or ["google_trends", "redbubble", "pinterest"]
        self.timeframe = timeframe
        self.geo = geo
        self.timeout = timeout
        self._client = httpx.Client(headers=_HEADERS, timeout=timeout,
                                    follow_redirects=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all(self) -> list[str]:
        """
        Fetch trending keywords from all configured sources and return
        a deduplicated, normalised list.
        """
        import calendar
        from datetime import datetime

        keywords: list[str] = []

        if "google_trends" in self.sources:
            keywords.extend(self._fetch_google_trends())

        if "redbubble" in self.sources:
            keywords.extend(self._fetch_redbubble_trending())

        if "pinterest" in self.sources:
            keywords.extend(self._fetch_pinterest_trends())

        # Always inject seasonal + evergreen seeds
        month = datetime.utcnow().month
        keywords.extend(_SEASONAL_NICHES.get(month, []))
        keywords.extend(_EVERGREEN_SEEDS)

        # Deduplicate while preserving order, normalise
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            norm = kw.strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                result.append(norm)

        logger.info("TrendDetector collected %d unique keywords.", len(result))
        return result

    # ------------------------------------------------------------------
    # Source implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _patch_urllib3_retry() -> None:
        """
        Monkey-patch urllib3 Retry for pytrends compatibility.
        urllib3 v2.0 renamed `method_whitelist` → `allowed_methods`.
        pytrends hasn't been updated yet; this patch bridges the gap.
        """
        try:
            from urllib3.util.retry import Retry
            _orig_init = Retry.__init__

            def _patched_init(self, *args, **kwargs):
                if "method_whitelist" in kwargs:
                    kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
                _orig_init(self, *args, **kwargs)

            if not getattr(Retry, "_rb_patched", False):
                Retry.__init__ = _patched_init
                Retry._rb_patched = True  # type: ignore[attr-defined]
        except Exception:
            pass

    def _fetch_google_trends(self) -> list[str]:
        """Fetch trending searches via pytrends."""
        if not PYTRENDS_AVAILABLE:
            logger.warning("pytrends not installed — skipping Google Trends.")
            return []

        # Apply urllib3 v2 compatibility patch before constructing TrendReq
        self._patch_urllib3_retry()

        keywords: list[str] = []
        try:
            pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2)

            # Daily trending searches
            trending_df = pt.trending_searches(pn="united_states")
            keywords.extend(trending_df[0].tolist()[:20])

            # Related queries for popular POD seed terms
            seeds = ["t-shirt design", "sticker art", "wall art print"]
            pt.build_payload(seeds[:1], timeframe=self.timeframe, geo=self.geo)
            related = pt.related_queries()
            for seed in seeds[:1]:
                data = related.get(seed, {})
                for kind in ("top", "rising"):
                    df = data.get(kind)
                    if df is not None and not df.empty:
                        keywords.extend(df["query"].tolist()[:10])

            logger.info("Google Trends returned %d keywords.", len(keywords))
        except Exception as exc:
            logger.error("Google Trends fetch failed: %s", exc)
        return keywords

    def _fetch_redbubble_trending(self) -> list[str]:
        """Scrape Redbubble's trending tags / search page."""
        keywords: list[str] = []
        urls = [
            "https://www.redbubble.com/trending/",
            "https://www.redbubble.com/shop/trending",
        ]
        for url in urls:
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                # Tag links typically contain /shop/<keyword>+
                for a in soup.find_all("a", href=True):
                    href: str = a["href"]
                    if "/shop/" in href:
                        segment = href.split("/shop/")[-1].split("?")[0]
                        # Replace URL-encoded + with space
                        kw = segment.replace("+", " ").replace("-", " ").strip()
                        if 2 < len(kw) < 50:
                            keywords.append(kw)

                logger.info("Redbubble trending: %d keywords from %s",
                            len(keywords), url)
                time.sleep(random.uniform(1.5, 3.0))
                break
            except Exception as exc:
                logger.warning("Redbubble fetch %s failed: %s", url, exc)

        return keywords[:40]

    def _fetch_pinterest_trends(self) -> list[str]:
        """Scrape Pinterest trending ideas keywords."""
        keywords: list[str] = []
        url = "https://www.pinterest.com/ideas/"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Pinterest renders idea cards; attempt to extract text from headers
            for tag in soup.find_all(["h1", "h2", "h3", "span"], limit=200):
                text = tag.get_text(strip=True)
                if 3 < len(text) < 60 and text.replace(" ", "").isalpha():
                    keywords.append(text.lower())

            logger.info("Pinterest ideas returned %d keywords.", len(keywords))
            time.sleep(random.uniform(1.0, 2.5))
        except Exception as exc:
            logger.warning("Pinterest trends fetch failed: %s", exc)

        return keywords[:30]

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "TrendDetector":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    with TrendDetector(sources=["google_trends", "redbubble"]) as td:
        kws = td.fetch_all()
        print(f"Total keywords: {len(kws)}")
        for k in kws[:20]:
            print(" •", k)
