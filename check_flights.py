

import json
import os
from playwright.sync_api import sync_playwright
import requests

# ---------- تنظیمات ----------
# مسیرها و تاریخ‌ها دیگه اینجا نوشته نمی‌شن (که توی Public repo دیده نشن).
# به‌جاش از متغیر مخفی FLIGHT_CONFIG خونده می‌شن.


def load_config():
    raw = os.environ.get("FLIGHT_CONFIG")
    if not raw:
        raise RuntimeError(
            "متغیر FLIGHT_CONFIG تنظیم نشده. یه Secret به همین اسم بساز."
        )
    data = json.loads(raw)
    return data["routes"], data["dates"]


NOT_FOUND_PHRASES = [
    "یافت نشد",
    "پروازی موجود نیست",
    "پروازی پیدا نشد",
    "متاسفانه",
    "نتیجه‌ای پیدا نشد",
    "نتیجه ای یافت نشد",
    "تکمیل شده",
    "ظرفیت پروازها در این تاریخ تکمیل شده",
]

STATE_FILE = "state.json"


def build_url(origin: str, destination: str, date: str) -> str:
    return (
        f"https://www.flytoday.ir/flight/search?"
        f"departure={origin},1&arrival={destination},1"
        f"&departureDate={date}&adt=1&chd=0&inf=0&cabin=1&isAnyWhere=false"
    )


def check_flight(page, origin: str, destination: str, date: str) -> bool:
    url = build_url(origin, destination, date)
    page.goto(url, timeout=60000)
    page.wait_for_timeout(15000)

    text = page.inner_text("body")

    has_not_found = any(phrase in text for phrase in NOT_FOUND_PHRASES)
    has_price_hint = ("تومان" in text) or ("ریال" in text)

    return has_price_hint and not has_not_found


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
            for date in dates:
                key = f"{route['origin']}-{route['destination']}-{date}"
                try:
                    available = check_flight(
                        page, route["origin"], route["destination"], date
                    )
                except Exception as e:
                    print(f"خطا در چک کردن {key}: {e}")
                    continue

                previous = state.get(key, False)

                if available and not previous:
                    msg = (
                        "✈️ پرواز جدید پیدا شد!\n"
                        f"مسیر: {route['label']}\n"
                        f"تاریخ: {date}\n"
                        f"لینک: {build_url(route['origin'], route['destination'], date)}"
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
