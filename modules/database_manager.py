"""
database_manager.py
====================
Centralised SQLite database layer for the Redbubble Automation System.

Responsibilities:
- Create and migrate all tables on first run.
- Provide typed CRUD methods for every entity.
- Expose lightweight query helpers used by other modules.

Tables
------
niches          – Trending topics / keyword niches
ideas           – Design concepts generated per niche
prompts         – AI image-generation prompts per idea
designs         – Generated artwork files
products        – Redbubble product records
upload_logs     – Per-upload attempt audit trail
analytics       – Periodic performance snapshots per product
"""

from __future__ import annotations

import sqlite3
import logging
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

_CREATE_NICHES = """
CREATE TABLE IF NOT EXISTS niches (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword                 TEXT NOT NULL UNIQUE,
    source                  TEXT NOT NULL,               -- google_trends | redbubble | pinterest | etc.
    commercial_potential    REAL DEFAULT 0.0,
    evergreen_score         REAL DEFAULT 0.0,
    competition_estimate    REAL DEFAULT 0.0,
    originality_opportunity REAL DEFAULT 0.0,
    overall_score           REAL DEFAULT 0.0,
    reasoning               TEXT,
    times_used              INTEGER DEFAULT 0,
    last_used_at            TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_IDEAS = """
CREATE TABLE IF NOT EXISTS ideas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id        INTEGER NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    emotion         TEXT,
    colors          TEXT,                               -- JSON list
    best_products   TEXT,                               -- JSON list
    status          TEXT DEFAULT 'pending',             -- pending | prompted | generated | uploaded | failed
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_PROMPTS = """
CREATE TABLE IF NOT EXISTS prompts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id         INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    prompt_text     TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'openai',     -- openai | leonardo | ideogram
    model           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_DESIGNS = """
CREATE TABLE IF NOT EXISTS designs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id       INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    idea_id         INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    file_size_bytes INTEGER,
    width_px        INTEGER,
    height_px       INTEGER,
    format          TEXT DEFAULT 'PNG',
    is_valid        INTEGER DEFAULT 1,                  -- 0 = failed validation
    validation_msg  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_PRODUCTS = """
CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id           INTEGER NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
    idea_id             INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    niche_id            INTEGER NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    seo_title           TEXT,
    seo_description     TEXT,
    seo_tags            TEXT,                           -- JSON list
    redbubble_url       TEXT,
    redbubble_work_id   TEXT UNIQUE,
    status              TEXT DEFAULT 'draft',           -- draft | published | failed | removed
    published_at        TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_UPLOAD_LOGS = """
CREATE TABLE IF NOT EXISTS upload_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    attempt_number  INTEGER DEFAULT 1,
    status          TEXT NOT NULL,                      -- success | failed | retrying
    message         TEXT,
    screenshot_path TEXT,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_ANALYTICS = """
CREATE TABLE IF NOT EXISTS analytics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    views           INTEGER DEFAULT 0,
    favorites       INTEGER DEFAULT 0,
    sales           INTEGER DEFAULT 0,
    revenue_usd     REAL DEFAULT 0.0,
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_ALL_DDL = [
    _CREATE_NICHES,
    _CREATE_IDEAS,
    _CREATE_PROMPTS,
    _CREATE_DESIGNS,
    _CREATE_PRODUCTS,
    _CREATE_UPLOAD_LOGS,
    _CREATE_ANALYTICS,
]

# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """Thread-safe SQLite manager for the automation system."""

    def __init__(self, db_path: str = "data/database.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("DatabaseManager initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a row-factory connection, auto-commit or rollback."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create all tables if they do not exist."""
        with self._conn() as conn:
            for ddl in _ALL_DDL:
                conn.execute(ddl)
        logger.debug("All tables verified/created.")

    # ------------------------------------------------------------------
    # Niche CRUD
    # ------------------------------------------------------------------

    def upsert_niche(self, keyword: str, source: str, scores: dict[str, Any]) -> int:
        """Insert or update a niche record; return its id."""
        now = datetime.utcnow().isoformat()
        sql = """
        INSERT INTO niches
            (keyword, source, commercial_potential, evergreen_score,
             competition_estimate, originality_opportunity, overall_score,
             reasoning, created_at, updated_at)
        VALUES
            (:keyword, :source, :commercial_potential, :evergreen_score,
             :competition_estimate, :originality_opportunity, :overall_score,
             :reasoning, :now, :now)
        ON CONFLICT(keyword) DO UPDATE SET
            overall_score           = excluded.overall_score,
            commercial_potential    = excluded.commercial_potential,
            evergreen_score         = excluded.evergreen_score,
            competition_estimate    = excluded.competition_estimate,
            originality_opportunity = excluded.originality_opportunity,
            reasoning               = excluded.reasoning,
            updated_at              = excluded.updated_at
        """
        with self._conn() as conn:
            conn.execute(sql, {
                "keyword": keyword,
                "source": source,
                "commercial_potential": scores.get("commercial_potential", 0.0),
                "evergreen_score": scores.get("evergreen_score", 0.0),
                "competition_estimate": scores.get("competition_estimate", 0.0),
                "originality_opportunity": scores.get("originality_opportunity", 0.0),
                "overall_score": scores.get("overall_score", 0.0),
                "reasoning": scores.get("reasoning", ""),
                "now": now,
            })
            row = conn.execute(
                "SELECT id FROM niches WHERE keyword = ?", (keyword,)
            ).fetchone()
            return row["id"]

    def get_top_niches(self, limit: int = 10) -> list[dict]:
        """Return top niches by overall_score."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM niches
                   ORDER BY overall_score DESC, times_used ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def increment_niche_usage(self, niche_id: int) -> None:
        """Mark a niche as used, recording timestamp."""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE niches SET times_used = times_used + 1,
                   last_used_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, niche_id),
            )

    # ------------------------------------------------------------------
    # Idea CRUD
    # ------------------------------------------------------------------

    def insert_idea(self, niche_id: int, data: dict[str, Any]) -> int:
        """Insert a design idea; return its id."""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO ideas
                   (niche_id, title, description, emotion, colors, best_products,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    niche_id,
                    data.get("title", ""),
                    data.get("description", ""),
                    data.get("emotion", ""),
                    json.dumps(data.get("colors", [])),
                    json.dumps(data.get("best_products", [])),
                    now, now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def update_idea_status(self, idea_id: int, status: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE ideas SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, idea_id),
            )

    def get_ideas_by_niche(self, niche_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ideas WHERE niche_id = ? ORDER BY created_at DESC",
                (niche_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Prompt CRUD
    # ------------------------------------------------------------------

    def insert_prompt(self, idea_id: int, prompt_text: str,
                      provider: str = "openai", model: str = "") -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO prompts (idea_id, prompt_text, provider, model, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (idea_id, prompt_text, provider, model, now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Design CRUD
    # ------------------------------------------------------------------

    def insert_design(self, prompt_id: int, idea_id: int,
                      file_path: str, meta: dict[str, Any]) -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO designs
                   (prompt_id, idea_id, file_path, file_size_bytes, width_px,
                    height_px, format, is_valid, validation_msg, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prompt_id, idea_id, file_path,
                    meta.get("file_size_bytes", 0),
                    meta.get("width_px", 0),
                    meta.get("height_px", 0),
                    meta.get("format", "PNG"),
                    int(meta.get("is_valid", True)),
                    meta.get("validation_msg", ""),
                    now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Product CRUD
    # ------------------------------------------------------------------

    def insert_product(self, design_id: int, idea_id: int,
                       niche_id: int, seo: dict[str, Any]) -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO products
                   (design_id, idea_id, niche_id, seo_title, seo_description,
                    seo_tags, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    design_id, idea_id, niche_id,
                    seo.get("title", ""),
                    seo.get("description", ""),
                    json.dumps(seo.get("tags", [])),
                    now, now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def update_product_published(self, product_id: int,
                                  url: str, work_id: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE products SET status='published', redbubble_url=?,
                   redbubble_work_id=?, published_at=?, updated_at=?
                   WHERE id=?""",
                (url, work_id, now, now, product_id),
            )

    def update_product_failed(self, product_id: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE products SET status='failed', updated_at=? WHERE id=?",
                (now, product_id),
            )

    def get_published_products(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT p.*, n.keyword as niche_keyword
                   FROM products p
                   JOIN niches n ON p.niche_id = n.id
                   WHERE p.status = 'published'
                   ORDER BY p.published_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_products_summary(self) -> dict[str, int]:
        """Return high-level counts for dashboard."""
        with self._conn() as conn:
            total_designs = conn.execute(
                "SELECT COUNT(*) FROM designs WHERE is_valid = 1"
            ).fetchone()[0]
            total_published = conn.execute(
                "SELECT COUNT(*) FROM products WHERE status = 'published'"
            ).fetchone()[0]
            total_failed = conn.execute(
                "SELECT COUNT(*) FROM products WHERE status = 'failed'"
            ).fetchone()[0]
            total_products = conn.execute(
                "SELECT COUNT(*) FROM products"
            ).fetchone()[0]
        return {
            "total_designs": total_designs,
            "total_published": total_published,
            "total_failed": total_failed,
            "total_products": total_products,
            "upload_success_rate": round(
                total_published / total_products * 100
                if total_products else 0.0, 1
            ),
        }

    # ------------------------------------------------------------------
    # Upload Logs
    # ------------------------------------------------------------------

    def log_upload(self, product_id: Optional[int], attempt: int,
                   status: str, message: str = "",
                   screenshot: str = "", duration_ms: int = 0) -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO upload_logs
                   (product_id, attempt_number, status, message,
                    screenshot_path, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (product_id, attempt, status, message, screenshot,
                 duration_ms, now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_recent_logs(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT ul.*, p.seo_title as product_title
                   FROM upload_logs ul
                   LEFT JOIN products p ON ul.product_id = p.id
                   ORDER BY ul.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def insert_analytics(self, product_id: int, views: int = 0,
                         favorites: int = 0, sales: int = 0,
                         revenue: float = 0.0) -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO analytics
                   (product_id, views, favorites, sales, revenue_usd, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (product_id, views, favorites, sales, revenue, now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_top_performing_niches(self, limit: int = 10) -> list[dict]:
        """Aggregate analytics by niche to find top performers."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT n.keyword,
                          COUNT(DISTINCT p.id)  AS product_count,
                          SUM(a.views)          AS total_views,
                          SUM(a.favorites)      AS total_favorites,
                          SUM(a.sales)          AS total_sales,
                          SUM(a.revenue_usd)    AS total_revenue
                   FROM analytics a
                   JOIN products  p ON a.product_id = p.id
                   JOIN niches    n ON p.niche_id   = n.id
                   GROUP BY n.id
                   ORDER BY total_revenue DESC, total_sales DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    db = DatabaseManager("data/test_database.db")
    nid = db.upsert_niche(
        "cute cats",
        "google_trends",
        {"commercial_potential": 0.9, "evergreen_score": 0.8,
         "competition_estimate": 0.5, "originality_opportunity": 0.7,
         "overall_score": 0.78, "reasoning": "Cats always sell."},
    )
    print("Niche ID:", nid)
    iid = db.insert_idea(nid, {
        "title": "Space Cat Astronaut",
        "description": "A cartoon cat in a spacesuit floating among stars.",
        "emotion": "Whimsy and wonder",
        "colors": ["#1a1a2e", "#e94560", "#ffffff"],
        "best_products": ["t-shirt", "sticker", "mug"],
    })
    print("Idea ID:", iid)
    print("Summary:", db.get_products_summary())
