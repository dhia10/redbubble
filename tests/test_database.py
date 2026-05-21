"""
tests/test_database.py
=======================
Unit tests for the DatabaseManager module.
Run with: pytest tests/
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.database_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    """Provide a fresh, temporary DatabaseManager for each test."""
    db_path = str(tmp_path / "test.db")
    return DatabaseManager(db_path)


# ---------------------------------------------------------------------------
# Niche tests
# ---------------------------------------------------------------------------

class TestNiches:
    def test_upsert_niche_creates_record(self, db):
        nid = db.upsert_niche("space cat", "google_trends", {
            "overall_score": 0.75,
            "commercial_potential": 0.8,
            "evergreen_score": 0.7,
            "competition_estimate": 0.3,
            "originality_opportunity": 0.9,
        })
        assert nid > 0

    def test_upsert_niche_upserts_on_duplicate(self, db):
        nid1 = db.upsert_niche("cute dog", "google_trends", {"overall_score": 0.5})
        nid2 = db.upsert_niche("cute dog", "redbubble",    {"overall_score": 0.8})
        assert nid1 == nid2  # Same keyword → same row

    def test_get_top_niches_ordered(self, db):
        db.upsert_niche("niche_a", "test", {"overall_score": 0.3})
        db.upsert_niche("niche_b", "test", {"overall_score": 0.9})
        db.upsert_niche("niche_c", "test", {"overall_score": 0.6})
        top = db.get_top_niches(limit=3)
        assert top[0]["keyword"] == "niche_b"
        assert top[1]["keyword"] == "niche_c"

    def test_increment_niche_usage(self, db):
        nid = db.upsert_niche("funny cat", "test", {"overall_score": 0.5})
        db.increment_niche_usage(nid)
        db.increment_niche_usage(nid)
        niches = db.get_top_niches(limit=1)
        assert niches[0]["times_used"] == 2


# ---------------------------------------------------------------------------
# Idea tests
# ---------------------------------------------------------------------------

class TestIdeas:
    def test_insert_idea(self, db):
        nid = db.upsert_niche("plants", "test", {"overall_score": 0.6})
        iid = db.insert_idea(nid, {
            "title": "Succulent Squad",
            "description": "Cute succulents with faces.",
            "emotion": "Cheerful",
            "colors": ["green", "pink"],
            "best_products": ["sticker", "mug"],
        })
        assert iid > 0

    def test_update_idea_status(self, db):
        nid = db.upsert_niche("ocean", "test", {"overall_score": 0.5})
        iid = db.insert_idea(nid, {"title": "Wave Rider", "description": "A surfing wave."})
        db.update_idea_status(iid, "generated")
        ideas = db.get_ideas_by_niche(nid)
        assert ideas[0]["status"] == "generated"


# ---------------------------------------------------------------------------
# Product & upload log tests
# ---------------------------------------------------------------------------

class TestProducts:
    def _setup_product(self, db):
        nid = db.upsert_niche("test niche", "test", {"overall_score": 0.7})
        iid = db.insert_idea(nid, {"title": "Test Idea", "description": "Test"})
        pid_prompt = db.insert_prompt(iid, "A test prompt", "openai", "dall-e-3")
        did = db.insert_design(pid_prompt, iid, "/fake/path.png", {
            "file_size_bytes": 1000, "width_px": 4500, "height_px": 5400,
            "format": "PNG", "is_valid": True,
        })
        prod_id = db.insert_product(did, iid, nid, {
            "title": "Test Product",
            "description": "A test.",
            "tags": ["tag1", "tag2"],
        })
        return prod_id

    def test_insert_product(self, db):
        prod_id = self._setup_product(db)
        assert prod_id > 0

    def test_update_product_published(self, db):
        prod_id = self._setup_product(db)
        db.update_product_published(prod_id, "https://redbubble.com/works/123", "123")
        products = db.get_published_products()
        assert len(products) == 1
        assert products[0]["redbubble_work_id"] == "123"

    def test_get_products_summary(self, db):
        summary = db.get_products_summary()
        assert "total_designs" in summary
        assert "upload_success_rate" in summary

    def test_log_upload(self, db):
        prod_id = self._setup_product(db)
        lid = db.log_upload(prod_id, 1, "success", "Published OK")
        assert lid > 0
        logs = db.get_recent_logs(limit=5)
        assert logs[0]["status"] == "success"


# ---------------------------------------------------------------------------
# Analytics tests
# ---------------------------------------------------------------------------

class TestAnalytics:
    def test_insert_analytics(self, db):
        nid = db.upsert_niche("art", "test", {"overall_score": 0.5})
        iid = db.insert_idea(nid, {"title": "T"})
        pid_prompt = db.insert_prompt(iid, "prompt", "openai")
        did  = db.insert_design(pid_prompt, iid, "/p.png", {})
        prod = db.insert_product(did, iid, nid, {"title": "T", "tags": []})
        db.update_product_published(prod, "https://rb.com/1", "1")
        aid = db.insert_analytics(prod, views=100, favorites=5, sales=2, revenue=1.50)
        assert aid > 0

    def test_top_performing_niches(self, db):
        rows = db.get_top_performing_niches()
        assert isinstance(rows, list)
