# -*- coding: utf-8 -*-
"""
pinterest_publisher.py
======================
Publishes each new Redbubble design as a Pinterest Pin, driving free
organic traffic back to the product listing.

Strategy
--------
- Connects to the SAME Opera GX debug browser already open for Redbubble
  (CDP on port 9222) - no second browser, no extra bot-detection risk.
- Opens Pinterest pin-builder in a dedicated tab that is reused across runs.
- Creates a pin: design image + SEO title/description + Redbubble link.
- Best-effort: any error is logged but NEVER crashes the Redbubble pipeline.

Pinterest traffic funnel
------------------------
  Pinterest Pin
    -> Destination link = Redbubble product URL
    -> Description contains hashtags + "Shop this design ->"
    -> Every save/re-pin multiplies reach for free
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PinResult:
    success: bool
    pin_url: str = ""
    error: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# PinterestPublisher
# ---------------------------------------------------------------------------

class PinterestPublisher:
    """
    Automates Pinterest pin creation to drive traffic to Redbubble listings.

    Parameters
    ----------
    board_name     : Pinterest board to post to (created automatically if absent)
    cdp_port       : CDP debug port - must match start_debug_browser.bat
    timeout_ms     : Default Playwright element timeout
    screenshot_dir : Where to save debug screenshots on failure
    """

    PIN_BUILDER_URL = "https://www.pinterest.com/pin-builder/"

    # -- Board definitions ------------------------------------------------
    # Each entry: (trigger_keywords, board_name, board_description)
    # The publisher auto-creates these boards if they don't exist yet,
    # then routes each pin to the most appropriate one.
    # Priority: first keyword match wins (most-specific rules at the top).
    _BOARD_ROUTES: list[tuple[frozenset, str, str]] = [
        # Solarpunk -> JUNO board
        (frozenset(["solarpunk", "sunpunk", "bioluminescent",
                    "nature tech", "green energy"]),
         "JUNO: Solarpunk Anime Designs",
         "Solarpunk, nature-tech fusion and bioluminescent anime art. "
         "Hopeful futurism where nature and technology coexist beautifully."),
        # Dieselpunk -> KAI board
        (frozenset(["dieselpunk", "steampunk", "industrial",
                    "mechanic", "clockwork", "retro futurism"]),
         "KAI: Dieselpunk Anime Art",
         "Dieselpunk and industrial anime art. Retro-futuristic mechanics, "
         "brass machinery, and dark industrial atmosphere."),
        # Cyberpop / Harajuku -> cyberpop board
        (frozenset(["cyberpop", "harajuku", "decora",
                    "kawaii cyber", "y2k cyber", "candy neon", "pop art"]),
         "PIXEL: Cyberpop Anime Designs",
         "Cyberpop and harajuku anime aesthetic. Explosive neon colour, "
         "cute characters, and Y2K cyber energy."),
        # Grunge / bold graphic -> grunge board
        (frozenset(["grunge", "drip art", "bold graphic",
                    "street art", "psychedelic grunge", "urban gothic"]),
         "RAW: Bold Graphic Grunge Art",
         "Bold, high-contrast grunge art and urban gothic design. "
         "Drip art, street art energy, dark and striking graphics."),
        # Dystopian / post-apocalyptic -> dystopian board
        (frozenset(["dystopian", "post apocalyptic",
                    "dark future", "wasteland", "urban decay"]),
         "VOID: Cyberpunk Dystopian Posters",
         "Dystopian anime poster art. Dark futures, ruined megacities, "
         "and post-apocalyptic warrior aesthetics."),
        # Vaporwave / synthwave -> retro board
        (frozenset(["vaporwave", "synthwave", "retrowave",
                    "outrun", "80s neon", "lo fi", "pastel neon"]),
         "WAVE: Vaporwave & Synthwave Art",
         "Vaporwave and synthwave aesthetic art. Retro neon, pastel grids, "
         "80s outrun vibes and anime nostalgia."),
        # Dark gothic anime -> gothic board
        (frozenset(["gothic", "dark fantasy", "dark magic",
                    "dark witch", "occult", "necromancer", "horror"]),
         "SHADE: Dark Gothic Anime Art",
         "Dark gothic and occult anime art. Witches, dark magic, "
         "horror aesthetics and beautiful darkness."),
        # Default -> NEON (cyberpunk techwear - the main board)
        (frozenset(["cyberpunk", "techwear", "neon", "glitch",
                    "android", "mecha", "sci fi", "cyber", "hacker",
                    "dark", "anime", "manga", "warrior", "samurai"]),
         "NEON: High Contrast Cyberpunk Techwear",
         "High contrast cyberpunk and techwear anime art. Bold neon, "
         "dark futuristic cityscapes, and striking character designs."),
    ]

    # All board names in one place (extracted from routes above + fallback)
    @classmethod
    def all_board_names(cls) -> list[tuple[str, str]]:
        """Return list of (board_name, description) for all boards."""
        seen = set()
        result = []
        for _, name, desc in cls._BOARD_ROUTES:
            if name not in seen:
                seen.add(name)
                result.append((name, desc))
        return result

    def __init__(
        self,
        board_name: str = "NEON: High Contrast Cyberpunk Techwear Designs",
        cdp_port: int = 9222,
        timeout_ms: int = 60_000,
        screenshot_dir: str = "data/exports/screenshots",
    ) -> None:
        self.board_name     = board_name   # fallback if routing fails
        self.cdp_port       = cdp_port
        self.timeout_ms     = timeout_ms
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._pw      = None
        self._browser = None
        self._context = None
        self._page    = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _route_board(self, niche: str, title: str = "") -> str:
        """
        Choose the most appropriate Pinterest board for this niche/design.

        Scans the niche string and title against each rule in _BOARD_ROUTES
        and returns the first matching board name.
        Falls back to self.board_name (default NEON board) if nothing matches.
        """
        combined = f"{niche} {title}".lower()
        for keywords, board, _desc in self._BOARD_ROUTES:
            for kw in keywords:
                if kw in combined:
                    logger.info(
                        "Pinterest board routing: '%s' matched '%s' -> '%s'",
                        niche[:40], kw, board,
                    )
                    return board
        logger.info(
            "Pinterest board routing: no match for '%s' -> default '%s'",
            niche[:40], self.board_name,
        )
        return self.board_name

    def publish_pin(
        self,
        image_path: str,
        title: str,
        description: str,
        tags: list,
        redbubble_url: str,
        niche: str = "",
    ) -> PinResult:
        """
        Create one Pinterest pin linking to the Redbubble product.

        Parameters
        ----------
        image_path    : Local path to the design PNG
        title         : Pin title (from SEO generator)
        description   : Pin body text (from SEO generator)
        tags          : List of keyword strings -> converted to #hashtags
        redbubble_url : Destination link shown on the pin
        niche         : Raw niche keyword (added as extra hashtag)
        """
        start_ms = int(time.time() * 1000)

        try:
            self._connect()
        except Exception as exc:
            return PinResult(
                success=False,
                error=f"Browser connect failed: {exc}",
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        try:
            result = self._do_publish(
                image_path, title, description, tags, redbubble_url, niche
            )
            result.duration_ms = int(time.time() * 1000) - start_ms
            return result
        except Exception as exc:
            logger.error("Pinterest publish exception: %s", exc)
            self._screenshot("publish_exception")
            return PinResult(
                success=False,
                error=str(exc),
                duration_ms=int(time.time() * 1000) - start_ms,
            )
        finally:
            self._disconnect()

    # ------------------------------------------------------------------
    # Browser: connect / disconnect
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Attach to the already-running Opera GX debug browser via CDP."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._pw = sync_playwright().start()
        cdp_url  = f"http://localhost:{self.cdp_port}"

        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url, timeout=5_000)
        except Exception as exc:
            self._pw.stop()
            self._pw = None
            raise RuntimeError(
                f"Could not connect to debug browser on port {self.cdp_port}: {exc}. "
                "Make sure start_debug_browser.bat is running before the pipeline."
            )

        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()

        # Reuse an existing Pinterest tab if one is open
        self._page = None
        try:
            for p in self._context.pages:
                try:
                    if not p.is_closed() and "pinterest.com" in p.url:
                        self._page = p
                        logger.info("Pinterest: reusing existing tab: %s", p.url[:80])
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if self._page is None:
            self._page = self._context.new_page()
            logger.info("Pinterest: opened new tab.")

        self._page.set_default_timeout(self.timeout_ms)

    def _disconnect(self) -> None:
        """Detach Playwright without closing the browser or any tabs."""
        time.sleep(0.3)
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None

    # ------------------------------------------------------------------
    # Core publish flow
    # ------------------------------------------------------------------

    def _do_publish(
        self,
        image_path: str,
        title: str,
        description: str,
        tags: list,
        redbubble_url: str,
        niche: str,
    ) -> PinResult:
        page = self._page

        # -- 1. Navigate to pin builder ----------------------------------
        already_there = "pinterest.com/pin-builder" in page.url
        if not already_there:
            logger.info("Pinterest: navigating to pin builder...")
            try:
                page.goto(self.PIN_BUILDER_URL, wait_until="networkidle", timeout=45_000)
            except Exception:
                try:
                    page.goto(self.PIN_BUILDER_URL, wait_until="domcontentloaded",
                              timeout=30_000)
                except Exception:
                    pass
        else:
            logger.info("Pinterest: already on pin builder. Reloading to ensure clean state...")
            try:
                page.reload(wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                try:
                    page.goto(self.PIN_BUILDER_URL, wait_until="domcontentloaded", timeout=20_000)
                except Exception:
                    pass
        self._delay(2.0, 3.5)

        # -- 2. Login guard & navigation validation ----------------------
        cur = page.url
        if "login" in cur or "/auth" in cur:
            try:
                page.bring_to_front()
            except Exception:
                pass
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1000, 300)
                    time.sleep(0.1)
            except Exception:
                pass
            raise RuntimeError(
                "Pinterest redirected to login. "
                "Please log into Pinterest manually in Opera GX, then retry."
            )

        # Re-navigate if we ended up elsewhere (e.g., homepage after login check)
        if "pin-builder" not in page.url and "pin-creation" not in page.url:
            logger.info("Pinterest: not on pin builder (URL: %s), attempting navigation...", page.url)
            try:
                page.goto(self.PIN_BUILDER_URL, wait_until="networkidle", timeout=40_000)
                self._delay(2.0, 3.5)
            except Exception as e:
                logger.warning("Pinterest: pin builder navigation failed: %s", e)

        # Double check: if still not on pin-builder/pin-creation, we are likely logged out
        if "pin-builder" not in page.url and "pin-creation" not in page.url:
            try:
                page.bring_to_front()
            except Exception:
                pass
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1000, 300)
                    time.sleep(0.1)
            except Exception:
                pass
            raise RuntimeError(
                f"Pinterest is not on the pin builder page (current URL: {page.url}). "
                "Please make sure you are logged into Pinterest manually in Opera GX first."
            )

        # -- 3. Upload image ---------------------------------------------
        logger.info("Pinterest: uploading image %s...", image_path)
        if not self._upload_image(page, image_path):
            raise RuntimeError(
                "Could not upload image to Pinterest pin builder. "
                "Check that the pin-builder page loaded correctly."
            )
        self._delay(3.5, 6.0)   # Pinterest needs time to process the upload

        # -- 4. Fill title -----------------------------------------------
        pin_title = title[:100]
        self._fill_title(page, pin_title)
        self._delay(0.5, 1.0)

        # -- 5. Fill description -----------------------------------------
        pin_desc = self._build_description(description, tags, redbubble_url, niche)
        self._fill_description(page, pin_desc)
        self._delay(0.5, 1.0)

        # -- 6. Destination link (the Redbubble URL) ---------------------
        if redbubble_url:
            self._fill_link(page, redbubble_url)
            self._delay(0.5, 1.0)

        # -- 7. Select board (routed by niche keywords) ------------------
        target_board = self._route_board(niche, title)
        self._select_board(page, target_board)
        self._delay(1.0, 2.0)

        # -- 8. Publish --------------------------------------------------
        logger.info("Pinterest: clicking Publish...")
        if not self._click_publish(page):
            raise RuntimeError("Could not find or click the Publish button on Pinterest.")

        # Wait for URL to leave pin-builder (success = redirect to the new pin)
        try:
            page.wait_for_url(
                lambda url: "pin-builder" not in url and "pinterest.com" in url,
                timeout=45_000,
            )
        except Exception:
            pass   # May stay on builder with a success toast - that's also fine

        self._delay(2.0, 3.0)
        pin_url = page.url
        logger.info("Pinterest: pin published! -> %s", pin_url[:120])

        # Park tab back at builder so next run starts cleanly
        try:
            page.goto(self.PIN_BUILDER_URL, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass

        return PinResult(success=True, pin_url=pin_url)

    # ------------------------------------------------------------------
    # Image upload
    # ------------------------------------------------------------------

    def _upload_image(self, page, image_path: str) -> bool:
        """Set the design image on Pinterest's pin builder."""
        abs_path = str(Path(image_path).resolve())
        # Expose any hidden file inputs first
        page.evaluate("""() => {
            document.querySelectorAll('input[type="file"]').forEach(inp => {
                inp.style.setProperty('display',    'block',   'important');
                inp.style.setProperty('visibility', 'visible', 'important');
                inp.style.setProperty('opacity',    '1',       'important');
                inp.style.setProperty('width',      '2px',     'important');
                inp.style.setProperty('height',     '2px',     'important');
            });
        }""")
        self._delay(0.3, 0.6)

        # Try direct set_input_files on any file input
        for sel in ('input[type="file"]', 'input[accept*="image"]'):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.set_input_files(abs_path)
                    logger.info("Pinterest: image set via '%s'.", sel)
                    return True
            except Exception as exc:
                logger.debug("Pinterest upload '%s' failed: %s", sel, exc)

        # Fallback: wait up to 15s for a file input to appear, then retry
        try:
            page.wait_for_selector('input[type="file"]', state="attached", timeout=15_000)
            page.evaluate("""() => {
                const inp = document.querySelector('input[type="file"]');
                if (inp) {
                    inp.style.setProperty('display',    'block',   'important');
                    inp.style.setProperty('visibility', 'visible', 'important');
                    inp.style.setProperty('opacity',    '1',       'important');
                }
            }""")
            page.locator('input[type="file"]').first.set_input_files(abs_path)
            logger.info("Pinterest: image uploaded via fallback wait.")
            return True
        except Exception as exc:
            logger.error("Pinterest: all image-upload strategies failed: %s", exc)
            self._screenshot("image_upload_fail")
            return False

    # ------------------------------------------------------------------
    # Field fillers
    # ------------------------------------------------------------------

    def _fill_title(self, page, title: str) -> None:
        selectors = [
            '[data-testid="pin-draft-title"]',
            '[data-test-id="pin-draft-title"]',
            'textarea[placeholder*="title" i]',
            'input[placeholder*="title" i]',
            '[aria-label*="title" i]',
            '[placeholder*="Add your title" i]',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(timeout=3000)
                    loc.fill(title)
                    logger.info("Pinterest: title filled.")
                    return
            except Exception:
                continue

        # JS fallback
        page.evaluate(
            """(t) => {
                for (const el of document.querySelectorAll(
                        'input, textarea, [contenteditable="true"]')) {
                    const hint = [
                        el.getAttribute('placeholder') || '',
                        el.getAttribute('aria-label')  || '',
                        el.getAttribute('data-testid') || '',
                    ].join(' ').toLowerCase();
                    if (/title/.test(hint)) {
                        el.focus();
                        if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) {
                            el.textContent = t;
                        } else {
                            const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                            const pd = Object.getOwnPropertyDescriptor(proto, 'value');
                            if (pd && pd.set) pd.set.call(el, t);
                            else el.value = t;
                        }
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }
                }
                return false;
            }""",
            t,
        )
        logger.debug("Pinterest: title fill attempted via JS.")

    def _fill_description(self, page, description: str) -> None:
        selectors = [
            '[data-testid="pin-draft-description"]',
            '[data-test-id="pin-draft-description"]',
            'textarea[placeholder*="Tell everyone" i]',
            'textarea[placeholder*="description" i]',
            '[aria-label*="description" i]',
            '[aria-label*="note" i]',
            'div[contenteditable="true"][data-testid*="description"]',
            'div[contenteditable="true"][data-test-id*="description"]',
            'div[class*="public-DraftEditor-content"]',
            'div[aria-label*="Pin is about" i]',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(timeout=3000)
                    loc.fill(description)
                    logger.info("Pinterest: description filled.")
                    return
            except Exception:
                continue

        # JS fallback for custom contenteditable/React inputs (with retry loop for slow SPA load)
        import time as _time_desc
        for attempt in range(3):
            filled = page.evaluate(
                """(desc) => {
                    for (const el of document.querySelectorAll('input, textarea, [contenteditable="true"]')) {
                        const hint = [
                            el.getAttribute('placeholder') || '',
                            el.getAttribute('aria-placeholder') || '',
                            el.getAttribute('aria-label')  || '',
                            el.getAttribute('data-testid') || '',
                            el.getAttribute('data-test-id') || '',
                            (el.parentElement ? el.parentElement.textContent : '') || '',
                        ].join(' ').toLowerCase();
                        if (/tell everyone|description|note|about/.test(hint)) {
                            el.focus();
                            if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) {
                                el.textContent = desc;
                            } else {
                                const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                                const pd = Object.getOwnPropertyDescriptor(proto, 'value');
                                if (pd && pd.set) pd.set.call(el, desc);
                                else el.value = desc;
                            }
                            el.dispatchEvent(new Event('input',  {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""",
                description,
            )
            if filled:
                logger.info("Pinterest: description filled via JS fallback.")
                return
            _time_desc.sleep(1.0)

        logger.warning("Pinterest: could not fill description field.")

    def _fill_link(self, page, url: str) -> None:
        selectors = [
            '[data-testid="pin-draft-link"]',
            '[data-test-id="pin-draft-link"]',
            'input[placeholder*="destination" i]',
            'input[placeholder*="website" i]',
            'input[placeholder*="link" i]',
            'input[placeholder*="Add a link" i]',
            '[aria-label*="link" i]',
            '[aria-label*="website" i]',
            'input[type="url"]',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(timeout=3000)
                    loc.fill(url)
                    logger.info("Pinterest: destination link filled: %s...", url[:60])
                    return
            except Exception:
                continue

        # JS fallback for custom contenteditable/React inputs (with retry loop for slow SPA load)
        import time as _time_link
        for attempt in range(3):
            filled = page.evaluate(
                """(link) => {
                    for (const el of document.querySelectorAll('input, textarea, [contenteditable="true"]')) {
                        const hint = [
                            el.getAttribute('placeholder') || '',
                            el.getAttribute('aria-placeholder') || '',
                            el.getAttribute('aria-label')  || '',
                            el.getAttribute('data-testid') || '',
                            el.getAttribute('data-test-id') || '',
                            (el.parentElement ? el.parentElement.textContent : '') || '',
                        ].join(' ').toLowerCase();
                        if (/destination|website|link|add a link/.test(hint)) {
                            el.focus();
                            if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) {
                                el.textContent = link;
                            } else {
                                const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                                const pd = Object.getOwnPropertyDescriptor(proto, 'value');
                                if (pd && pd.set) pd.set.call(el, link);
                                else el.value = link;
                            }
                            el.dispatchEvent(new Event('input',  {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""",
                url,
            )
            if filled:
                logger.info("Pinterest: destination link filled via JS fallback.")
                return
            _time_link.sleep(1.0)

        logger.warning("Pinterest: could not fill destination link.")

    # ------------------------------------------------------------------
    # One-time board setup
    # ------------------------------------------------------------------

    def setup_boards(self) -> dict:
        """
        Create all themed Pinterest boards in one go.

        Call this once via:  python main.py --setup-pinterest

        Returns
        -------
        dict  {board_name: "created" | "exists" | "failed"}
        """
        results: dict = {}
        boards = self.all_board_names()
        logger.info("Pinterest setup: creating %d themed boards...", len(boards))

        try:
            self._connect()
        except Exception as exc:
            logger.error("Pinterest setup: browser connect failed: %s", exc)
            return {"error": str(exc)}

        try:
            page = self._page
            for board_name, board_desc in boards:
                logger.info("Pinterest setup: creating board '%s'...", board_name)
                status = self._ensure_board_exists(page, board_name, board_desc)
                results[board_name] = status
                self._delay(2.0, 3.5)
        finally:
            self._disconnect()

        ok  = sum(1 for v in results.values() if v in ("created", "exists"))
        err = sum(1 for v in results.values() if v == "failed")
        logger.info(
            "Pinterest setup complete: %d boards ready, %d failed.", ok, err
        )
        return results

    def _ensure_board_exists(self, page, board_name: str, board_desc: str = "") -> str:
        """
        Navigate to board creation if the board doesn't exist yet.

        Returns "created", "exists", or "failed".
        """
        # Quick check - navigate to Pinterest boards list
        try:
            page.goto(
                "https://www.pinterest.com/boards/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            self._delay(1.5, 2.5)

            # If board already listed on page, skip creation
            if board_name.lower()[:20] in page.content().lower():
                logger.info("Board '%s' already exists - skipping.", board_name)
                return "exists"
        except Exception:
            pass   # Can't check - attempt creation anyway

        # Navigate to board creation page
        try:
            page.goto(
                "https://www.pinterest.com/board/create/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            self._delay(1.5, 2.5)
        except Exception as exc:
            logger.warning("Board creation nav failed: %s", exc)
            return "failed"

        # Fill board name
        name_filled = False
        for sel in (
            'input[name="boardName"]',
            'input[placeholder*="Name" i]',
            'input[placeholder*="board name" i]',
            '[data-testid="board-name-input"]',
            '[data-test-id="board-name-input"]',
            'input[id*="board" i]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(timeout=3000)
                    loc.fill(board_name)
                    name_filled = True
                    logger.info("Board name '%s' filled.", board_name)
                    break
            except Exception:
                continue

        if not name_filled:
            logger.warning("Could not fill board name for '%s'.", board_name)
            return "failed"

        self._delay(0.5, 1.0)

        # Click Create / Save
        created = False
        for sel in (
            'button:has-text("Create")',
            'button[type="submit"]',
            '[data-testid="board-create-button"]',
            '[data-test-id="board-create-button"]',
            'button:has-text("Done")',
            'button:has-text("Save")',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    loc.click(force=True, timeout=3000)
                    created = True
                    logger.info("Board '%s' created!", board_name)
                    break
            except Exception:
                continue

        if not created:
            # JS fallback
            clicked = page.evaluate("""() => {
                for (const b of document.querySelectorAll('button')) {
                    const t = (b.textContent||'').trim().toLowerCase();
                    if (t === 'create' || t === 'done' || t === 'save') {
                        b.click(); return t;
                    }
                }
                return null;
            }""")
            if clicked:
                logger.info("Board '%s' create clicked via JS.", board_name)
                created = True

        self._delay(1.5, 2.5)
        return "created" if created else "failed"

    # ------------------------------------------------------------------
    # Board selection
    # ------------------------------------------------------------------

    def _select_board(self, page, board_name: str) -> None:
        """Open the board dropdown, search for board_name, click it.
        If the board doesn't exist in the dropdown, create it on the fly."""
        # -- Open the dropdown --------------------------------------------
        opened = False
        open_selectors = [
            '[data-testid="board-dropdown-select-button"]',
            '[data-test-id="board-dropdown-select-button"]',
            'button:has-text("Choose a board")',
            'button:has-text("Select")',
            '[aria-label*="board" i]',
            '[aria-haspopup="listbox"]',
        ]
        for sel in open_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(timeout=3000)
                    self._delay(1.0, 2.0)
                    opened = True
                    logger.info("Pinterest: board dropdown opened.")
                    break
            except Exception:
                continue

        if not opened:
            # Sibling-lookup JS fallback: find the Publish button, then locate the board dropdown next to it
            opened = page.evaluate(
                """() => {
                    let publishBtn = null;
                    for (const b of document.querySelectorAll('button, [role="button"], div')) {
                        const txt = (b.textContent || '').trim().toLowerCase();
                        if (txt === 'publish' || txt === 'save' || txt === 'done') {
                            publishBtn = b;
                            break;
                        }
                    }
                    if (publishBtn) {
                        let parent = publishBtn.parentElement;
                        while (parent && parent !== document.body) {
                            const buttons = Array.from(parent.querySelectorAll('button, [role="button"]'));
                            const dropdown = buttons.find(b => b !== publishBtn && (
                                b.getAttribute('aria-haspopup') || 
                                b.querySelector('svg') || 
                                /select|board|choose/i.test(b.textContent || b.ariaLabel || '') ||
                                buttons.length === 2
                            ));
                            if (dropdown) {
                                dropdown.click();
                                return true;
                            }
                            parent = parent.parentElement;
                        }
                    }
                    return false;
                }"""
            )
            if opened:
                logger.info("Pinterest: board dropdown opened via sibling-lookup JS fallback.")
                self._delay(1.0, 2.0)

        if not opened:
            logger.warning("Pinterest: could not open board dropdown - skipping.")
            return

        # -- Type to search for the board ---------------------------------
        search_selectors = [
            'input[aria-label*="boards" i]',
            'input[aria-label*="Search through your boards" i]',
            '[role="listbox"] input',
            '[role="menu"] input',
            'input[placeholder*="Search board" i]',
            '[data-testid="board-search-input"]',
            '[data-test-id="board-search-input"]',
        ]
        search_filled = False
        for sel in search_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(board_name)
                    self._delay(1.0, 1.5)
                    search_filled = True
                    break
            except Exception:
                continue

        if not search_filled:
            # JS fallback to find search input inside popover/listbox
            search_filled = page.evaluate(
                """(name) => {
                    const popover = document.querySelector('[role="listbox"], [role="menu"], [class*="popover"], [class*="dropdown"], div[class*="menu" i]');
                    if (popover) {
                        const input = popover.querySelector('input');
                        if (input) {
                            input.focus();
                            input.value = name;
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    // Fallback to any input on page that is visible, excluding global headers/navs/search boxes
                    for (const inp of document.querySelectorAll('input')) {
                        let isGlobal = false;
                        let parent = inp.parentElement;
                        while (parent) {
                            const tagName = parent.tagName.toLowerCase();
                            const className = (parent.className && typeof parent.className === 'string') ? parent.className : '';
                            const id = parent.id || '';
                            if (tagName === 'header' || tagName === 'nav' || 
                                className.includes('header') || className.includes('nav') || 
                                className.includes('SearchBox') || id.includes('header') || id.includes('search')) {
                                isGlobal = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (isGlobal) continue;
                        const txt = (inp.placeholder || inp.ariaLabel || '').toLowerCase();
                        if (/search|board/i.test(txt)) {
                            inp.focus();
                            inp.value = name;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    return false;
                }""",
                board_name,
            )
            if search_filled:
                logger.info("Pinterest: board search filled via JS fallback.")
                self._delay(1.0, 1.5)

        # -- Click matching board ------------------------------------------
        board_item_selectors = [
            f'[data-testid*="board-option"]:has-text("{board_name}")',
            f'[data-test-id*="board-option"]:has-text("{board_name}")',
            f'li:has-text("{board_name}")',
            f'div[role="option"]:has-text("{board_name}")',
            f'button:has-text("{board_name}")',
        ]
        selected = False
        selected_res = None
        for sel in board_item_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    loc.click(timeout=3000)
                    selected = True
                    logger.info("Pinterest: board '%s' selected.", board_name)
                    break
            except Exception:
                continue

        if not selected:
            # Try to click "Create board" button in the dropdown via Playwright locator first
            for create_btn_sel in (
                '[role="button"]:has-text("Create board")',
                'div[role="button"]:has-text("Create board")',
                'button:has-text("Create board")',
                '[data-testid*="create-board"]',
                '[data-test-id*="create-board"]',
            ):
                try:
                    loc = page.locator(create_btn_sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=2_000):
                        loc.click(timeout=2000)
                        logger.info("Pinterest: clicked 'Create board' button via selector '%s'.", create_btn_sel)
                        selected_res = "create_clicked"
                        break
                except Exception:
                    continue

        if not selected and not selected_res:
            # JS fallback to find matching board item by text or click "Create board"
            selected_res = page.evaluate(
                """(name) => {
                    const cleanName = name.toLowerCase().trim();
                    const container = document.querySelector('[role="listbox"], [role="menu"], [class*="popover"], [class*="dropdown"], div[class*="menu" i]');
                    const elements = container ? Array.from(container.querySelectorAll('*')) : Array.from(document.querySelectorAll('[role="option"], li, button, div'));
                    
                    // 1. Try exact match
                    let matched = elements.find(el => (el.textContent || '').trim().toLowerCase() === cleanName);
                    
                    // 2. Try substring match on option-like elements
                    if (!matched) {
                        matched = elements.find(el => {
                            const role = el.getAttribute('role');
                            const tagName = el.tagName.toLowerCase();
                            const isOption = role === 'option' || tagName === 'li' || tagName === 'button' || el.classList.contains('board-option');
                            if (!isOption) return false;
                            const txt = (el.textContent || '').trim().toLowerCase();
                            return txt && (txt.includes(cleanName) || cleanName.includes(txt));
                        });
                    }

                    // 2.5. Try substring match on any leaf or button-like elements in the dropdown
                    if (!matched) {
                        matched = elements.find(el => {
                            const txt = (el.textContent || '').trim().toLowerCase();
                            const cleanedTxt = txt.replace('publish', '').replace('save', '').trim();
                            return txt === cleanName || cleanedTxt === cleanName || txt.includes(cleanName);
                        });
                    }
                    
                    if (matched) {
                        matched.click();
                        return 'matched';
                    }
                    
                    // 3. Try to find the "Create board" button in the dropdown
                    const createBtn = elements.find(el => {
                        const testId = el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '';
                        const txt = (el.textContent || '').trim().toLowerCase();
                        return testId.includes('create-board') || txt === 'create board';
                    });
                    if (createBtn) {
                        const clickTarget = createBtn.closest('button, [role="button"]') || createBtn;
                        clickTarget.focus();
                        clickTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                        clickTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                        clickTarget.click();
                        return 'create_clicked';
                    }
                    
                    return null;
                }""",
                board_name,
            )
            if selected_res == "matched":
                selected = True
                logger.info("Pinterest: board selected via text-match JS fallback.")

        if selected_res == "create_clicked":
            logger.info("Pinterest: clicked 'Create board' in dropdown. Waiting for creation modal...")
            self._delay(2.0, 3.0)
            
            # Fill name and click create in the modal via Playwright locator first
            modal_filled = False
            for modal_inp_sel in (
                'input[name="boardName"]',
                'input[placeholder*="Name" i]',
                '[data-test-id="board-name-input"]',
                '[data-testid="board-name-input"]',
            ):
                try:
                    loc = page.locator(modal_inp_sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=3_000):
                        loc.fill(board_name)
                        modal_filled = True
                        logger.info("Pinterest: board creation modal name filled.")
                        break
                except Exception:
                    continue

            if modal_filled:
                for confirm_sel in (
                    'button:has-text("Create")',
                    'button:has-text("Done")',
                    'button:has-text("Save")',
                    '[data-test-id="board-create-button"]',
                    '[data-testid="board-create-button"]',
                    '[role="button"]:has-text("Create")',
                ):
                    try:
                        cloc = page.locator(confirm_sel).first
                        if cloc.count() > 0 and cloc.is_visible(timeout=3_000):
                            cloc.click(timeout=3000)
                            logger.info("Pinterest: board '%s' created and selected via creation modal.", board_name)
                            selected = True
                            self._delay(2.0, 3.0)
                            break
                    except Exception:
                        continue

            if not selected:
                # Fallback to JS modal filling and clicking
                created = page.evaluate(
                    """(name) => {
                        const modalInput = document.querySelector('input[name="boardName"], input[placeholder*="Name" i], input[id*="board" i], [data-testid="board-name-input"], [data-test-id="board-name-input"]');
                        if (modalInput) {
                            modalInput.focus();
                            modalInput.value = name;
                            modalInput.dispatchEvent(new Event('input', { bubbles: true }));
                            modalInput.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        const createBtn = Array.from(document.querySelectorAll('button, [role="button"], div')).find(b => {
                            const txt = (b.textContent || '').trim().toLowerCase();
                            return txt === 'create' || txt === 'done' || txt === 'save';
                        });
                        if (createBtn) {
                            const clickTarget = createBtn.closest('button, [role="button"]') || createBtn;
                            clickTarget.focus();
                            clickTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                            clickTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                            clickTarget.click();
                            return true;
                        }
                        return false;
                    }""",
                    board_name,
                )
                if created:
                    logger.info("Pinterest: board '%s' created and selected via creation modal JS.", board_name)
                    self._delay(2.0, 3.0)
                    selected = True

        if not selected:
            # Check if Pinterest shows a "Create [board_name]" option in the dropdown
            create_option_selected = False
            for create_sel in (
                f'[data-testid*="create"]:has-text("{board_name}")',
                f'[data-test-id*="create"]:has-text("{board_name}")',
                f'button:has-text("Create \\"{board_name}\\"")',
                f'li:has-text("Create")',
                f'[role="option"]:has-text("Create")',
            ):
                try:
                    loc = page.locator(create_sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=2_000):
                        loc.click(timeout=2000)
                        logger.info(
                            "Pinterest: creating board '%s' via dropdown.", board_name
                        )
                        self._delay(2.0, 3.0)
                        # After click, Pinterest may open a mini creation form
                        # - submit it if a Create button appears
                        for confirm_sel in (
                            'button:has-text("Create")',
                            '[data-testid="board-create-button"]',
                            '[data-test-id="board-create-button"]',
                            'button[type="submit"]',
                        ):
                            try:
                                cloc = page.locator(confirm_sel).first
                                if cloc.count() > 0 and cloc.is_visible(timeout=3_000):
                                    cloc.click(force=True, timeout=3000)
                                    self._delay(1.5, 2.5)
                                    create_option_selected = True
                                    logger.info("Board '%s' created and selected.", board_name)
                                    break
                            except Exception:
                                continue
                        if create_option_selected:
                            selected = True
                            break
                except Exception:
                    continue

            if not create_option_selected:
                # Last resort: click the first available option in the dropdown via JS
                fallback_selected = page.evaluate(
                    """() => {
                        const container = document.querySelector('[role="listbox"], [role="menu"], [class*="popover"], [class*="dropdown"], div[class*="menu" i]');
                        if (container) {
                            const opt = container.querySelector('[role="option"], li, button');
                            if (opt) {
                                opt.click();
                                return true;
                            }
                        }
                        const opt = document.querySelector('[role="option"]');
                        if (opt) {
                            opt.click();
                            return true;
                        }
                        return false;
                    }"""
                )
                if fallback_selected:
                    logger.info("Pinterest: selected first available board via fallback JS.")
                    selected = True
                else:
                    for fallback in (
                        '[data-testid*="board-option"]',
                        '[data-test-id*="board-option"]',
                        '[role="option"]',
                        'li[data-test-id]',
                    ):
                        try:
                            loc = page.locator(fallback).first
                            if loc.count() > 0 and loc.is_visible(timeout=2_000):
                                loc.click(timeout=2000)
                                logger.info(
                                    "Pinterest: selected first available board (fallback)."
                                )
                                selected = True
                                break
                        except Exception:
                            continue

    # ------------------------------------------------------------------
    # Publish button
    # ------------------------------------------------------------------

    def _click_publish(self, page) -> bool:
        publish_selectors = [
            '[data-testid="board-dropdown-save-button"]',
            '[data-test-id="board-dropdown-save-button"]',
            '[data-testid="pin-draft-save-button"]',
            '[data-test-id="pin-draft-save-button"]',
            'button:has-text("Publish")',
            'button:has-text("Save")',
            'button[type="submit"]',
        ]
        for sel in publish_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    loc.click(force=True, timeout=3000)
                    logger.info("Pinterest: publish clicked (%s).", sel)
                    return True
            except Exception:
                continue

        # Robust JS fallback: support Gestalt design system (div[role="button"]) and leaf-text nodes
        clicked = page.evaluate(
            """() => {
                // Phase 1: Try standard buttons, role="button", and divs with button/publish classes
                const elements = Array.from(document.querySelectorAll(
                    'button, [role="button"], div[class*="publish" i], div[class*="button" i]'
                ));
                for (const el of elements) {
                    const txt = (el.textContent || '').trim().toLowerCase();
                    if (txt === 'publish' || txt === 'save' || txt === 'done') {
                        el.click();
                        return 'button:' + txt;
                    }
                }
                
                // Phase 2: Try any innermost leaf elements containing exactly "publish" or "save"
                for (const el of document.querySelectorAll('*')) {
                    if (el.children.length === 0) {
                        const txt = (el.textContent || '').trim().toLowerCase();
                        if (txt === 'publish' || txt === 'save') {
                            el.click();
                            return 'leaf:' + txt;
                        }
                    }
                }
                return null;
            }"""
        )
        if clicked:
            logger.info("Pinterest: publish clicked via JS fallback ('%s').", clicked)
            return True

        self._screenshot("publish_btn_not_found")
        logger.error("Pinterest: could not find Publish button.")
        return False

    # ------------------------------------------------------------------
    # Description builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_description(
        description: str,
        tags: list,
        redbubble_url: str,
        niche: str,
    ) -> str:
        """
        Craft a Pinterest-optimised description that drives Redbubble sales.

        Structure
        ---------
          [SEO description - first 300 chars]

          Shop this design -> [Redbubble URL]

          #tag1 #tag2 ... (up to 15 hashtags)

        Pinterest pins with a destination link + hashtags rank in both
        Pinterest search AND Google image search, giving double exposure.
        """
        desc = (description or "").strip()[:300]

        # Build hashtags: niche first, then tags
        raw_tags = []
        if niche:
            raw_tags.append(niche)
        raw_tags.extend(tags[:15])

        hashtags_parts = []
        seen_ht: set = set()
        for t in raw_tags:
            ht = re.sub(r"[^a-zA-Z0-9]", "", t.replace(" ", "")).lower()
            if ht and ht not in seen_ht:
                hashtags_parts.append(f"#{ht}")
                seen_ht.add(ht)
            if len(hashtags_parts) >= 15:
                break
        hashtags = " ".join(hashtags_parts)

        parts = [desc]
        if redbubble_url:
            parts.append(f"\n\n[Shop] Shop this design -> {redbubble_url}")
        if hashtags:
            parts.append(f"\n\n{hashtags}")

        return "".join(parts)[:500]   # Pinterest description cap

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _screenshot(self, label: str = "error") -> str:
        try:
            if self._page:
                path = str(
                    self.screenshot_dir / f"pinterest_{label}_{int(time.time())}.png"
                )
                self._page.screenshot(path=path, full_page=True)
                logger.info("Pinterest screenshot: %s", path)
                return path
        except Exception as exc:
            logger.warning("Pinterest screenshot failed: %s", exc)
        return ""

    @staticmethod
    def _delay(min_s: float, max_s: float) -> None:
        time.sleep(random.uniform(min_s, max_s))
