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

# Evergreen seed niches — matched to the brand's Pinterest aesthetic:
# high-contrast anime / cyberpunk / techwear / solarpunk / dieselpunk.
# These convert on Redbubble as posters, t-shirts, stickers and phone cases.
_EVERGREEN_SEEDS: list[str] = [
    # ── Cyberpunk / Techwear (biggest board — NEON 1.1k pins) ────────────
    "cyberpunk anime girl techwear",
    "high contrast cyberpunk neon art",
    "dark futuristic anime warrior",
    "neon city cyberpunk street art",
    "techwear aesthetic dark anime",
    "cyberpunk dystopian poster art",
    "glitch art digital girl",
    "cyber aesthetic neon anime",
    "anime girl high contrast black",
    "futuristic sci fi anime art",
    "cyberpunk hacker girl neon",
    "dark cyberpunk cityscape anime",
    "neon samurai cyberpunk art",
    "android girl cyberpunk art",
    "mecha pilot anime techwear",

    # ── Solarpunk (JUNO board) ────────────────────────────────────────────
    "solarpunk anime girl nature",
    "bioluminescent fantasy anime art",
    "nature tech fusion solarpunk",
    "solarpunk hopeful future girl",
    "green energy anime aesthetic",
    "solarpunk witch garden glowing",
    "sunpunk anime floral tech",

    # ── Dieselpunk (KAI board) ────────────────────────────────────────────
    "dieselpunk anime mechanic girl",
    "industrial gothic anime art",
    "steampunk anime dark aesthetic",
    "dieselpunk warrior girl art",
    "retro futurism anime poster",
    "dark steampunk gear girl",

    # ── Bold Graphic / Grunge ─────────────────────────────────────────────
    "bold graphic grunge design art",
    "high contrast black white drip",
    "street art urban gothic print",
    "psychedelic grunge anime print",
    "dark drip art horror graphic",
    "grunge skull anime bold",

    # ── Cyberpop / Harajuku ───────────────────────────────────────────────
    "cyberpop colorful anime girl",
    "harajuku neon fashion art",
    "pop art anime bold color",
    "decora kawaii cyber aesthetic",
    "y2k cyber anime girl colorful",

    # ── Vaporwave / Synthwave ─────────────────────────────────────────────
    "vaporwave retro aesthetic art",
    "synthwave neon sunset poster",
    "retrowave anime girl 80s neon",
    "lo fi aesthetic dark anime",
    "outrun aesthetic neon grid art",

    # ── Dystopian / Post-Apocalyptic ──────────────────────────────────────
    "dystopian anime poster art",
    "post apocalyptic warrior girl",
    "cyberpunk wasteland dark art",
    "dark future city anime poster",

    # ── Dark / Gothic Anime ───────────────────────────────────────────────
    "dark academia gothic anime art",
    "gothic lolita dark aesthetic",
    "dark fantasy anime girl sword",
    "witch necromancer anime art",
    "dark magical girl transformation",

    # ── Anime Character / Portrait ────────────────────────────────────────
    "anime portrait high contrast",
    "manga art bold lines print",
    "anime girl sword warrior art",
    "anime character design poster",
    "dark anime girl eyes glow",
]

# Seasonal niches (triggered by month)
_SEASONAL_NICHES: dict[int, list[str]] = {
    1:  ["dark winter anime aesthetic", "new year cyberpunk countdown"],
    2:  ["dark romance anime valentines", "gothic love aesthetic"],
    3:  ["spring solarpunk bloom", "neon cherry blossom anime"],
    4:  ["cyberpunk earth day nature tech", "solarpunk spring anime"],
    5:  ["graduation anime warrior", "dark academia graduation"],
    6:  ["pride neon anime art", "summer neon cyberpunk beach",
         "pride rainbow cyberpunk", "graduation cyberpunk poster"],
    7:  ["summer vaporwave neon", "retro synthwave summer art"],
    8:  ["dark back to school anime", "cyberpunk autumn aesthetic"],
    9:  ["dark autumn gothic anime", "fall cyberpunk neon leaves"],
    10: ["halloween dark anime art", "gothic horror cyberpunk",
         "dark witch anime halloween", "horror aesthetic anime"],
    11: ["dark cozy anime fall", "gothic thanksgiving aesthetic"],
    12: ["cyberpunk christmas neon", "dark holiday anime art",
         "gothic winter solstice anime"],
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
        _shuffled_ev = list(_EVERGREEN_SEEDS)
        import random as _rand_ev; _rand_ev.shuffle(_shuffled_ev)
        keywords.extend(_shuffled_ev)

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
