"""
main.py  —  FREE TIER EDITION
==============================
Orchestrates the full Redbubble Automation pipeline.

Text generation auto-routing:
  • If OPENAI_API_KEY is set  → use GPT-4o-mini (paid, higher quality)
  • If not set                → use FreeTextGenerator (templates, zero cost) ✓

Image generation:
  • Leonardo AI free tier (150 tokens/day, ~12 images/day at 768×768)
  • Falls back to placeholder PNG if quota exhausted

Run modes:
  python main.py              → Start the built-in daily scheduler loop
  python main.py --run-now    → Execute one full pipeline run immediately
  python main.py --run-now --dry-run  → Full run but skip Redbubble upload
  python main.py --dashboard  → Launch the Streamlit dashboard
  python main.py --test-image → Test Leonardo AI connection only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_dir: str = "logs", log_level: str = "INFO") -> None:
    log_path = ROOT / log_dir
    log_path.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_path / "pipeline.log"),
                                encoding="utf-8"),
        ],
    )

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg_path = ROOT / "config" / "settings.yaml"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

# ---------------------------------------------------------------------------
# Text-backend factory
# ---------------------------------------------------------------------------

def _make_text_backend(config: dict):
    """
    Return the appropriate text generator.
    Uses FreeTextGenerator unless OPENAI_API_KEY is present.
    """
    backend = config.get("pipeline", {}).get("text_backend", "template")
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if backend == "openai" and openai_key:
        from modules.idea_generator import IdeaGenerator
        from modules.prompt_builder import PromptBuilder
        from modules.seo_generator  import SEOGenerator
        logging.getLogger("main").info("Text backend: OpenAI GPT")
        return "openai", IdeaGenerator(), PromptBuilder(), SEOGenerator()

    from modules.free_text_generator import FreeTextGenerator
    logging.getLogger("main").info(
        "Text backend: FreeTextGenerator (zero cost, template-based)"
    )
    ftg = FreeTextGenerator()
    return "free", ftg, ftg, ftg   # same object handles all three tasks


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """Full daily automation pipeline — free-tier optimised."""

    def __init__(self, config: dict) -> None:
        self.config  = config
        self.log     = logging.getLogger("Pipeline")

        from modules.database_manager  import DatabaseManager
        from modules.trend_detector    import TrendDetector
        from modules.niche_scorer      import NicheScorer
        from modules.image_generator   import ImageGenerator
        from modules.redbubble_uploader import RedbubbleUploader
        from modules.analytics         import Analytics

        db_cfg       = config.get("database",         {})
        trend_cfg    = config.get("trend_detection",  {})
        img_cfg      = config.get("image_generation", {})
        rb_cfg       = config.get("redbubble",        {})
        pipe_cfg     = config.get("pipeline",         {})

        self.db = DatabaseManager(db_cfg.get("path", "data/database.db"))

        self.trend_detector = TrendDetector(
            sources=trend_cfg.get("sources", ["google_trends", "redbubble_trending"]),
            timeframe=trend_cfg.get("google_trends_timeframe", "now 7-d"),
        )

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            from modules.niche_scorer import NicheScorer as _GptScorer
            self.niche_scorer = _GptScorer(
                min_score=trend_cfg.get("min_niche_score", 0.35),
            )
        else:
            # Free niche scorer: skip LLM, apply heuristics only
            self.niche_scorer = _FreeNicheScorer(
                min_score=trend_cfg.get("min_niche_score", 0.35)
            )

        # Text backend (ideas / prompts / SEO)
        self._backend_name, self._idea_gen, self._prompt_builder, self._seo_gen = \
            _make_text_backend(config)

        self.image_gen = ImageGenerator(
            provider=img_cfg.get("provider", "leonardo"),
            output_dir=img_cfg.get("output_dir", "data/images"),
            model=img_cfg.get("model", "phoenix"),
            gen_width=img_cfg.get("width", 768),
            gen_height=img_cfg.get("height", 768),
            num_inference_steps=25,
        )

        self.uploader = RedbubbleUploader(
            headless=rb_cfg.get("headless", True),
            slow_mo_ms=rb_cfg.get("slow_mo_ms", 200),
            timeout_ms=rb_cfg.get("timeout_ms", 90_000),
            max_retries=pipe_cfg.get("max_upload_retries", 3),
            upload_delay_seconds=pipe_cfg.get("upload_delay_seconds", 8),
            products_to_enable=rb_cfg.get("products_to_enable", []),
        )

        self.analytics = Analytics(db_manager=self.db)

        self.niches_per_run  = pipe_cfg.get("niches_per_run", 2)
        self.ideas_per_niche = pipe_cfg.get("ideas_per_niche", 2)
        self.dry_run         = pipe_cfg.get("dry_run", False)

        if self.dry_run:
            self.log.warning("⚠  DRY RUN — upload to Redbubble is DISABLED.")

    # ------------------------------------------------------------------
    # Pipeline runner
    # ------------------------------------------------------------------

    def run(self) -> dict:
        stats = {
            "niches_processed": 0,
            "ideas_generated":  0,
            "images_created":   0,
            "products_uploaded": 0,
            "errors": [],
        }

        self._stage("1/10  Trend Detection")
        keywords = self.trend_detector.fetch_all()
        self.log.info("Detected %d raw keywords.", len(keywords))

        self._stage("2/10  Niche Scoring")
        scored = self.niche_scorer.score_keywords(keywords)
        self.log.info("%d niches scored above threshold.", len(scored))

        self._stage("3/10  Niche Selection")
        # Filter out niches used in the last 7 days to ensure variety
        import datetime as _dt
        _cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=7)).isoformat()
        fresh = []
        repeated = []
        for nd in scored:
            kw = nd.get("keyword", "")
            try:
                # FIX: _conn is a context manager — use it properly
                with self.db._conn() as _c:
                    row = _c.execute(
                        "SELECT last_used_at FROM niches WHERE keyword=?", (kw,)
                    ).fetchone()
                if row and row["last_used_at"] and row["last_used_at"] > _cutoff:
                    repeated.append(nd)   # used recently — deprioritise
                    continue
            except Exception:
                pass
            fresh.append(nd)

        # Prefer fresh niches; fall back to repeated ones if not enough
        candidates = fresh + repeated

        # ── Diversity check: prevent two niches sharing the same primary word ──
        # e.g. "dog lover" and "cute dog" must not both be picked in same run
        _ANIMAL_WORDS = {
            "cat", "dog", "fox", "wolf", "bear", "rabbit", "owl",
            "penguin", "frog", "axolotl", "sloth", "deer", "horse",
            "koala", "duck", "shark", "turtle", "butterfly",
        }
        _THEME_WORDS = {
            "witch", "space", "galaxy", "mushroom", "coffee", "plant",
            "ocean", "mountain", "teacher", "nurse", "gamer", "reader",
            "yoga", "hiking", "fishing", "cycling", "pizza", "unicorn",
            "dragon", "dinosaur", "mermaid", "astronaut", "halloween",
        }

        def _primary_token(kw: str) -> str:
            """Return the dominant word so we can enforce one-per-topic."""
            words = set(kw.lower().split())
            animals = words & _ANIMAL_WORDS
            if animals:
                return next(iter(animals))
            themes = words & _THEME_WORDS
            if themes:
                return next(iter(themes))
            return kw.split()[0]

        diverse_top: list[dict] = []
        used_tokens: set[str] = set()
        for nd in candidates:
            token = _primary_token(nd.get("keyword", ""))
            if token not in used_tokens:
                diverse_top.append(nd)
                used_tokens.add(token)
            if len(diverse_top) >= self.niches_per_run:
                break
        # Safety fallback: if diversity filter left us short, pad with remainder
        if len(diverse_top) < self.niches_per_run:
            for nd in candidates:
                if nd not in diverse_top:
                    diverse_top.append(nd)
                if len(diverse_top) >= self.niches_per_run:
                    break

        top = diverse_top
        self.log.info(
            "Niche selection: %d fresh, %d repeated → diverse pick: %s",
            len(fresh), len(repeated),
            [n.get("keyword") for n in top],
        )
        niche_records: list[tuple[int, str]] = []
        for nd in top:
            kw = nd.get("keyword", "")
            if not kw:
                continue
            nid = self.db.upsert_niche(kw, nd.get("source", "mixed"), nd)
            self.db.increment_niche_usage(nid)
            niche_records.append((nid, kw))
            self.log.info("  Niche [%d]: '%s' (score %.2f)",
                          nid, kw, nd.get("overall_score", 0))

        stats["niches_processed"] = len(niche_records)
        if not niche_records:
            self.log.warning("No niches — aborting.")
            return stats

        for niche_id, niche_kw in niche_records:
            self.log.info("\n>>> Niche: '%s' <<<", niche_kw)
            self._process_niche(niche_id, niche_kw, stats)

        self._stage("10/10  Analytics Update")
        try:
            self.analytics.collect()
        except Exception as exc:
            self.log.warning("Analytics failed: %s", exc)
            stats["errors"].append(f"Analytics: {exc}")

        self._print_summary(stats)
        return stats

    # ------------------------------------------------------------------
    # Per-niche loop
    # ------------------------------------------------------------------

    def _process_niche(self, nid: int, niche: str, stats: dict) -> None:
        self._stage(f"4/10  Idea Generation  [{niche}]")
        try:
            if self._backend_name == "free":
                ideas = self._idea_gen.generate_ideas(niche, count=self.ideas_per_niche)
            else:
                ideas = self._idea_gen.generate(niche, count=self.ideas_per_niche)
        except Exception as exc:
            self.log.error("Idea generation failed: %s", exc)
            stats["errors"].append(f"Ideas/{niche}: {exc}")
            return

        for idea in ideas:
            self._process_idea(nid, niche, idea, stats)

    def _process_idea(self, nid: int, niche: str, idea: dict, stats: dict) -> None:
        title = idea.get("title", "Untitled")

        # Save idea
        idea_id = self.db.insert_idea(nid, idea)
        stats["ideas_generated"] += 1
        self.log.info("  Idea [%d]: '%s'", idea_id, title)

        # ── Stage 5: Prompt ──────────────────────────────────────────
        try:
            if self._backend_name == "free":
                prompt_text = self._prompt_builder.build_prompt(idea)
                neg_prompt  = self._prompt_builder.build_negative_prompt()
            else:
                prompt_text = self._prompt_builder.build(idea)
                neg_prompt  = ""
            prompt_id = self.db.insert_prompt(idea_id, prompt_text,
                                              provider="leonardo",
                                              model="phoenix")
            self.db.update_idea_status(idea_id, "prompted")
        except Exception as exc:
            self.log.error("Prompt failed for '%s': %s", title, exc)
            stats["errors"].append(f"Prompt/{title}: {exc}")
            return

        # ── Stage 6: Image ───────────────────────────────────────────
        self._stage(f"6/10  Image Generation  [{title}]")
        try:
            img_result = self.image_gen.generate(
                prompt=prompt_text,
                idea_title=title,
                negative_prompt=neg_prompt,
            )
        except Exception as exc:
            self.log.error("Image gen exception: %s", exc)
            self.db.update_idea_status(idea_id, "failed")
            stats["errors"].append(f"Image/{title}: {exc}")
            return

        if not img_result.success:
            self.log.error("Image gen failed: %s", img_result.error)
            self.db.update_idea_status(idea_id, "failed")
            stats["errors"].append(f"Image/{title}: {img_result.error}")
            return

        design_id = self.db.insert_design(
            prompt_id=prompt_id,
            idea_id=idea_id,
            file_path=img_result.file_path,
            meta={
                "file_size_bytes": img_result.file_size_bytes,
                "width_px":        img_result.width_px,
                "height_px":       img_result.height_px,
                "format":          img_result.format,
                "is_valid":        img_result.is_valid,
                "validation_msg":  img_result.validation_msg,
            },
        )
        self.db.update_idea_status(idea_id, "generated")
        stats["images_created"] += 1
        self.log.info("  Image saved [%d]: %s", design_id, img_result.file_path)

        # ── Stage 7: SEO ─────────────────────────────────────────────
        try:
            if self._backend_name == "free":
                seo = self._seo_gen.generate_seo(idea, niche)
            else:
                seo = self._seo_gen.generate(idea, niche)
        except Exception as exc:
            self.log.error("SEO failed: %s", exc)
            seo = {
                "title":       title[:80],
                "description": idea.get("description", "")[:500],
                "tags":        niche.split(),
            }

        # ── Stage 8: Save product ─────────────────────────────────────
        product_id = self.db.insert_product(design_id, idea_id, nid, seo)
        self.log.info("  Product record [%d]: %s", product_id, seo["title"])

        # ── Stage 9: Upload ───────────────────────────────────────────
        self._stage(f"9/10  Upload  [{title}]")
        if self.dry_run:
            self.log.info("  [DRY RUN] Upload skipped for product %d.", product_id)
            self.db.log_upload(product_id, 1, "success", "Dry run")
            return

        try:
            upload_result = self.uploader.upload(
                image_path=img_result.file_path,
                seo=seo,
                product_id=product_id,
            )
        except Exception as exc:
            self.log.error("Upload exception: %s", exc)
            self.db.update_product_failed(product_id)
            self.db.log_upload(product_id, 1, "failed", str(exc))
            stats["errors"].append(f"Upload/p{product_id}: {exc}")
            return

        if upload_result.success:
            self.db.update_product_published(
                product_id,
                url=upload_result.product_url,
                work_id=upload_result.work_id,
            )
            self.db.log_upload(
                product_id, 1, "success",
                f"Published: {upload_result.product_url}",
                duration_ms=upload_result.duration_ms,
            )
            self.db.update_idea_status(idea_id, "uploaded")
            stats["products_uploaded"] += 1
            self.log.info("  ✅ Published: %s", upload_result.product_url)
        else:
            self.db.update_product_failed(product_id)
            self.db.log_upload(
                product_id, 1, "failed",
                upload_result.error,
                screenshot=upload_result.screenshot_path,
                duration_ms=upload_result.duration_ms,
            )
            stats["errors"].append(f"Upload/p{product_id}: {upload_result.error}")
            self.log.error("  ❌ Upload failed: %s", upload_result.error)

        time.sleep(self.uploader.upload_delay)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stage(self, name: str) -> None:
        self.log.info("─" * 50)
        self.log.info("  %s", name)
        self.log.info("─" * 50)

    def _print_summary(self, stats: dict) -> None:
        self.log.info("\n%s", "=" * 50)
        self.log.info("  PIPELINE COMPLETE")
        self.log.info("  %-26s : %s", "Niches processed",   stats["niches_processed"])
        self.log.info("  %-26s : %s", "Ideas generated",    stats["ideas_generated"])
        self.log.info("  %-26s : %s", "Images created",     stats["images_created"])
        self.log.info("  %-26s : %s", "Products uploaded",  stats["products_uploaded"])
        if stats["errors"]:
            self.log.warning("  Errors (%d):", len(stats["errors"]))
            for e in stats["errors"]:
                self.log.warning("    • %s", e)
        self.log.info("=" * 50)


# ---------------------------------------------------------------------------
# Free niche scorer (no OpenAI needed)
# ---------------------------------------------------------------------------

class _FreeNicheScorer:
    """
    Scores keywords as POD niches using heuristics only — no API required.

    Applies:
    - IP risk check (blocks brand/celebrity keywords)
    - Length / quality check
    - Community-strength signal based on keyword patterns
    - Evergreen category boosting
    """

    _IP_RISK = frozenset([
        "disney", "marvel", "dc comics", "pokemon", "harry potter", "star wars",
        "nike", "adidas", "gucci", "coca cola", "netflix", "youtube",
        "mickey", "spiderman", "batman", "superman", "elsa", "frozen",
        "minecraft", "fortnite", "roblox", "among us", "fnaf",
    ])

    _HIGH_VALUE_WORDS = frozenset([
        # Animals (proven sellers)
        "cat", "dog", "axolotl", "frog", "fox", "mushroom", "capybara", "duck",
        "corgi", "husky", "panda",
        # Occupations (gift niche = high purchase intent)
        "nurse", "teacher", "engineer", "firefighter", "paramedic", "librarian",
        "veterinarian", "pharmacist", "dentist", "mechanic", "chef",
        # Aesthetics that convert
        "witch", "cottagecore", "goblincore", "academia",
        "kawaii", "aesthetic", "botanical",
        # Lifestyle identity
        "gamer", "reader", "hiker", "cyclist", "yogi", "climber",
        "introvert", "coffee", "pizza", "plant",
        # POD-specific boosters
        "funny", "cute", "vintage", "retro", "gift", "mom", "dad", "lover",
        "pride", "mental", "awareness",
        # Space / cosmos (strong art print sales)
        "space", "moon", "galaxy", "nebula", "cosmos",
    ])

    def __init__(self, min_score: float = 0.35) -> None:
        self.min_score = min_score

    def score_keywords(self, keywords: list[str]) -> list[dict]:
        import random as _rng
        results: list[dict] = []
        seen: set[str] = set()

        for kw in keywords:
            kw_lower = kw.strip().lower()
            if not kw_lower or kw_lower in seen:
                continue
            seen.add(kw_lower)

            # IP risk
            if any(risk in kw_lower for risk in self._IP_RISK):
                continue

            # Too short / too long
            if len(kw_lower) < 3 or len(kw_lower) > 60:
                continue

            score = self._heuristic_score(kw_lower)
            # Add small random jitter (±0.06) so tied niches don't always
            # resolve in the same deterministic order across runs
            score = round(min(0.95, max(0.30, score + _rng.uniform(-0.06, 0.06))), 3)
            if score >= self.min_score:
                results.append({
                    "keyword":                kw_lower,
                    "source":                 "heuristic",
                    "overall_score":          round(score, 3),
                    "commercial_potential":   round(score * 0.9, 3),
                    "evergreen_score":        round(score * 0.8, 3),
                    "competition_estimate":   round(1 - score * 0.7, 3),
                    "originality_opportunity": round(score * 0.85, 3),
                    "reasoning":              "Heuristic scoring (no API)",
                })

        results.sort(key=lambda x: x["overall_score"], reverse=True)
        logging.getLogger("FreeNicheScorer").info(
            "Scored %d keywords → %d pass threshold %.2f",
            len(keywords), len(results), self.min_score,
        )
        return results

    def _heuristic_score(self, kw: str) -> float:
        score = 0.50   # Base score

        words = kw.split()

        # Multi-word niches are less saturated on Redbubble
        if len(words) >= 2:
            score += 0.08
        if len(words) >= 3:
            score += 0.05  # 3-word niches are even more specific

        # High-value words boost
        for word in words:
            if word in self._HIGH_VALUE_WORDS:
                score += 0.15
                break

        # Compound niche — "space cat", "witch fox", "mushroom frog" (POD gold)
        _ANIMALS = {"cat", "dog", "fox", "wolf", "bear", "axolotl", "frog",
                    "rabbit", "owl", "penguin", "koala", "capybara", "duck",
                    "corgi", "husky", "panda", "crow", "raven", "ghost"}
        _THEMES  = {"space", "galaxy", "witch", "magic", "mushroom", "coffee",
                    "plant", "ocean", "mountain", "moon", "dark", "retro",
                    "goblin", "cottage", "academia", "gothic", "kawaii"}
        has_animal = any(w in _ANIMALS for w in words)
        has_theme  = any(w in _THEMES  for w in words)
        if has_animal and has_theme:
            score += 0.20  # "space cat", "witch fox" — sells as sticker + shirt

        # Occupational gift (high purchase intent — people buy for colleagues)
        _OCCUPATIONS = {"nurse", "teacher", "engineer", "firefighter", "doctor",
                        "librarian", "veterinarian", "pharmacist", "dentist",
                        "paramedic", "mechanic", "chef", "accountant"}
        _GIFT_SIGNALS = {"gift", "appreciation", "life", "proud", "humor",
                         "funny", "lover", "mom", "dad", "wife", "husband"}
        has_job  = any(w in _OCCUPATIONS  for w in words)
        has_gift = any(w in _GIFT_SIGNALS for w in words)
        if has_job:
            score += 0.15  # occupation alone is good
        if has_job and has_gift:
            score += 0.10  # "nurse funny" / "teacher gift" = purchase intent

        # Seasonal boost — May/June 2026: graduation + Father's Day
        import datetime as _dt_score
        month = _dt_score.datetime.utcnow().month
        if month in (5, 6):
            seasonal_words = {"graduation", "graduate", "grad", "father",
                              "dad", "fathers", "summer", "pride"}
            if any(w in seasonal_words for w in words):
                score += 0.12

        # Identity keywords ("mom", "dad", "lover", "addict") — gift/self-buy
        if any(w in kw for w in ["mom", "dad", "lover", "addict", "obsessed",
                                  "life", "awareness", "pride"]):
            score += 0.08

        # Cap at 0.95
        return min(score, 0.95)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Redbubble Automation — Free Tier")
    parser.add_argument("--run-now",    action="store_true",
                        help="Run the pipeline once immediately")
    parser.add_argument("--dashboard",  action="store_true",
                        help="Launch the Streamlit dashboard")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Skip actual Redbubble upload")
    parser.add_argument("--test-image", action="store_true",
                        help="Test Leonardo AI connection and token balance")
    args = parser.parse_args()

    config = _load_config()
    _setup_logging(
        config.get("app", {}).get("log_dir", "logs"),
        config.get("app", {}).get("log_level", "INFO"),
    )
    log = logging.getLogger("main")

    if args.test_image:
        from modules.image_generator import ImageGenerator
        key = os.environ.get("LEONARDO_API_KEY", "")
        tokens = ImageGenerator.check_remaining_tokens(key)
        print(f"\n{'='*40}")
        print(f"  Leonardo AI token balance: {tokens}")
        print(f"  Key ends in: ...{key[-6:] if key else 'NOT SET'}")
        print(f"{'='*40}\n")
        gen = ImageGenerator(provider="leonardo", model="phoenix",
                             gen_width=512, gen_height=512,
                             output_dir="data/images")
        result = gen.generate(
            "A cute cartoon cat astronaut floating in space, flat vector art, "
            "sticker style, transparent background",
            idea_title="Test Image"
        )
        print(f"Result: success={result.success}")
        print(f"Path:   {result.file_path}")
        print(f"Size:   {result.width_px}×{result.height_px}")
        if result.error:
            print(f"Error:  {result.error}")
        return

    if args.dashboard:
        import subprocess
        log.info("Launching dashboard…")
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run",
             str(ROOT / "dashboard" / "app.py")],
            check=True,
        )
        return

    if args.dry_run:
        config.setdefault("pipeline", {})["dry_run"] = True

    pipeline = Pipeline(config)

    if args.run_now:
        pipeline.run()
    else:
        from modules.scheduler import PipelineScheduler
        run_time = config.get("scheduler", {}).get("run_time", "09:00")
        scheduler = PipelineScheduler(
            pipeline_fn=pipeline.run,
            run_time=run_time,
        )
        log.info("Scheduler starting. Daily run at %s UTC.", run_time)
        scheduler.start_loop()


if __name__ == "__main__":
    main()
