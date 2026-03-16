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
LIST_URL = "https://temp-number.com/public/temporary-numbers?q=&country=United+States&per_page=50"
POLL_INTERVAL = 25          # seconds between checks
MAX_POLL_DURATION = 3600    # 60 minutes max

# Create a single scraper instance to maintain cookies/session
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)

# Global dictionary to store latest messages for each monitored number
# Structure: { "phone_number": [list_of_message_dicts] }
latest_messages_cache = {}
# Track the freshest message time seen so far for each number
last_seen_state = {}

# ================= HELPER FUNCTIONS =================

def get_numbers_list():
    """Fetches and parses the list of phone numbers."""
    try:
        resp = scraper.get(LIST_URL, timeout=15)
        if resp.status_code != 200:
            print(f"List page failed: {resp.status_code}", flush=True)
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
            "detail_url": detail_url,
            "status": status
        })

    return numbers


def parse_time_ago(time_str):
    """Converts 'X minutes ago' string to seconds."""
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
    """Fetches messages from a detail page and filters for new ones."""
    try:
        resp = scraper.get(detail_url, timeout=12)
        if resp.status_code != 200:
            print(f"Detail page failed: {resp.status_code}", flush=True)
            return [], last_seen_seconds_ago
    except Exception as e:
        print(f"Request error: {e}", flush=True)
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

        # Filter: only messages newer than what we've already seen
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
    """Background thread function to monitor a specific number."""
    print(f"[Thread] Starting monitor for {phone}...", flush=True)
    
    # Initialize state for this thread
    last_freshest = 999999
    first_poll = True
    start = time.time()
    
    # Initialize cache for this number
    latest_messages_cache[phone] = []

    while time.time() - start < MAX_POLL_DURATION:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            # print(f"[{ts}] [{phone}] Checking...", flush=True) # Optional: Debug log

            new_msgs, new_freshest = get_new_messages(detail_url, last_freshest)
            last_freshest = min(last_freshest, new_freshest)

            if new_msgs:
                print(f"[{ts}] [{phone}] Found {len(new_msgs)} new message(s)", flush=True)
                # Add new messages to the front of the list (newest first)
                latest_messages_cache[phone] = new_msgs + latest_messages_cache[phone]
                # Optional: Limit cache size to prevent memory issues
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
    # Clean up
    if phone in latest_messages_cache:
        del latest_messages_cache[phone]


# ================= FLASK ROUTES =================

@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "endpoints": {
            "/numbers": "Get list of available numbers",
            "/start/<phone>": "Start monitoring a specific phone number",
            "/messages/<phone>": "Get cached messages for a monitored number"
        }
    })

@app.route('/numbers')
def api_numbers():
    """Returns list of numbers."""
    data = get_numbers_list()
    return jsonify({
        "count": len(data),
        "numbers": data
    })

@app.route('/start/<phone>')
def api_start_monitor(phone):
    """Starts monitoring a specific phone number in the background."""
    
    # 1. Verify the number exists and get its URL
    # We fetch the list again to ensure we have the valid detail_url
    numbers = get_numbers_list()
    target = next((n for n in numbers if n['phone'] == phone), None)
    
    if not target:
        return jsonify({"error": "Phone number not found in current list"}), 404

    # 2. Check if already running (simple check)
    if phone in latest_messages_cache:
        return jsonify({"status": "already_running", "message": f"Already monitoring {phone}"})

    # 3. Start Thread
    t = threading.Thread(target=monitor_thread_func, args=(phone, target['detail_url']))
    t.daemon = True # Allows flask to kill threads on exit
    t.start()
    
    return jsonify({
        "status": "started", 
        "phone": phone, 
        "detail_url": target['detail_url']
    })

@app.route('/messages/<phone>')
def api_get_messages(phone):
    """Returns cached messages for the phone number."""
    if phone not in latest_messages_cache:
        return jsonify({"error": "No active monitor for this number. Call /start/<phone> first."}), 404
    
    return jsonify({
        "phone": phone,
        "messages": latest_messages_cache[phone]
    })


# ================= MAIN =================

if __name__ == '__main__':
    print("Starting Flask Server...", flush=True)
    print("Access http://127.0.0.1:5000/numbers to see available numbers", flush=True)
    # use_reloader=False is important to prevent the scraper from initializing twice
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
