"""
analytics.py
=============
Tracks product performance metrics and surfaces winning niches.

What it does
------------
1. Scrapes the Redbubble portfolio page for views / favourites data
   (Redbubble does not have a public analytics API, so this uses
   lightweight Playwright scraping of the seller dashboard).
2. Stores snapshots in the analytics table.
3. Computes niche performance rankings.
4. Produces a Pandas summary DataFrame for the dashboard.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class Analytics:
    """Collects, stores, and analyses product performance data."""

    PORTFOLIO_URL = "https://www.redbubble.com/portfolio/"

    def __init__(
        self,
        db_manager,          # DatabaseManager instance
        email: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        db_manager : DatabaseManager – shared DB instance
        email      : Redbubble login email (env fallback)
        password   : Redbubble login password (env fallback)
        headless   : Run analytics scrape in headless browser
        """
        self.db       = db_manager
        self.email    = email    or os.environ.get("REDBUBBLE_EMAIL", "")
        self.password = password or os.environ.get("REDBUBBLE_PASSWORD", "")
        self.headless = headless

    # ------------------------------------------------------------------
    # Main analytics collection
    # ------------------------------------------------------------------

    def collect(self) -> int:
        """
        Scrape analytics for all published products and write snapshots
        to the DB. Returns number of records updated.
        """
        products = self.db.get_published_products(limit=500)
        if not products:
            logger.info("No published products to track.")
            return 0

        logger.info("Collecting analytics for %d products…", len(products))
        updated = 0

        # Attempt to scrape Redbubble dashboard
        work_stats = self._scrape_portfolio_stats()

        for product in products:
            work_id    = product.get("redbubble_work_id", "")
            stats      = work_stats.get(work_id, {})
            views      = stats.get("views", 0)
            favourites = stats.get("favourites", 0)
            sales      = stats.get("sales", 0)
            revenue    = stats.get("revenue_usd", 0.0)

            self.db.insert_analytics(
                product_id=product["id"],
                views=views,
                favorites=favourites,
                sales=sales,
                revenue=revenue,
            )
            updated += 1

        logger.info("Analytics collection complete: %d records written.", updated)
        return updated

    # ------------------------------------------------------------------
    # Portfolio scraping
    # ------------------------------------------------------------------

    def _scrape_portfolio_stats(self) -> dict[str, dict]:
        """
        Attempt to scrape views/favourites from Redbubble portfolio.
        Returns dict keyed by work_id with stats dicts.
        Falls back to empty dict if scraping fails.
        """
        if not self.email or not self.password:
            logger.warning("No credentials — analytics scraping skipped.")
            return {}

        stats: dict[str, dict] = {}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                page    = context.new_page()
                page.set_default_timeout(45_000)

                # Login
                page.goto("https://www.redbubble.com/auth/login",
                          wait_until="domcontentloaded")
                page.fill('input[type="email"]', self.email)
                page.fill('input[type="password"]', self.password)
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")
                time.sleep(2)

                # Navigate to portfolio
                page.goto(self.PORTFOLIO_URL + "works",
                          wait_until="domcontentloaded")
                time.sleep(2)

                # Parse work cards
                works = page.query_selector_all("[data-work-id], .work-card, .portfolio-item")
                for work in works:
                    try:
                        work_id = work.get_attribute("data-work-id") or ""
                        views   = self._extract_number(
                            work.query_selector(".views, [class*='view']")
                        )
                        favs    = self._extract_number(
                            work.query_selector(".favorites, [class*='favorite']")
                        )
                        stats[work_id] = {
                            "views": views,
                            "favourites": favs,
                            "sales": 0,
                            "revenue_usd": 0.0,
                        }
                    except Exception:
                        pass

                browser.close()
                logger.info("Scraped stats for %d works.", len(stats))

        except Exception as exc:
            logger.warning("Portfolio scraping failed: %s — using zero values.", exc)

        return stats

    @staticmethod
    def _extract_number(elem) -> int:
        """Extract integer from an element's text content."""
        if elem is None:
            return 0
        try:
            text = elem.inner_text().strip().replace(",", "").split()[0]
            return int(text)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def niche_performance_df(self) -> pd.DataFrame:
        """
        Return a DataFrame of niche performance sorted by revenue / views.
        Columns: keyword, product_count, total_views, total_favorites,
                 total_sales, total_revenue
        """
        rows = self.db.get_top_performing_niches(limit=50)
        if not rows:
            return pd.DataFrame(columns=[
                "keyword", "product_count", "total_views",
                "total_favorites", "total_sales", "total_revenue",
            ])
        df = pd.DataFrame(rows)
        df = df.fillna(0)
        df = df.sort_values(
            ["total_revenue", "total_sales", "total_views"],
            ascending=False,
        ).reset_index(drop=True)
        return df

    def products_summary_df(self) -> pd.DataFrame:
        """
        Return recent published products with their latest analytics snapshot.
        """
        products = self.db.get_published_products(limit=200)
        if not products:
            return pd.DataFrame()
        df = pd.DataFrame(products)
        cols = [c for c in [
            "id", "niche_keyword", "seo_title", "status",
            "published_at", "redbubble_url",
        ] if c in df.columns]
        return df[cols]

    def overall_stats(self) -> dict:
        """Return high-level KPIs for the dashboard."""
        summary = self.db.get_products_summary()
        niche_df = self.niche_performance_df()
        top_niche = (
            niche_df.iloc[0]["keyword"] if not niche_df.empty else "N/A"
        )
        total_revenue = (
            float(niche_df["total_revenue"].sum()) if not niche_df.empty else 0.0
        )
        return {
            **summary,
            "top_niche": top_niche,
            "total_estimated_revenue_usd": round(total_revenue, 2),
        }


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from modules.database_manager import DatabaseManager
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    db       = DatabaseManager()
    reporter = Analytics(db_manager=db)
    stats    = reporter.overall_stats()
    print("\n=== Overall Stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    df = reporter.niche_performance_df()
    print("\n=== Top Niches ===")
    print(df.to_string(index=False) if not df.empty else "No data yet.")
