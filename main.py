import cloudscraper
from bs4 import BeautifulSoup
import re
import time
import sys
import threading
from datetime import datetime
from flask import Flask, jsonify

# ================= FLASK SETUP =================
app = Flask(__name__)

# ================= CONFIG =================
LIST_URL = "https://temp-number.com/public/temporary-numbers?q=&country=United+States&per_page=10&new24=1"
POLL_INTERVAL = 25
MAX_POLL_DURATION = 3600

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)

latest_messages_cache = {}

# ================= HELPER FUNCTIONS =================

def construct_detail_url(phone):
    """Helper to build the URL internally when needed."""
    return f"https://temp-number.com/public/temporary-numbers/United-States/{phone}/1"

def get_numbers_list():
    """Fetches list and hides the detail URL from output."""
    try:
        resp = scraper.get(LIST_URL, timeout=15)
        if resp.status_code != 200:
            return []
    except Exception as e:
        print(f"Request error: {e}", flush=True)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.find_all("article", class_="number-card")

    numbers = []
    for card in cards:
        onclick = card.get("onclick", "")
        match = re.search(r"window\.location\.href='([^']+)'", onclick)
        if not match:
            continue

        # We still parse the URL to extract the phone number
        detail_url = match.group(1)
        if not detail_url.startswith("http"):
            detail_url = "https://temp-number.com" + detail_url

        parts = detail_url.strip("/").split("/")
        if len(parts) < 4 or not parts[-2].isdigit():
            continue

        phone_raw = parts[-2]
        formatted = f"+1 ({phone_raw[1:4]}) {phone_raw[4:7]}-{phone_raw[7:]}" if phone_raw.startswith("1") and len(phone_raw) == 10 else f"+{phone_raw}"

        text = card.get_text(" ", strip=True)
        status_match = re.search(r"(NEW\s*Just\s*now|NEW|\d+\s*(min|sec|hour)s?\s*ago)", text, re.I)
        status = status_match.group(0).strip() if status_match else "?"

        numbers.append({
            "phone": phone_raw,
            "formatted": formatted,
            # "detail_url": detail_url,  <-- REMOVED from JSON output
            "status": status
        })

    return numbers


def parse_time_ago(time_str):
    if not time_str:
        return 999999
    time_str = time_str.lower().strip()
    match = re.search(r'(\d+)\s*(second|minute|hour)s?\s*ago', time_str)
    if not match:
        return 999999
    value, unit = int(match.group(1)), match.group(2)
    if "second" in unit:   return value
    if "minute" in unit:   return value * 60
    if "hour"   in unit:   return value * 3600
    return 999999


def get_new_messages(detail_url, last_seen_seconds_ago=999999):
    try:
        resp = scraper.get(detail_url, timeout=12)
        if resp.status_code != 200:
            return [], last_seen_seconds_ago
    except Exception as e:
        return [], last_seen_seconds_ago

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_cards = soup.find_all("article", class_=re.compile(r'^msg-card'))

    new_messages = []
    freshest_this_poll = 999999

    for card in msg_cards:
        from_div = card.find("div", class_="msg-from")
        sender_span = from_div.find_all("span")[-1] if from_div else None
        sender = sender_span.get_text(strip=True) if sender_span else "?"

        time_tag = card.find("time", class_="msg-time")
        time_text = time_tag.get_text(strip=True) if time_tag else ""
        seconds_ago = parse_time_ago(time_text)

        if seconds_ago < freshest_this_poll:
            freshest_this_poll = seconds_ago

        if seconds_ago > last_seen_seconds_ago + 5:
            continue

        body_div = card.find("div", class_="msg-body")
        if not body_div:
            continue
        body_text = body_div.get_text(" ", strip=True)

        otp_span = body_div.find("span", class_="otp-code")
        otp = None
        if otp_span:
            otp = otp_span.get_text(strip=True) or otp_span.get("data-clipboard-text", "").strip()

        display = body_text
        if otp:
            display = f"**OTP → {otp}**\n{display}"

        new_messages.append({
            "sender": sender,
            "time": time_text or "?",
            "otp": otp or "—",
            "message": body_text,
            "display": display
        })

    return new_messages, freshest_this_poll


# ================= MONITORING THREAD =================

def monitor_thread_func(phone, detail_url):
    print(f"[Thread] Starting monitor for {phone}...", flush=True)
    last_freshest = 999999
    first_poll = True
    start = time.time()
    
    # Initialize empty list immediately
    latest_messages_cache[phone] = []

    while time.time() - start < MAX_POLL_DURATION:
        try:
            new_msgs, new_freshest = get_new_messages(detail_url, last_freshest)
            last_freshest = min(last_freshest, new_freshest)

            if new_msgs:
                # Prepend new messages
                latest_messages_cache[phone] = new_msgs + latest_messages_cache[phone]
                # Limit cache size
                if len(latest_messages_cache[phone]) > 20:
                    latest_messages_cache[phone] = latest_messages_cache[phone][:20]

            if first_poll:
                print(f"[Thread] {phone} initial scan complete.", flush=True)
                first_poll = False

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[Thread] Error: {e}", flush=True)
            time.sleep(20)

    print(f"[Thread] Monitoring finished for {phone}.", flush=True)
    if phone in latest_messages_cache:
        del latest_messages_cache[phone]


# ================= FLASK ROUTES =================

@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "endpoints": {
            "/numbers": "Get list of available numbers (cleaned)",
            "/messages/<phone>": "Get messages (auto-starts monitor if needed)"
        }
    })

@app.route('/numbers')
def api_numbers():
    data = get_numbers_list()
    return jsonify({
        "count": len(data),
        "numbers": data
    })

@app.route('/messages/<phone>')
def api_get_messages(phone):
    # 1. Check if monitoring is already active
    if phone in latest_messages_cache:
        return jsonify({
            "status": "monitoring_active",
            "phone": phone,
            "messages": latest_messages_cache[phone]
        })
    
    # 2. If not active, start monitoring automatically
    # We construct the URL internally so the user doesn't need to provide it
    detail_url = construct_detail_url(phone)
    
    print(f"[API] Auto-starting monitor for {phone}...", flush=True)
    
    # Initialize cache key so subsequent requests know it's starting
    latest_messages_cache[phone] = [] 
    
    t = threading.Thread(target=monitor_thread_func, args=(phone, detail_url))
    t.daemon = True
    t.start()
    
    return jsonify({
        "status": "monitor_started",
        "phone": phone,
        "message": "Monitoring initiated. Please refresh in a few seconds to see initial messages.",
        "messages": []
    })

# ================= MAIN =================

if __name__ == '__main__':
    print("Starting Flask Server...", flush=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
