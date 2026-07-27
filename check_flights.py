
import json
import os
import re
from playwright.sync_api import sync_playwright
import requests

STATE_FILE = "state.json"

# ---------------------------------------------------------------------
# Flytoday
# ---------------------------------------------------------------------

FLYTODAY_NOT_FOUND_PHRASES = [
    "یافت نشد",
    "پروازی موجود نیست",
    "پروازی پیدا نشد",
    "متاسفانه",
    "نتیجه‌ای پیدا نشد",
    "نتیجه ای یافت نشد",
    "تکمیل شده",
    "ظرفیت پروازها در این تاریخ تکمیل شده",
]


def build_flytoday_url(origin: str, destination: str, date: str) -> str:
    return (
        f"https://www.flytoday.ir/flight/search?"
        f"departure={origin},1&arrival={destination},1"
        f"&departureDate={date}&adt=1&chd=0&inf=0&cabin=1&isAnyWhere=false"
    )


def check_flytoday(page, origin: str, destination: str, date: str) -> bool:
    url = build_flytoday_url(origin, destination, date)
    page.goto(url, timeout=60000)
    page.wait_for_timeout(15000)

    text = page.inner_text("body")
    has_not_found = any(phrase in text for phrase in FLYTODAY_NOT_FOUND_PHRASES)
    has_price_hint = ("تومان" in text) or ("ریال" in text)

    return has_price_hint and not has_not_found


# ---------------------------------------------------------------------
# Iran Air (ebooking.iranair.com)
# ---------------------------------------------------------------------

IRANAIR_HOME_URL = "https://ebooking.iranair.com/ibe/IR/home/?language=fa#searchForm"


def julian_day_number(date_str: str) -> int:
    """محاسبه Julian Day Number استاندارد برای یه تاریخ میلادی (YYYY-MM-DD)."""
    y, m, d = map(int, date_str.split("-"))
    a = (14 - m) // 12
    y2 = y + 4800 - a
    m2 = m + 12 * a - 3
    return (
        d
        + (153 * m2 + 2) // 5
        + 365 * y2
        + y2 // 4
        - y2 // 100
        + y2 // 400
        - 32045
    )


def iranair_calendar_value(date_str: str) -> str:
    """عددی که سایت ایران‌ایر توی کلاس روزهای تقویم استفاده می‌کنه (jdXXXXXXX.5)."""
    jdn = julian_day_number(date_str)
    value = jdn - 0.5
    return str(value)


def set_iranair_autocomplete(page, input_id: str, code: str) -> None:
    page.click(f"#{input_id}")
    page.fill(f"#{input_id}", "")
    page.type(f"#{input_id}", code, delay=120)
    page.wait_for_timeout(1200)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)


def to_iranair_date_format(date_str: str) -> str:
    """تبدیل 2026-07-27 به همون فرمتی که ایران‌ایر توی data-date استفاده می‌کنه (27/07/2026)."""
    y, m, d = date_str.split("-")
    return f"{d}/{m}/{y}"


def check_iranair(page, origin: str, destination: str, date: str) -> bool:
    page.goto(IRANAIR_HOME_URL, timeout=60000)
    page.wait_for_timeout(3000)

    set_iranair_autocomplete(page, "PRSF_from", origin)
    set_iranair_autocomplete(page, "PRSF_to", destination)

    # باز کردن تقویم و انتخاب روز درست
    page.click("#PRSF_dep_date")
    page.wait_for_timeout(800)
    jd_value = iranair_calendar_value(date)
    day_selector = f'a[class*="jd{jd_value}"]'
    try:
        page.click(day_selector, timeout=5000)
    except Exception as e:
        print(f"نتونستم روز {date} رو توی تقویم ایران‌ایر پیدا کنم: {e}")
        return False

    page.wait_for_timeout(500)
    page.click("#PRSF_search_form_do")
    page.wait_for_timeout(15000)

    # این سایت یه هفته کامل تاریخ رو با هم نشون می‌ده (تب‌های بالای نتایج)،
    # پس باید دقیقاً همون تب مربوط به تاریخ درخواستی رو پیدا کنیم، نه کل صفحه.
    target = to_iranair_date_format(date)
    tab_selector = f'a[data-date="{target}"]'
    try:
        tab = page.locator(tab_selector).first
        class_attr = tab.get_attribute("class") or ""
        tab_text = tab.inner_text()
    except Exception as e:
        print(f"نتونستم تب تاریخ {date} رو توی نتایج ایران‌ایر پیدا کنم: {e}")
        return False

    if "no-flights-day" in class_attr:
        return False

    return "ریال" in tab_text


# ---------------------------------------------------------------------
# منطق مشترک
# ---------------------------------------------------------------------


def load_config():
    raw = os.environ.get("FLIGHT_CONFIG")
    if not raw:
        raise RuntimeError(
            "متغیر FLIGHT_CONFIG تنظیم نشده. یه Secret به همین اسم بساز."
        )
    data = json.loads(raw)
    return data["routes"], data["dates"]


def check_route(page, route: dict, date: str) -> bool:
    site = route.get("site", "flytoday")
    if site == "iranair":
        return check_iranair(page, route["origin"], route["destination"], date)
    return check_flytoday(page, route["origin"], route["destination"], date)


def link_for(route: dict, date: str) -> str:
    site = route.get("site", "flytoday")
    if site == "iranair":
        return IRANAIR_HOME_URL
    return build_flytoday_url(route["origin"], route["destination"], date)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=30)
    if not resp.ok:
        print("خطا در ارسال پیام تلگرام:", resp.text)


def main() -> None:
    routes, dates = load_config()
    state = load_state()
    changed = False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        for route in routes:
            site = route.get("site", "flytoday")
            for date in dates:
                key = f"{site}-{route['origin']}-{route['destination']}-{date}"
                try:
                    available = check_route(page, route, date)
                except Exception as e:
                    print(f"خطا در چک کردن {key}: {e}")
                    continue

                previous = state.get(key, False)

                if available and not previous:
                    msg = (
                        "✈️ پرواز جدید پیدا شد!\n"
                        f"سایت: {site}\n"
                        f"مسیر: {route.get('label', key)}\n"
                        f"تاریخ: {date}\n"
                        f"لینک: {link_for(route, date)}"
                    )
                    send_telegram(msg)
                    changed = True
                    print(f"پیام فرستاده شد برای {key}")
                else:
                    print(f"{key}: موجود={available} (قبلی={previous})")

                state[key] = available

        browser.close()

    save_state(state)

    if not changed:
        print("هیچ تغییری نسبت به اجرای قبلی پیدا نشد.")


if __name__ == "__main__":
    main()
