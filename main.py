import cloudscraper
from bs4 import BeautifulSoup
import re
from flask import Flask, jsonify

# ================= FLASK SETUP =================
app = Flask(__name__)

# ================= CONFIG =================
LIST_URL = "https://temp-number.com/public/temporary-numbers?q=&country=United+States&per_page=10&new24=1"

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)

# ================= HELPER FUNCTIONS =================

def construct_detail_url(phone):
    """Constructs the URL internally based on the phone number."""
    return f"https://temp-number.com/public/temporary-numbers/United-States/{phone}/1"

def get_numbers_list():
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
            # "detail_url": detail_url, # Hidden from output
            "status": status
        })

    return numbers

def get_messages(detail_url):
    """
    Fetches the detail page and returns all messages found.
    """
    try:
        resp = scraper.get(detail_url, timeout=12)
        if resp.status_code != 200:
            print(f"Detail page failed: {resp.status_code}", flush=True)
            return []
    except Exception as e:
        print(f"Request error: {e}", flush=True)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_cards = soup.find_all("article", class_=re.compile(r'^msg-card'))

    messages = []

    for card in msg_cards:
        from_div = card.find("div", class_="msg-from")
        sender_span = from_div.find_all("span")[-1] if from_div else None
        sender = sender_span.get_text(strip=True) if sender_span else "?"

        time_tag = card.find("time", class_="msg-time")
        time_text = time_tag.get_text(strip=True) if time_tag else "?"
        
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

        messages.append({
            "sender": sender,
            "time": time_text,
            "otp": otp or "—",
            "message": body_text,
            "display": display
        })

    return messages


# ================= FLASK ROUTES =================

@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "endpoints": {
            "/numbers": "Get list of available numbers",
            "/messages/<phone>": "Fetch messages for a specific phone number (Refresh to update)"
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

@app.route('/messages/<phone>')
def api_get_messages(phone):
    """
    Fetches messages on-demand. 
    No monitoring. Refresh the page/request to get new data.
    """
    # Construct URL internally
    detail_url = construct_detail_url(phone)
    
    # Fetch messages live
    msgs = get_messages(detail_url)
    
    return jsonify({
        "phone": phone,
        "count": len(msgs),
        "messages": msgs
    })


# ================= MAIN =================

if __name__ == '__main__':
    print("Starting Flask Server (On-Demand Mode)...", flush=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
