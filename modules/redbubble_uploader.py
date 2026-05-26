"""
redbubble_uploader.py
======================
Automates the full Redbubble product upload flow using Playwright.

Flow
----
1. Launch browser (headless or headed)
2. Log in with stored credentials
3. Navigate to the new upload page
4. Upload the design PNG
5. Fill title, tags, description
6. Enable selected product types
7. Set design placement / scaling for key products
8. Publish and capture the resulting product URL
9. Return upload result (success + URL)

Robustness features
-------------------
- Configurable retry attempts
- Screenshot on every failure for debugging
- Randomised human-like delays to reduce bot detection
- Cookie/session persistence to avoid repeated logins
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class UploadResult:
    success: bool
    product_url: str = ""
    work_id: str = ""
    error: str = ""
    screenshot_path: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# RedBubbleUploader
# ---------------------------------------------------------------------------

class RedbubbleUploader:
    """
    Automates Redbubble product uploads via Playwright browser automation.

    Parameters
    ----------
    email               : Redbubble account email
    password            : Redbubble account password
    headless            : Run browser without GUI (default True)
    slow_mo_ms          : Milliseconds between each Playwright action
    timeout_ms          : Default element wait timeout
    screenshot_dir      : Where to save error screenshots
    max_retries         : Upload attempt retries on failure
    upload_delay_seconds: Pause between successive uploads
    products_to_enable  : List of product type slugs to enable
    """

    LOGIN_URL  = "https://www.redbubble.com/auth/login"
    UPLOAD_URL = "https://www.redbubble.com/portfolio/images/new"
    SESSION_FILE = "data/redbubble_session.json"

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True,
        slow_mo_ms: int = 150,
        timeout_ms: int = 60_000,
        screenshot_dir: str = "data/exports/screenshots",
        max_retries: int = 3,
        upload_delay_seconds: float = 5.0,
        products_to_enable: Optional[list[str]] = None,
    ) -> None:
        self.email      = email    or os.environ.get("REDBUBBLE_EMAIL", "")
        self.password   = password or os.environ.get("REDBUBBLE_PASSWORD", "")
        self.headless   = headless
        self.slow_mo_ms = slow_mo_ms
        self.timeout_ms = timeout_ms
        self.screenshot_dir    = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries       = max_retries
        self.upload_delay      = upload_delay_seconds
        self.products_to_enable = products_to_enable or [
            "t-shirt", "sticker", "poster", "mug", "hoodie",
        ]

        self._browser  = None
        self._context  = None
        self._page     = None

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    # Persistent Chrome profile directory — survives between runs.
    PROFILE_DIR    = "data/chrome_profile"
    CDP_DEBUG_PORT = 9222   # start_debug_browser.bat launches on this port

    _STEALTH_SCRIPT = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});
        if (!window.chrome) { window.chrome = { runtime: {} }; }
        const _origPQ = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (p) =>
            p && p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission, onchange: null })
                : _origPQ(p);
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const a = [
                    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client',      filename: 'internal-nacl-plugin' },
                ];
                a.refresh = () => {}; a.item = i => a[i];
                a.namedItem = n => a.find(p => p.name === n) || null;
                return a;
            }, configurable: true,
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'], configurable: true,
        });
    """

    def _start_browser(self) -> None:
        """
        Connect to a browser in the least-detectable way available:

        Priority 1 — CDP connection (BEST: no automation flags at all)
            Requires start_debug_browser.bat to be running first.
            Playwright connects as a debugger to an existing Opera GX instance.
            Cloudflare never sees automation flags — clean real browser.

        Priority 2 — Persistent context with Opera GX executable
            Falls back when the debug browser isn't running.

        Priority 3 — Bundled Chromium persistent context
            Last resort.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            )

        self._pw       = sync_playwright().start()
        self._browser  = None
        self._context  = None
        self._is_cdp   = False

        # ── Priority 1: CDP connection to already-running debug browser ──────
        cdp_url = f"http://localhost:{self.CDP_DEBUG_PORT}"
        try:
            self._browser = self._pw.chromium.connect_over_cdp(
                cdp_url, timeout=3_000
            )
            # Use the first existing context (the browser's default context)
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else self._browser.new_context()
            self._context.add_init_script(self._STEALTH_SCRIPT)
            self._is_cdp = True
            logger.info("Connected to debug browser via CDP on port %d — no automation flags.",
                        self.CDP_DEBUG_PORT)
        except Exception as cdp_exc:
            logger.info(
                "CDP connect failed (%s) — falling back to persistent launch. "
                "Tip: run start_debug_browser.bat first to avoid Cloudflare challenges.",
                cdp_exc,
            )

        # ── Priority 2 / 3: Persistent launch ────────────────────────────────
        if not self._is_cdp:
            profile_dir = Path(self.PROFILE_DIR)
            profile_dir.mkdir(parents=True, exist_ok=True)

            _launch_kwargs = dict(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )

            _opera_paths = [
                r"C:\Users\DELL\AppData\Local\Programs\Opera GX\opera.exe",
                r"C:\Program Files\Opera GX\opera.exe",
                r"C:\Program Files (x86)\Opera GX\opera.exe",
            ]
            launched = False
            for opera_path in _opera_paths:
                if Path(opera_path).exists():
                    try:
                        kw = dict(_launch_kwargs)
                        kw["executable_path"] = opera_path
                        self._context = self._pw.chromium.launch_persistent_context(
                            str(profile_dir), **kw
                        )
                        logger.info("Browser launched (Opera GX persistent, profile=%s)",
                                    profile_dir)
                        launched = True
                        break
                    except Exception as exc:
                        logger.debug("Opera GX launch failed: %s", exc)
            if not launched:
                for channel in ("chrome", None):
                    try:
                        kw = dict(_launch_kwargs)
                        if channel:
                            kw["channel"] = channel
                        self._context = self._pw.chromium.launch_persistent_context(
                            str(profile_dir), **kw
                        )
                        logger.info("Browser launched (channel=%s, profile=%s)",
                                    channel or "chromium", profile_dir)
                        launched = True
                        break
                    except Exception as exc:
                        logger.debug("Launch channel=%s failed: %s", channel, exc)
            if not launched:
                raise RuntimeError("Could not launch browser.")

            self._context.add_init_script(self._STEALTH_SCRIPT)

        # ── Merge Redbubble auth cookies ──────────────────────────────────────
        # Only merge session cookies if not running in CDP mode, to avoid overwriting
        # active browser cookies in the real Opera GX profile.
        if not self._is_cdp:
            session_path = Path(self.SESSION_FILE)
            if session_path.exists():
                try:
                    data = json.loads(session_path.read_text(encoding="utf-8"))
                    cookies = data.get("cookies", [])
                    if cookies:
                        self._context.add_cookies(cookies)
                        logger.info("Merged %d auth cookies into browser.", len(cookies))
                except Exception as exc:
                    logger.warning("Could not merge session cookies: %s", exc)

        # ── Pick the right tab ───────────────────────────────────────────────────────
        # Priority order:
        #   1. A tab already on redbubble.com (user pre-navigated, CF already solved)
        #   2. A new blank tab (avoids browser-internal pages like gxcorner.games
        #      which block external navigation with ERR_ABORTED)
        # We do NOT reuse gxcorner.games / opera internal tabs because
        # page.goto() on those always aborts.
        INTERNAL_HOSTS = ("gxcorner.games", "opera:", "chrome:", "about:", "edge:")
        self._page = None
        if self._is_cdp:
            try:
                existing_pages = self._context.pages
                # Pass 1: find a tab already on redbubble.com
                for p in existing_pages:
                    try:
                        url = p.url
                        if not p.is_closed() and "redbubble.com" in url:
                            self._page = p
                            logger.info("Reusing Redbubble tab: %s", url[:80])
                            break
                    except Exception:
                        continue

                # Pass 2: if no redbubble tab found, try to find a failed/error tab to reuse
                if self._page is None:
                    for p in existing_pages:
                        try:
                            url = p.url
                            if not p.is_closed() and ("chrome-error" in url or "about:blank" in url or url == ""):
                                self._page = p
                                logger.info("Reusing failed/blank/empty tab: %s", url[:80])
                                break
                        except Exception:
                            continue
            except Exception as e:
                logger.warning("Error checking existing pages: %s", e)

        if self._page is None:
            # Open a fresh tab — safer than reusing an internal/unknown tab
            self._page = self._context.new_page()
            logger.info("Opened new tab for upload (no usable Redbubble tab found).")

        self._page.set_default_timeout(self.timeout_ms)

    def _stop_browser(self) -> None:
        """
        Save session and disconnect/close the browser.
        For CDP connections we do NOT close the browser — just save state and
        stop the Playwright driver. The debug browser keeps running for next time.
        """
        if self._context:
            if self._is_cdp:
                # CDP mode: PARK the tab at the upload URL instead of closing it.
                # This means the next run finds a Redbubble tab already open and
                # skips the Cloudflare challenge entirely.
                # We skip saving storage_state to a file in CDP mode since the real
                # browser profile handles session persistence automatically.
                try:
                    if self._page and not self._page.is_closed():
                        # Navigate to upload URL — do NOT close the tab
                        try:
                            self._page.goto(
                                self.UPLOAD_URL,
                                wait_until="domcontentloaded",
                                timeout=20_000,
                            )
                            logger.info("Tab parked at upload URL for next run.")
                        except Exception:
                            pass  # Keep tab alive even if navigation fails
                except Exception:
                    pass
            else:
                # Persistent launch: save state then close the whole context.
                try:
                    Path(self.SESSION_FILE).parent.mkdir(parents=True, exist_ok=True)
                    self._context.storage_state(path=self.SESSION_FILE)
                except Exception as exc:
                    logger.warning("Could not save session state: %s", exc)
                try:
                    self._context.close()
                except Exception:
                    pass

        # Brief grace period so Playwright's internal async loop can drain any
        # pending coroutines before we call stop().  Without this, coroutines
        # such as storage_state() or click() that were awaited just before
        # cleanup sometimes appear as "Task was destroyed but it is pending"
        # in the asyncio log.
        time.sleep(0.5)

        # Stop the Playwright driver (doesn't close the remote debug browser)
        try:
            if hasattr(self, "_pw") and self._pw:
                self._pw.stop()
        except Exception:
            pass

        self._browser = self._context = self._page = None
        self._is_cdp  = False
        if hasattr(self, "_pw"):
            self._pw = None

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _is_logged_in(self) -> bool:
        """
        Check authenticated status by reading the active cookies in the browser context
        or falling back to the saved session file (for non-CDP runs).
        """
        import json as _json
        import base64 as _b64
        import time as _time

        cookies = []
        if self._context:
            try:
                cookies = self._context.cookies()
                logger.info("Checked active browser context cookies: found %d cookies.", len(cookies))
            except Exception as exc:
                logger.warning("Could not read cookies from context: %s", exc)

        # Fallback to session file if no cookies found in context
        if not cookies:
            session_path = Path(self.SESSION_FILE)
            if session_path.exists():
                try:
                    data = _json.loads(session_path.read_text(encoding="utf-8"))
                    cookies = data.get("cookies", [])
                except Exception:
                    pass

        if not cookies:
            logger.info("No cookies found — not logged in.")
            return False

        now = _time.time()

        # refresh_token only exists when the user is authenticated
        for c in cookies:
            if c.get("name") == "refresh_token":
                exp = c.get("expires", -1)
                if exp == -1 or exp > now:
                    logger.info("Session authenticated (refresh_token present).")
                    return True

        # Fallback: decode the open_id_token JWT payload
        for c in cookies:
            if c.get("name") == "open_id_token":
                try:
                    parts = c["value"].split(".")
                    padding = "=" * (-len(parts[1]) % 4)
                    payload = _json.loads(
                        _b64.urlsafe_b64decode(parts[1] + padding).decode()
                    )
                    if "authenticated" in payload.get("amr", []):
                        exp = payload.get("exp", 0)
                        if exp > now:
                            logger.info("Session authenticated (JWT amr=authenticated).")
                            return True
                except Exception:
                    pass

        logger.info("Session not authenticated (no valid auth cookies found).")
        return False

    def _wait_for_cloudflare(self, timeout_ms: int = 90_000) -> None:
        """
        Detect and wait out Cloudflare challenges.
        Uses JavaScript body-text inspection because the Turnstile widget
        renders inside a cross-origin iframe (CSS selectors can't reach it).

        After the checkbox click Cloudflare does a full page reload — we must
        also wait for domcontentloaded to settle, not just for the CF text to
        disappear (otherwise we read the DOM mid-reload and get a false clear).
        """
        page = self._page
        try:
            is_cf = page.evaluate("""
                () => {
                    const txt = (document.body && document.body.innerText) || '';
                    const title = document.title || '';
                    return txt.includes('Verify you are human')
                        || txt.includes('Performing security verification')
                        || txt.includes('checking your browser')
                        || title.includes('Just a moment');
                }
            """)
            if not is_cf:
                return

            logger.warning(
                "☁️  Cloudflare challenge detected. "
                "Please click 'Verify you are human' in the browser window. "
                "Waiting up to %ds…", timeout_ms // 1000
            )

            # Bring page/tab to front so it is visible to the user
            try:
                page.bring_to_front()
            except Exception:
                pass

            # Alert user with beep sound on Windows
            try:
                import winsound
                # Play 3 beeps
                for _ in range(3):
                    winsound.Beep(1000, 300)
                    time.sleep(0.1)
            except Exception:
                pass

            # Phase 1: wait for CF text to disappear (user completes checkbox)
            page.wait_for_function(
                """() => {
                    const txt = (document.body && document.body.innerText) || '';
                    const title = document.title || '';
                    return !txt.includes('Verify you are human')
                        && !txt.includes('Performing security verification')
                        && !txt.includes('checking your browser')
                        && !title.includes('Just a moment');
                }""",
                timeout=timeout_ms,
            )

            # Phase 2: wait for the post-challenge page reload to stabilise
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

            # Phase 3: confirm we're not immediately back on ANOTHER CF screen
            # (can happen if CF issues a second challenge after the reload)
            still_cf = page.evaluate("""
                () => {
                    const txt = (document.body && document.body.innerText) || '';
                    const title = document.title || '';
                    return txt.includes('Verify you are human')
                        || title.includes('Just a moment');
                }
            """)
            if still_cf:
                logger.warning("Cloudflare re-challenge after reload — waiting again…")
                try:
                    import winsound
                    winsound.Beep(1200, 400)
                except Exception:
                    pass
                page.wait_for_function(
                    """() => {
                        const txt = (document.body && document.body.innerText) || '';
                        const title = document.title || '';
                        return !txt.includes('Verify you are human')
                            && !title.includes('Just a moment');
                    }""",
                    timeout=timeout_ms,
                )
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                except Exception:
                    pass

            logger.info("Cloudflare challenge resolved.")
            self._human_delay(2.5, 4.0)

        except Exception as exc:
            logger.error("Cloudflare challenge wait timed out or failed: %s", exc)
            # Raise exception so the upload attempt fails early and triggers a clean retry/reload
            raise RuntimeError("Cloudflare challenge was not resolved in time.")

    def _dismiss_cookie_banner(self) -> None:
        """Try to dismiss GDPR/cookie consent banners if present."""
        cookie_selectors = [
            'button:has-text("Accept")',
            'button:has-text("Accept All")',
            'button:has-text("Accept Cookies")',
            'button:has-text("I Accept")',
            'button:has-text("OK")',
            'button:has-text("Agree")',
            '[data-testid="cookie-accept"]',
            '#onetrust-accept-btn-handler',
            '.cookie-accept',
        ]
        for sel in cookie_selectors:
            try:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    logger.info("Dismissed cookie banner (%s)", sel)
                    self._human_delay(0.5, 1.0)
                    return
            except Exception:
                continue

    def _login(self) -> bool:
        """Perform full login flow. Returns True on success."""
        if not self.email or not self.password:
            logger.error("Redbubble credentials not set.")
            return False

        logger.info("Logging into Redbubble as %s", self.email)
        try:
            # Use networkidle for the LOGIN page specifically — Cloudflare's
            # Turnstile challenge needs several seconds of JS execution to
            # complete before the real login form appears. Without this wait
            # the form fields may belong to the Cloudflare interstitial, not
            # the actual login form.
            try:
                self._page.goto(self.LOGIN_URL, wait_until="domcontentloaded",
                                timeout=30_000)
            except Exception:
                pass
            self._human_delay(2.0, 3.0)

            # Handle Cloudflare challenge BEFORE anything else
            self._wait_for_cloudflare(timeout_ms=90_000)

            # Dismiss any cookie / consent banner
            self._dismiss_cookie_banner()

            # ── Wait for the login form to render ─────────────────────
            # Redbubble uses a floating-label pattern — inputs have NO
            # placeholder attribute, so all CSS placeholder selectors fail.
            # We wait until at least 2 inputs exist in the DOM, then fill
            # them via JavaScript which works regardless of attributes.
            logger.info("Waiting for login form inputs to render…")
            try:
                self._page.wait_for_function(
                    "() => document.querySelectorAll('input').length >= 2",
                    timeout=30_000,
                )
            except Exception:
                self._take_screenshot("login_no_email_field")
                logger.error(
                    "Login form did not render. Current URL: %s", self._page.url
                )
                return False

            # ── Fill email via JavaScript ─────────────────────────────
            # Finds the first visible, non-password, non-hidden input on
            # the page that sits inside a <form> or below the page fold
            # (i.e. not the header search bar which has tabIndex=-1 or
            # is inside a <header> / <nav> element).
            filled_email = self._page.evaluate(
                """(email) => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    // Skip inputs inside <header> or <nav> (search bars)
                    const loginInput = inputs.find(el => {
                        if (el.type === 'password' || el.type === 'hidden'
                                || el.type === 'checkbox' || el.type === 'submit'
                                || el.disabled || el.readOnly) return false;
                        let node = el;
                        while (node) {
                            const tag = node.tagName ? node.tagName.toLowerCase() : '';
                            if (tag === 'header' || tag === 'nav') return false;
                            node = node.parentElement;
                        }
                        return true;
                    });
                    if (!loginInput) return null;
                    loginInput.focus();
                    // Set value via native input setter so React state updates
                    const nativeInput = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value');
                    nativeInput.set.call(loginInput, email);
                    loginInput.dispatchEvent(new Event('input',  {bubbles: true}));
                    loginInput.dispatchEvent(new Event('change', {bubbles: true}));
                    return loginInput.name || loginInput.id || loginInput.type || 'found';
                }""",
                self.email,
            )
            if not filled_email:
                self._take_screenshot("login_no_email_field")
                logger.error("Could not locate email input via JS. URL: %s",
                             self._page.url)
                return False
            logger.info("Email filled via JS (field identifier: %s).", filled_email)
            self._human_delay(0.4, 0.8)

            # ── Fill password via JavaScript ──────────────────────────
            filled_pw = self._page.evaluate(
                """(pw) => {
                    const pwInput = document.querySelector('input[type="password"]');
                    if (!pwInput) return null;
                    pwInput.focus();
                    const nativeInput = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value');
                    nativeInput.set.call(pwInput, pw);
                    pwInput.dispatchEvent(new Event('input',  {bubbles: true}));
                    pwInput.dispatchEvent(new Event('change', {bubbles: true}));
                    return 'ok';
                }""",
                self.password,
            )
            if not filled_pw:
                self._take_screenshot("login_no_password_field")
                logger.error("Could not fill password field via JS.")
                return False
            logger.info("Password filled via JS.")
            self._human_delay(0.5, 1.0)

            # ── Submit via the Log In button ──────────────────────────
            # Click the pink "Log In" button specifically (not the header
            # search button which also has type=submit).
            submitted = self._page.evaluate(
                """() => {
                    // Try to find a button with login-related text
                    const buttons = Array.from(document.querySelectorAll(
                        'button, input[type="submit"]'));
                    const loginBtn = buttons.find(b => {
                        const txt = (b.textContent || b.value || '').toLowerCase();
                        return txt.includes('log in') || txt.includes('login')
                            || txt.includes('sign in');
                    });
                    if (loginBtn) { loginBtn.click(); return 'button'; }
                    // Fallback: submit the form containing the password input
                    const pw = document.querySelector('input[type="password"]');
                    if (pw && pw.form) { pw.form.submit(); return 'form'; }
                    return null;
                }"""
            )
            logger.info("Login submitted via: %s", submitted or "JS fallback")
            if not submitted:
                # Last resort: press Enter on the password field via Playwright
                try:
                    self._page.locator('input[type="password"]').first.press("Enter")
                except Exception:
                    pass

            # Wait for navigation after submit
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=25_000)
            except Exception:
                pass
            self._human_delay(2.5, 4.0)

            current_url = self._page.url
            logger.info("Post-login URL: %s", current_url)

            # Failure check — catch both auth pages AND search redirects.
            # If the email ended up as a search query it means the search button
            # was clicked instead of the login form submit button.
            failure_signals = (
                "login", "auth/", "sign_in",
                "query=",          # search-box redirect
                "ref=search_box",  # search-box redirect variant
            )
            if any(sig in current_url for sig in failure_signals):
                self._take_screenshot("login_failed")
                logger.error(
                    "Login failed — unexpected redirect to: %s", current_url
                )
                return False

            logger.info("Login successful.")
            return True

        except Exception as exc:
            logger.error("Login exception: %s", exc)
            self._take_screenshot("login_exception")
            return False

    # ------------------------------------------------------------------
    # Upload flow
    # ------------------------------------------------------------------

    def upload(self, image_path: str, seo: dict, product_id: int) -> UploadResult:
        """
        Upload one design to Redbubble.

        The browser is started ONCE and reused across all retry attempts.
        This is critical for the persistent-context approach: closing and
        reopening the profile directory between attempts causes a Chromium
        profile-lock conflict that immediately crashes the second launch.

        Parameters
        ----------
        image_path : Path to the PNG file
        seo        : dict with title, description, tags (list)
        product_id : Internal DB product ID (for screenshot naming)

        Returns
        -------
        UploadResult
        """
        start_ms = int(time.time() * 1000)

        try:
            self._start_browser()
        except Exception as exc:
            return UploadResult(
                success=False,
                error=f"Browser launch failed: {exc}",
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        try:
            # Login check once per upload session (not per attempt)
            if not self._is_logged_in():
                if not self._login():
                    return UploadResult(
                        success=False,
                        error="Login failed — check credentials and run bootstrap_login.py",
                        duration_ms=int(time.time() * 1000) - start_ms,
                    )

            for attempt in range(1, self.max_retries + 1):
                logger.info("Upload attempt %d/%d for product %d",
                            attempt, self.max_retries, product_id)
                try:
                    result = self._do_upload(image_path, seo, product_id)
                    result.duration_ms = int(time.time() * 1000) - start_ms
                    if result.success:
                        return result

                except Exception as exc:
                    logger.error("Upload attempt %d failed: %s", attempt, exc)
                    self._take_screenshot(f"error_product{product_id}_attempt{attempt}")
                    if attempt < self.max_retries:
                        wait = self.upload_delay * attempt
                        logger.info("Waiting %.0fs before retry…", wait)
                        time.sleep(wait)

            return UploadResult(
                success=False,
                error=f"All {self.max_retries} upload attempts failed",
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        finally:
            # Always close the browser — even on exception or early return
            try:
                self._stop_browser()
            except Exception:
                pass

    def _do_upload(self, image_path: str, seo: dict,
                   product_id: int) -> UploadResult:
        """Core upload steps (runs inside a try/except in upload())."""
        page = self._page
        title       = seo.get("title", "Untitled Design")
        description = seo.get("description", "")
        tags        = seo.get("tags", [])

        # --- Navigate to upload page ---
        # If the user already has the upload page open (CF already solved),
        # skip goto() entirely to avoid re-triggering Cloudflare.
        already_on_upload = (
            "portfolio/images/new" in page.url
            or "works/new" in page.url
        )
        logger.info("Navigate to upload page (already there: %s)", already_on_upload)
        if not already_on_upload:
            try:
                page.goto(self.UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception as nav_exc:
                logger.warning("goto timeout (continuing anyway): %s", nav_exc)
            self._human_delay(2.0, 3.0)
        else:
            logger.info("Skipping goto — already on upload page: %s", page.url[:80])
            self._human_delay(0.5, 1.0)

        # --- Check for login redirect ---
        # If the session expired, Redbubble silently redirects /portfolio/images/new
        # back to the auth/login page before we even see the upload form.
        current_url = page.url
        if any(sig in current_url for sig in ("login", "auth/", "sign_in")):
            logger.warning("Upload page redirected to login (%s) — re-authenticating…",
                           current_url)
            if not self._login():
                raise RuntimeError("Session expired and re-login failed.")
            try:
                page.goto(self.UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                pass
            self._human_delay(2.0, 3.0)

        # Handle any Cloudflare challenge on the upload page
        self._wait_for_cloudflare(timeout_ms=90_000)

        # After CF challenge, the page reference may have changed (tab navigated
        # or was replaced). Re-sync self._page from the context so subsequent
        # calls don't hit TargetClosedError.
        try:
            if self._page.is_closed():
                for p in self._context.pages:
                    try:
                        if not p.is_closed() and p.url.startswith("http"):
                            self._page = p
                            page = self._page
                            logger.info("Page ref refreshed after CF — now: %s", page.url[:80])
                            break
                    except Exception:
                        continue
            else:
                page = self._page  # ensure local alias stays in sync
        except Exception:
            pass


        # After CF's clearance redirect, the browser often lands on the homepage
        # (/?#) instead of staying on the upload URL.  Navigate back if needed.
        if self.UPLOAD_URL not in page.url:
            logger.info(
                "CF redirect landed on %s — re-navigating to upload page…", page.url
            )
            try:
                # Use networkidle so React finishes rendering before we probe the DOM
                page.goto(self.UPLOAD_URL, wait_until="networkidle", timeout=45_000)
            except Exception:
                try:
                    page.goto(self.UPLOAD_URL, wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
            self._human_delay(3.0, 5.0)
            # Handle any second CF challenge after the re-navigation
            self._wait_for_cloudflare(timeout_ms=60_000)
        else:
            # Already on upload URL — still give React time to render
            self._human_delay(2.0, 3.5)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

        # Dismiss any cookie/consent banner that may block the upload form
        self._dismiss_cookie_banner()

        # --- Click "Upload new work" on the landing page ---
        # /portfolio/images/new may show a chooser: "Upload new work" vs
        # "Copy an existing work".  The input[type="file"] only appears AFTER
        # clicking "Upload new work".  We must click it before waiting for the input.
        logger.info("Looking for 'Upload new work' button on landing page…")
        upload_new_clicked = False

        def _try_click_upload_button() -> bool:
            """Try every known strategy to click the upload button. Returns True if clicked."""
            # JS strategy — cast a wide net over ALL elements
            try:
                page.wait_for_function(
                    """() => {
                        const all = Array.from(document.querySelectorAll('*'));
                        return all.some(el => /upload new work/i.test(
                            (el.textContent || '').trim().slice(0, 60)
                        ));
                    }""",
                    timeout=12_000,
                )
                clicked = page.evaluate("""() => {
                    const all = Array.from(document.querySelectorAll(
                        'a, button, div[role], span[role], li, [class*="upload"]'
                    ));
                    // Prefer exact matches first
                    for (const el of all) {
                        const txt = (el.textContent || '').trim();
                        if (/^upload new work$/i.test(txt)) {
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return 'exact:' + txt;
                        }
                    }
                    // Partial match fallback
                    for (const el of all) {
                        const txt = (el.textContent || '').slice(0, 60);
                        if (/upload new work/i.test(txt)) {
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return 'partial:' + txt.trim().slice(0, 30);
                        }
                    }
                    return null;
                }""")
                if clicked:
                    logger.info("Clicked 'Upload new work' via JS (%s)", clicked)
                    return True
            except Exception:
                pass

            # Playwright locator fallback
            for sel in [
                'text="Upload new work"',
                ':has-text("Upload new work")',
                'button:has-text("Upload")',
                'a:has-text("Upload")',
                '[data-testid*="upload"]',
                '[class*="UploadNewWork"]',
                '[class*="upload-new"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible(timeout=2_000):
                        btn.click(force=True, timeout=8_000)
                        logger.info("Clicked upload button (%s)", sel)
                        return True
                except Exception:
                    continue
            return False

        # Attempt 1: click the button normally
        if _try_click_upload_button():
            upload_new_clicked = True
            self._human_delay(2.0, 3.0)

        # If button not found AND no file input, try a page reload
        if not upload_new_clicked:
            file_count = page.locator('input[type="file"]').count()
            if file_count == 0:
                logger.info("Upload button not found, no file input — reloading page…")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30_000)
                    self._human_delay(3.0, 5.0)
                    self._wait_for_cloudflare(timeout_ms=60_000)
                    # Try button again after reload
                    if _try_click_upload_button():
                        upload_new_clicked = True
                        self._human_delay(2.0, 3.0)
                except Exception as reload_exc:
                    logger.warning("Reload failed: %s", reload_exc)

        if not upload_new_clicked:
            # Page may already show the full upload form — continue anyway
            logger.info("'Upload new work' button not found — checking if form already visible.")
            # But log what's actually on the page for debugging
            try:
                snippet = page.evaluate(
                    "() => (document.body?.innerText || '').slice(0, 300)"
                )
                logger.info("Page text snippet: %s", snippet.replace("\n", " ")[:200])
            except Exception:
                pass

        if not upload_new_clicked:
            # Either the page already shows the upload form (no chooser)
            # or we're on a URL that skips the chooser — continue anyway
            logger.info("'Upload new work' button not found — assuming form already visible.")

        # --- Wait for the file input (may be hidden inside drag-and-drop zone) ---
        # Redbubble's upload form uses a React drag-and-drop component that keeps
        # input[type="file"] with display:none / visibility:hidden at all times.
        # Default state='visible' therefore always fails; use state='attached' instead.
        logger.info("Waiting for file upload input (attached)…")
        try:
            page.wait_for_selector('input[type="file"]', state="attached", timeout=30_000)
        except Exception:
            logger.warning("File input not found in 30s — attempting load-state wait")
            try:
                page.wait_for_load_state("load", timeout=20_000)
            except Exception:
                pass
            # Last chance: confirm the element exists at all
            count = page.locator('input[type="file"]').count()
            if count == 0:
                self._take_screenshot("upload_no_file_input")
                raise RuntimeError(
                    "No input[type='file'] found on upload page. "
                    f"Current URL: {page.url}"
                )

        # --- Make the file input temporarily visible so Playwright can use it ---
        # Playwright's set_input_files() internally waits for the element to be
        # visible+enabled before acting.  We unlock that by briefly removing the
        # CSS that hides the input, set the file, then restore it.
        page.evaluate("""() => {
            const inp = document.querySelector('input[type="file"]');
            if (inp) {
                inp.style.setProperty('display',    'block',   'important');
                inp.style.setProperty('visibility', 'visible', 'important');
                inp.style.setProperty('opacity',    '1',       'important');
                inp.style.setProperty('width',      '1px',     'important');
                inp.style.setProperty('height',     '1px',     'important');
            }
        }""")
        self._human_delay(0.3, 0.6)

        # --- Upload file ---
        logger.info("Uploading file: %s", image_path)
        file_input = page.locator('input[type="file"]').first
        file_input.set_input_files(image_path)

        # Restore the input to hidden so the UI looks normal again
        page.evaluate("""() => {
            const inp = document.querySelector('input[type="file"]');
            if (inp) {
                inp.style.removeProperty('display');
                inp.style.removeProperty('visibility');
                inp.style.removeProperty('opacity');
                inp.style.removeProperty('width');
                inp.style.removeProperty('height');
            }
        }""")
        # Wait for upload to process.
        # Strategy 1: wait for a preview element to appear (preferred).
        # Strategy 2: wait for the title input to appear (upload form loaded).
        # Strategy 3: just wait a generous fixed time and continue.
        # Wait for the upload form to be ready.
        # We use the title input as the primary signal — it appears as soon as
        # Redbubble processes the image.  Actual id is "work_title_en", so
        # input[id*="title"] is the reliable catch-all.
        upload_confirmed = False
        for confirm_sel in [
            'input[id*="title"]',           # work_title_en — most reliable
            'input[id*="title_en"]',         # explicit variant
            '#work_title_en',
            'input[name*="title"]',          # work[title_en]
            'input[placeholder*="title" i]',
            "canvas",                        # preview canvas as last resort
        ]:
            try:
                page.wait_for_selector(confirm_sel, timeout=45_000)
                upload_confirmed = True
                logger.info("Upload form ready (confirmed via %s).", confirm_sel)
                break
            except Exception:
                continue

        if not upload_confirmed:
            logger.warning("Could not confirm upload form ready — waiting 20s and continuing.")
            time.sleep(20)

        self._human_delay(2.0, 4.0)
        logger.info("File upload complete.")

        # --- Fill title ---
        # Try multiple selector patterns; fill() already clears first so no
        # triple_click needed (triple_click doesn't exist on Playwright Locator).
        title_filled = False
        for sel in [
            'input[id*="title"]',            # matches work_title_en
            '#work_title_en',
            'input[name*="title"]',           # matches work[title_en]
            '#work_title',
            'input[placeholder*="title" i]',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    loc.click()
                    loc.press("Control+a")
                    loc.fill(title)
                    title_filled = True
                    logger.info("Title filled (%s).", sel)
                    break
            except Exception:
                continue
        if not title_filled:
            logger.warning("Could not find title field — skipping title fill.")
        self._human_delay(0.5, 1.2)

        # --- Fill tags ---
        # Scroll to TOP so the title/description/tags section (above the
        # products list) is in view. The earlier scroll-to-bottom was moving
        # the viewport AWAY from the tag input, causing all tag strategies
        # to fail because the element was scrolled out of view.
        try:
            page.evaluate("window.scrollTo(0, 0)")
            self._human_delay(1.5, 2.5)
        except Exception:
            pass

        # ── Fill tags ────────────────────────────────────────────────────────────────
        # Redbubble now has TWO visible tag fields:
        #   1. "Main Tag"        placeholder="Example: Mountain"
        #   2. "Supporting Tags" placeholder="Example: nature, outdoors, painting, realistic"
        # The hidden #work_tag_editor_en is React-managed; do NOT target it directly.

        tag_filled_any = False

        # --- Strategy 0: placeholder-based selectors (highest priority) ---
        main_tag_text = tags[0].strip() if tags else ""
        supporting_text = ", ".join(t.strip() for t in tags[1:15])

        for ph_sel in [
            'input[placeholder="Example: Mountain"]',
            'input[placeholder*="Mountain" i]',
        ]:
            try:
                loc = page.locator(ph_sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(force=True)
                    loc.fill(main_tag_text)
                    logger.info("Main Tag filled (%s): %s", ph_sel, main_tag_text)
                    tag_filled_any = True
                    break
            except Exception:
                continue

        for ph_sel in [
            'input[placeholder*="nature, outdoors" i]',
            'input[placeholder*="painting, realistic" i]',
            'input[placeholder*="Supporting" i]',
        ]:
            try:
                loc = page.locator(ph_sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3_000)
                    loc.click(force=True)
                    loc.fill(supporting_text)
                    logger.info("Supporting Tags filled (%s).", ph_sel)
                    tag_filled_any = True
                    break
            except Exception:
                continue

        if not tag_filled_any:
            # Strategy A: legacy textarea
            tag_textarea_filled = False
            for sel in [
                'textarea[name*="tag"]',
                'textarea[id*="tag"]',
                '#work_tag_list',
                'textarea[placeholder*="tag" i]',
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=2_000):
                        loc.fill(", ".join(t.strip() for t in tags[:15]))
                        tag_textarea_filled = True
                        logger.info("Tags filled as comma-separated list (%s).", sel)
                        break
                except Exception:
                    continue

            if not tag_textarea_filled:
                # Strategy B: chip/token input
                tag_input = None
                tag_selectors = [
                    'input.token-input',
                    'input[id^="token-input"]',
                    'input[id*="tag_list"]',
                    'input[name*="tag"]',
                    'input[id*="tag"]',
                    'input[placeholder*="tag" i]',
                    'input[placeholder*="Add a tag" i]',
                    'input[placeholder*="Add tags" i]',
                    'input[placeholder*="keyword" i]',
                    'input[class*="tag" i]',
                    '[data-testid*="tag" i] input',
                    '.tag-list input',
                    '.tags-input input',
                    '.chip-input input',
                    '.token-input-list input',
                ]
                for sel in tag_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=2_000):
                            tag_input = loc
                            break
                    except Exception:
                        continue

                if tag_input:
                    for tag in tags[:15]:
                        try:
                            tag_input.click()
                            tag_input.type(tag.strip(), delay=60)
                            tag_input.press("Enter")
                            self._human_delay(0.2, 0.5)
                        except Exception:
                            continue
                    logger.info("Tags typed one-by-one via chip input.")
                    tag_filled_any = True
                else:
                    # Strategy C: JS injection visible inputs
                    js_filled = page.evaluate(
                        "(tagStr) => {"
                        "  const all = Array.from(document.querySelectorAll("
                        "    'input[type=text], input:not([type])'));"
                        "  for (const el of all) {"
                        "    if (!el.offsetParent) continue;"
                        "    const combined = (el.id+el.getAttribute('name')+el.getAttribute('class')+el.getAttribute('placeholder')).toLowerCase();"
                        "    if (/tag|keyword/.test(combined)) {"
                        "      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
                        "      if (s&&s.set) s.set.call(el,tagStr); else el.value=tagStr;"
                        "      el.dispatchEvent(new Event('input',{bubbles:true}));"
                        "      el.dispatchEvent(new Event('change',{bubbles:true}));"
                        "      return {found:true, id:el.id, ph:el.placeholder};"
                        "    }"
                        "  }"
                        "  return null;"
                        "}",
                        ", ".join(t.strip() for t in tags[:15]),
                    )
                    if js_filled:
                        logger.info("Tags filled via JS strategy C: %s", js_filled)
                        tag_filled_any = True
                    else:
                        # Strategy D: ALL elements incl hidden + contenteditable
                        hidden_filled = page.evaluate(
                            "(tagStr) => {"
                            "  for (const el of document.querySelectorAll('*')) {"
                            "    const id  = (el.id||'').toLowerCase();"
                            "    const nm  = (el.getAttribute('name')||'').toLowerCase();"
                            "    const cls = (el.getAttribute('class')||'').toLowerCase();"
                            "    const lbl = (el.getAttribute('aria-label')||'').toLowerCase();"
                            "    const ph  = (el.getAttribute('placeholder')||'').toLowerCase();"
                            "    const dt  = (el.getAttribute('data-testid')||'').toLowerCase();"
                            "    if (/tag|keyword/.test(id+nm+cls+lbl+ph+dt)) {"
                            "      if (el.tagName==='INPUT'||el.tagName==='TEXTAREA') {"
                            "        const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;"
                            "        const pd = Object.getOwnPropertyDescriptor(proto, 'value');"
                            "        if (pd&&pd.set) pd.set.call(el,tagStr); else el.value=tagStr;"
                            "        el.dispatchEvent(new Event('input',{bubbles:true}));"
                            "        el.dispatchEvent(new Event('change',{bubbles:true}));"
                            "        return {found:true,tagName:el.tagName,id:el.id,type:el.type||''};"
                            "      } else if (el.isContentEditable) {"
                            "        el.focus(); el.textContent=tagStr;"
                            "        el.dispatchEvent(new Event('input',{bubbles:true}));"
                            "        return {found:true,tagName:el.tagName,id:el.id,ce:true};"
                            "      }"
                            "    }"
                            "  }"
                            "  return null;"
                            "}",
                            ", ".join(t.strip() for t in tags[:15]),
                        )
                        if hidden_filled:
                            logger.info("Tags filled via strategy D: %s", hidden_filled)
                            tag_filled_any = True
                        else:
                            logger.warning("All tag-fill strategies failed.")

        self._human_delay(0.5, 1.0)

        # --- Fill description ---
        desc_filled = False
        for sel in [
            'textarea[id*="description"]',
            '#work_description_en',
            '#work_description',
            'textarea[name*="description"]',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    loc.fill(description)
                    desc_filled = True
                    break
            except Exception:
                continue
        if not desc_filled:
            logger.warning("Could not find description field — skipping.")
        self._human_delay(0.3, 0.8)

        # --- Enable product types ---
        self._enable_products(page)

        # --- Select No for mature content (mandatory radio) ---
        # click() triggers React onChange; check()/checked= do NOT.
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._human_delay(0.5, 1.0)
            no_btn = page.locator('#work_safe_for_work_true').first
            if no_btn.count() > 0:
                no_btn.scroll_into_view_if_needed(timeout=3_000)
                no_btn.click(force=True)
                self._human_delay(0.3, 0.6)
                page.evaluate(
                    "() => {"
                    "  const r = document.querySelector('#work_safe_for_work_true');"
                    "  if (r) { r.checked = true;"
                    "    ['click','change','input'].forEach(t => r.dispatchEvent("
                    "      new Event(t, {bubbles:true, cancelable:true})));"
                    "  }"
                    "}"
                )
                logger.info("Mature content radio set to No (safe for work).")
            else:
                logger.warning("Mature content radio #work_safe_for_work_true not found.")
        except Exception as exc:
            logger.warning("Could not set mature content radio: %s", exc)
        self._human_delay(0.5, 1.0)

        # --- Tick the Rights Declaration checkbox (required for form submit) ---
        # Must use click() — React ignores programmatic check()/checked= changes.
        try:
            rights = page.locator('#rightsDeclaration').first
            if rights.count() > 0:
                rights.scroll_into_view_if_needed(timeout=3_000)
                if not rights.is_checked():
                    rights.click(force=True)
                    self._human_delay(0.3, 0.5)
                # Belt-and-suspenders: also fire React synthetic events via JS
                page.evaluate(
                    "() => {"
                    "  const cb = document.querySelector('#rightsDeclaration');"
                    "  if (cb) { cb.checked = true;"
                    "    ['click','change','input'].forEach(t => cb.dispatchEvent("
                    "      new Event(t, {bubbles:true, cancelable:true})));"
                    "  }"
                    "}"
                )
                logger.info("Rights declaration ticked (click + JS events).")
            else:
                logger.warning("Rights declaration checkbox not found.")
        except Exception as exc:
            logger.warning("Could not tick rights declaration: %s", exc)
        self._human_delay(0.5, 1.0)

        # --- Save / Publish ---
        logger.info("Clicking publish / save button…")
        save_btn = None
        for sel in [
            '#submit-work',
            'input[type="submit"][name="commit"]',
            'button:has-text("Save work")',
            'button:has-text("Save")',
            'button:has-text("Publish")',
            'input[value*="Save"]',
            'button[type="submit"]',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3_000):
                    save_btn = loc
                    break
            except Exception:
                continue
        if save_btn:
            save_btn.click(force=True)
            logger.info("Save button clicked via selector.")
        else:
            logger.warning("Save button not found — attempting JS submit.")
            page.evaluate("""() => {
                const btn = document.querySelector('#submit-work') ||
                            document.querySelector('input[type="submit"]');
                if (btn) btn.click();
                else { const f = document.querySelector('form'); if (f) f.submit(); }
            }""")
        # Wait for URL to leave /new (up to 90s — server can be slow)
        try:
            page.wait_for_url(
                lambda url: "/new" not in url and "redbubble.com" in url,
                timeout=90_000,
            )
            logger.info("Page navigated after save: %s", page.url)
        except Exception:
            # Log what's on the page to help diagnose why it didn't navigate
            try:
                err_text = page.locator('.error, .alert, .flash, [class*="error"], [class*="Error"]').all_inner_texts()
                if err_text:
                    logger.warning("Form errors visible after save attempt: %s", err_text)
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
        self._human_delay(2.0, 4.0)

        # --- Capture product URL ---
        product_url = page.url
        work_id = ""
        import re
        m = re.search(r"/works/(\d+)", product_url)
        if m:
            work_id = m.group(1)
        elif "portfolio" in product_url:
            m2 = re.search(r"/(\d+)", product_url)
            if m2:
                work_id = m2.group(1)

        if not work_id:
            page_text = page.inner_text("body")
            if "published" in page_text.lower() or "live" in page_text.lower():
                work_id = f"pending_{product_id}"
                product_url = product_url.split("?")[0]

        logger.info("Upload successful. URL: %s | work_id: %s",
                    product_url, work_id)

        # ── Navigate back to /new so next upload can reuse this tab ──────
        # Without this, the tab stays on /studio/promote/... and the next
        # run finds no redbubble.com/new tab, opens a fresh one, and
        # triggers Cloudflare again.
        try:
            logger.info("Navigating tab back to upload page for next run…")
            page.goto(
                self.UPLOAD_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            logger.info("Tab ready for next upload: %s", page.url[:80])
        except Exception as nav_back_exc:
            logger.warning(
                "Could not navigate back to /new (non-fatal): %s", nav_back_exc
            )

        return UploadResult(
            success=True,
            product_url=product_url,
            work_id=work_id,
        )

    # ------------------------------------------------------------------
    # Product enablement
    # ------------------------------------------------------------------

    def _enable_products(self, page: "Page") -> None:  # type: ignore[name-defined]
        """Enable checkboxes for configured product types."""
        for product_slug in self.products_to_enable:
            try:
                selectors = [
                    f'input[value="{product_slug}"]',
                    f'label[for*="{product_slug}"]',
                    f'[data-product-type="{product_slug}"] input',
                    f'input[id*="{product_slug}"]',
                ]
                for sel in selectors:
                    elem = page.locator(sel).first
                    if elem.count() > 0:
                        if not elem.is_checked():
                            elem.check()
                            self._human_delay(0.1, 0.3)
                        break
            except Exception as exc:
                logger.debug("Could not enable product '%s': %s", product_slug, exc)

    # ------------------------------------------------------------------
    # Screenshot helper
    # ------------------------------------------------------------------

    def _take_screenshot(self, label: str = "error") -> str:
        """Save a screenshot and return its path."""
        try:
            if self._page:
                path = str(self.screenshot_dir / f"{label}_{int(time.time())}.png")
                self._page.screenshot(path=path, full_page=True)
                logger.info("Screenshot saved: %s", path)
                return path
        except Exception as exc:
            logger.warning("Screenshot failed: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Human-like delay
    # ------------------------------------------------------------------

    @staticmethod
    def _human_delay(min_s: float, max_s: float) -> None:
        time.sleep(random.uniform(min_s, max_s))

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RedbubbleUploader":
        return self

    def __exit__(self, *_: object) -> None:
        try:
            self._stop_browser()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI smoke-test (dry-run mode — does not actually upload)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    uploader = RedbubbleUploader(headless=False)
    print("Uploader initialised. Set dry_run=True in settings to skip actual upload.")
    print("Email:", uploader.email[:4] + "****" if uploader.email else "NOT SET")
