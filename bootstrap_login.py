"""
bootstrap_login.py
==================
Run this ONCE manually to log into Redbubble and save the session.

Usage
-----
    python bootstrap_login.py

A Chrome window will open at the Redbubble login page.
Log in normally (email + password, solve any CAPTCHA).
Once you are on the Redbubble homepage / dashboard, come back to this
terminal and press Enter — the session is saved automatically.

IMPORTANT: This script uses a PERSISTENT Chrome profile stored at
data/chrome_profile/.  That profile keeps Cloudflare's cf_clearance
cookie alive between runs, so the pipeline won't be challenged again
after the first successful verification.

After this, main.py will reuse the saved profile and cookies and won't
need to log in again until the session expires (~30 days).
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SESSION_FILE = Path("data/redbubble_session.json")
SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

PROFILE_DIR = Path("data/chrome_profile")
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

LOGIN_URL = "https://www.redbubble.com/auth/login"

print("=" * 60)
print("  Redbubble Session Bootstrap")
print("=" * 60)
print()
print("Opening Chrome…  A browser window will appear.")
print("Log in manually with your Redbubble credentials.")
print("Solve any CAPTCHA or Cloudflare verification if prompted.")
print()
print("When you are logged in and see your Redbubble dashboard,")
print("come back here and press ENTER.")
print()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed.")
    print("Run:  pip install playwright && playwright install chromium")
    sys.exit(1)

pw = sync_playwright().start()

_launch_kwargs = dict(
    headless=False,
    slow_mo=80,
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

# Use a persistent context — CF clearance cookies are stored in the profile
# and reused on every future run so the pipeline is never challenged again.
# Launch order: Opera GX (user's real browser) → Chrome → Chromium
_opera_paths = [
    r"C:\Users\DELL\AppData\Local\Programs\Opera GX\opera.exe",
    r"C:\Program Files\Opera GX\opera.exe",
    r"C:\Program Files (x86)\Opera GX\opera.exe",
]
context = None

for opera_path in _opera_paths:
    if Path(opera_path).exists():
        try:
            kw = dict(_launch_kwargs)
            kw["executable_path"] = opera_path
            context = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kw)
            print(f"(Using Opera GX with persistent profile)")
            break
        except Exception as exc:
            print(f"(Opera GX failed: {exc} — trying next)")

if context is None:
    for channel in ("chrome", None):
        try:
            kw = dict(_launch_kwargs)
            if channel:
                kw["channel"] = channel
            context = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kw)
            print(f"(Using {'real Chrome' if channel else 'bundled Chromium'} with persistent profile)")
            break
        except Exception as exc:
            print(f"(channel={channel} failed: {exc} — trying next)")

if context is None:
    print("ERROR: Could not launch any browser.")
    pw.stop()
    sys.exit(1)

# Anti-bot stealth patches injected before any page loads
context.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined, configurable: true,
    });
    if (!window.chrome) { window.chrome = { runtime: {} }; }
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const a = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            ];
            a.refresh = () => {}; a.item = i => a[i];
            return a;
        }, configurable: true,
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'], configurable: true,
    });
""")

# Also merge any existing auth cookies so the user may already be logged in
if SESSION_FILE.exists() and SESSION_FILE.stat().st_size > 10:
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        if cookies:
            context.add_cookies(cookies)
            print("(Restored previous Redbubble auth cookies — you may already be logged in.)")
    except Exception:
        pass

# Always open a fresh tab — pages[0] might be gxcorner.games (Opera GX new-tab)
# which blocks navigation to external URLs.
page = context.new_page()

# If we already have auth cookies, go straight to the homepage — navigating
# to the login URL causes ERR_ABORTED because Redbubble immediately redirects
# authenticated users away before domcontentloaded fires.
# If no cookies yet, go to the login page so the user can log in manually.
import json as _json
_has_cookies = False
try:
    _data = _json.loads(SESSION_FILE.read_text(encoding="utf-8")) if SESSION_FILE.exists() else {}
    _has_cookies = bool(_data.get("cookies"))
except Exception:
    pass

_start_url = "https://www.redbubble.com/" if _has_cookies else LOGIN_URL
try:
    page.goto(_start_url, wait_until="domcontentloaded", timeout=30_000)
except Exception as _e:
    # ERR_ABORTED on login page = already logged in redirect — ignore it
    print(f"(Navigation note: {_e} — continuing)")
    # Give the redirect a moment to settle
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass

# ── Pre-fill credentials if available ──────────────────────────────
email    = os.environ.get("REDBUBBLE_EMAIL", "")
password = os.environ.get("REDBUBBLE_PASSWORD", "")

if email:
    try:
        for sel in [
            'input[placeholder="Email or Username"]',
            'input[placeholder*="Email or Username" i]',
            'input[placeholder*="username" i]',
            'input[name="credentials[email]"]',
            'input[autocomplete="email"]',
            'input[type="email"]',
        ]:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=2_000):
                loc.fill(email)
                print(f"(Pre-filled email field with: {email})")
                break
    except Exception:
        pass

print()
print("Browser is open. Complete the login manually now.")
print("If you see a Cloudflare 'Verify you are human' checkbox, click it.")
print("The verification result is saved to the profile, so it won't appear again.")
print()
input(">>> Press ENTER here once you're logged in <<<")
print()

# ── Wait for any in-flight navigation to settle ────────────────────
# If Cloudflare just did a redirect when Enter was pressed, the page
# context is mid-navigation and evaluate() would crash.
for _ in range(3):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8_000)
        break
    except Exception:
        pass

# ── Check if actually logged in ────────────────────────────────────
current_url = page.url
try:
    logged_in_flag = page.evaluate(
        "() => window.localStorage.getItem('_rb_logged_in')"
    )
except Exception:
    # Page navigated between our check and evaluate — read the URL instead
    logged_in_flag = None
    current_url = page.url

if logged_in_flag == "1" or (
    "redbubble.com" in current_url
    and "login" not in current_url
    and "auth" not in current_url
):
    # Save auth cookies to SESSION_FILE for _is_logged_in() check
    context.storage_state(path=str(SESSION_FILE))
    print(f"✅  Session saved to {SESSION_FILE}")
    print(f"    Chrome profile: {PROFILE_DIR}")
    print(f"    Current URL   : {current_url}")
    print()
    print("You can now run the pipeline:")
    print("    python main.py --run-now")
else:
    print(f"⚠️   It looks like you may not be fully logged in.")
    print(f"    Current URL : {current_url}")
    print(f"    _rb_logged_in flag: {logged_in_flag}")
    print()
    save = input("Save session anyway? [y/N]: ").strip().lower()
    if save == "y":
        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved to {SESSION_FILE}")

try:
    context.close()
except Exception:
    pass
try:
    pw.stop()
except Exception:
    pass
print("Done.")
