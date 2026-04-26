#!/usr/bin/env python3
"""
Park Hyatt Tokyo Harry Winston Collaboration - Weekend Reservation Monitor
Works both locally (.env file) and on GitHub Actions (environment variables).
"""

import json
import os
import smtplib
import datetime
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESERVATION_URL = (
    "https://www.tablecheck.com/ja/shops/tokyo-park-hyatt-plb/reserve"
    "?menu_lists%5b%5d=69b935b73d77ef8c41e24daa"
)
SHOP_SLUG = "tokyo-park-hyatt-plb"
MENU_LIST_ID = "69b935b73d77ef8c41e24daa"
SEEN_DATES_FILE = BASE_DIR / "seen_dates.json"
LOG_FILE = BASE_DIR / "checker.log"


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Read from environment variables first, then fall back to .env file."""
    config = {}
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip().strip('"').strip("'")
    # Environment variables override .env (used by GitHub Actions Secrets)
    for key in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def load_seen_dates() -> set:
    if SEEN_DATES_FILE.exists():
        return set(json.loads(SEEN_DATES_FILE.read_text()))
    return set()


def save_seen_dates(dates: set):
    SEEN_DATES_FILE.write_text(
        json.dumps(sorted(dates), ensure_ascii=False, indent=2)
    )


def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def is_weekend(date_str: str) -> bool:
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        return d.weekday() in (5, 6)
    except ValueError:
        return False


def is_future(date_str: str) -> bool:
    try:
        return datetime.date.fromisoformat(date_str[:10]) >= datetime.date.today()
    except ValueError:
        return False


def weekday_ja(date_str: str) -> str:
    days = ["月", "火", "水", "木", "金", "土", "日"]
    d = datetime.date.fromisoformat(date_str[:10])
    return days[d.weekday()]


# ---------------------------------------------------------------------------
# Strategy 1: Direct HTTP API (fast, no browser needed)
# ---------------------------------------------------------------------------

def check_via_api() -> list[str]:
    """Try tablecheck's JSON API endpoints to find available dates."""
    try:
        import requests
    except ImportError:
        return []

    headers = {
        "Accept": "application/json, text/javascript, */*",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Referer": RESERVATION_URL,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    today = datetime.date.today()
    found_dates = []

    for month_offset in range(3):
        year = today.year + (today.month + month_offset - 1) // 12
        month = (today.month + month_offset - 1) % 12 + 1

        candidate_urls = [
            (
                f"https://www.tablecheck.com/api/2.0/venues/{SHOP_SLUG}/availability"
                f"?year={year}&month={month}&menu_list_ids[]={MENU_LIST_ID}"
            ),
            (
                f"https://www.tablecheck.com/ja/shops/{SHOP_SLUG}/availability.json"
                f"?year={year}&month={month}&menu_list_ids[]={MENU_LIST_ID}"
            ),
            (
                f"https://www.tablecheck.com/ja/shops/{SHOP_SLUG}/reserve/availability"
                f"?year={year}&month={month}&menu_list_ids[]={MENU_LIST_ID}"
            ),
        ]

        for url in candidate_urls:
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    body = resp.text
                    dates = re.findall(r"\d{4}-\d{2}-\d{2}", body)
                    # Only keep dates that look "available" (not just referenced as unavailable)
                    try:
                        data = resp.json()
                        # If response has explicit available/unavailable fields, filter properly
                        body_lower = json.dumps(data).lower()
                        if '"available":true' in body_lower or '"open":true' in body_lower:
                            # Try to extract only available dates
                            for d in dates:
                                idx = body.find(d)
                                context = body[max(0, idx - 50):idx + 50]
                                if "true" in context.lower():
                                    found_dates.append(d)
                        else:
                            found_dates.extend(dates)
                    except Exception:
                        found_dates.extend(dates)
                    log(f"API hit: {url} → {len(dates)} dates")
                    break
            except Exception:
                continue

    return found_dates


# ---------------------------------------------------------------------------
# Strategy 2: Playwright headless browser (reliable fallback)
# ---------------------------------------------------------------------------

def check_via_playwright() -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright not installed. Skipping browser strategy.")
        return []

    found_dates = []
    api_dates = []

    def handle_response(response):
        url = response.url
        if any(k in url for k in ("availability", "slots", "schedule", "calendar", "reserve")):
            try:
                text = response.text()
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
                api_dates.extend(dates)
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = context.new_page()
        page.on("response", handle_response)

        try:
            log("Launching headless browser...")
            page.goto(RESERVATION_URL, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(4000)
            page.keyboard.press("PageDown")
            page.wait_for_timeout(2000)

            # data-date attributes
            items = page.eval_on_selector_all(
                "[data-date]",
                "els => els.map(e => ({ d: e.dataset.date, cls: e.className, dis: e.getAttribute('aria-disabled') }))"
            )
            for item in items:
                d = item.get("d", "")
                disabled = str(item.get("dis", "") or item.get("cls", "")).lower()
                if d and "disabled" not in disabled and "true" not in disabled:
                    found_dates.append(d)

            # DayPicker pattern
            if not found_dates:
                labels = page.eval_on_selector_all(
                    ".DayPicker-Day:not(.DayPicker-Day--disabled):not(.DayPicker-Day--outside)",
                    "els => els.map(e => e.getAttribute('aria-label') || e.dataset.day || '')"
                )
                for label in labels:
                    found_dates.extend(re.findall(r"\d{4}-\d{2}-\d{2}", str(label)))

            # Intercepted API responses
            if not found_dates and api_dates:
                log(f"Using {len(api_dates)} dates from intercepted API responses")
                found_dates.extend(api_dates)

            # Last resort: page source
            if not found_dates:
                html = page.content()
                found_dates.extend(re.findall(r"\d{4}-\d{2}-\d{2}", html))

            screenshot_path = BASE_DIR / "last_check.png"
            page.screenshot(path=str(screenshot_path), full_page=True)

        except Exception as e:
            log(f"Browser error: {e}")
        finally:
            browser.close()

    return found_dates


# ---------------------------------------------------------------------------
# Availability aggregation
# ---------------------------------------------------------------------------

def check_availability() -> list[str]:
    today = datetime.date.today()

    # Try fast API approach first
    log("Trying direct API...")
    api_results = check_via_api()
    if api_results:
        log(f"API returned {len(api_results)} dates")
        weekends = sorted({
            d for d in set(api_results)
            if is_weekend(d) and is_future(d)
        })
        if weekends:
            log(f"Weekend dates from API: {weekends}")
            return weekends
        log("API returned dates but none were future weekends — trying browser...")

    # Fall back to Playwright
    log("Trying headless browser...")
    browser_results = check_via_playwright()
    weekends = sorted({
        d for d in set(browser_results)
        if is_weekend(d) and is_future(d)
    })
    return weekends


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def send_email(config: dict, new_dates: list[str]) -> bool:
    sender = config.get("GMAIL_ADDRESS", "").strip()
    password = config.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = config.get("NOTIFY_EMAIL", sender).strip()

    if not sender or not password:
        log("ERROR: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "【予約空き情報】パークハイアット東京 ハリーウィンストンコラボ"
    msg["From"] = sender
    msg["To"] = recipient

    date_lines_text = "\n".join(f"  ・{d}（{weekday_ja(d)}）" for d in new_dates)
    date_lines_html = "".join(
        f"<li><strong>{d}（{weekday_ja(d)}）</strong></li>" for d in new_dates
    )

    text_body = f"""\
パークハイアット東京 ハリーウィンストンコラボに土日の空きが出ました！

▼ 空いている日程:
{date_lines_text}

▼ 今すぐ予約する:
{RESERVATION_URL}

このメールは自動監視システムから送信されています。
"""

    html_body = f"""\
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;">
<h2 style="color:#1a1a1a;">パークハイアット東京<br>ハリーウィンストンコラボ 空き情報</h2>
<p style="font-size:16px;">土日の予約に空きが出ました！お早めに！</p>
<h3>空いている日程:</h3>
<ul style="font-size:15px;line-height:1.8;">{date_lines_html}</ul>
<p style="margin-top:24px;">
  <a href="{RESERVATION_URL}"
     style="background:#8b1a2d;color:white;padding:14px 28px;
            text-decoration:none;border-radius:4px;font-size:15px;">
    今すぐ予約する
  </a>
</p>
<hr style="margin-top:32px;">
<small style="color:#888;">このメールは自動監視システムから送信されています。</small>
</body></html>
"""

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        log(f"Email sent to {recipient}")
        return True
    except smtplib.SMTPAuthenticationError:
        log("ERROR: Gmail authentication failed. Check GMAIL_APP_PASSWORD.")
        return False
    except Exception as e:
        log(f"ERROR: Failed to send email: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("=== Availability check started ===")
    config = load_config()
    seen_dates = load_seen_dates()

    available = check_availability()
    log(f"Available weekend dates: {available if available else 'none'}")

    new_dates = [d for d in available if d not in seen_dates]

    if new_dates:
        log(f"NEW dates to notify: {new_dates}")
        if send_email(config, new_dates):
            seen_dates.update(new_dates)
            save_seen_dates(seen_dates)
    else:
        if available:
            log("Available dates already notified previously.")
        else:
            log("No weekend availability found.")

    log("=== Check complete ===\n")


if __name__ == "__main__":
    main()
