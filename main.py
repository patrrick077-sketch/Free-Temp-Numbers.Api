import cloudscraper
from bs4 import BeautifulSoup
import re
from flask import Flask, jsonify

# ================= FLASK SETUP =================
app = Flask(__name__)

# ================= CONFIG =================
LIST_URL = "https://temp-number.com/temporary-numbers?country=United+States"

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)

# ================= HELPER FUNCTIONS =================

def construct_detail_url(phone):
    """Constructs detail URL."""
    return f"https://temp-number.com/temporary-numbers/United-States/{phone}/1"


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
        try:
            # ✅ NEW: get number directly
            number_tag = card.find("span", class_="number-card__number")
            if not number_tag:
                continue
            phone_raw = number_tag.get_text(strip=True)

            # ✅ NEW: reliable link source
            detail_url = card.get("data-href")
            if not detail_url:
                link_tag = card.find("a", class_="number-card__link")
                detail_url = link_tag["href"] if link_tag else None

            if not detail_url:
                continue

            # ✅ Format number
            if phone_raw.startswith("1") and len(phone_raw) == 11:
                formatted = f"+1 ({phone_raw[1:4]}) {phone_raw[4:7]}-{phone_raw[7:]}"
            else:
                formatted = f"+{phone_raw}"

            # ✅ NEW: cleaner status extraction
            status_tag = card.find("time", class_="number-card__date")
            status = status_tag.get_text(strip=True) if status_tag else "?"

            # Detect NEW badge
            is_new = card.find("span", class_="pill--new")
            if is_new:
                status = f"NEW {status}"

            # ✅ Messages count
            msgs_tag = card.find("span", class_="number-card__msgs")
            messages_count = msgs_tag.get_text(strip=True) if msgs_tag else "0"

            numbers.append({
                "phone": phone_raw,
                "formatted": formatted,
                "status": status,
                "messages": messages_count
            })

        except Exception as e:
            print(f"Card parse error: {e}", flush=True)
            continue

    return numbers


def get_messages(detail_url):
    """
    Fetches messages from detail page.
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
        try:
            # Sender
            sender = "?"
            from_div = card.find("div", class_="msg-from")
            if from_div:
                spans = from_div.find_all("span")
                if spans:
                    sender = spans[-1].get_text(strip=True)

            # Time
            time_tag = card.find("time", class_="msg-time")
            time_text = time_tag.get_text(strip=True) if time_tag else "?"

            # Body
            body_div = card.find("div", class_="msg-body")
            if not body_div:
                continue

            body_text = body_div.get_text(" ", strip=True)

            # OTP extraction
            otp_span = body_div.find("span", class_="otp-code")
            otp = None
            if otp_span:
                otp = otp_span.get_text(strip=True) or otp_span.get("data-clipboard-text", "").strip()

            display = body_text
            if otp:
                display = f"**OTP → {otp}**\n{body_text}"

            messages.append({
                "sender": sender,
                "time": time_text,
                "otp": otp or "—",
                "message": body_text,
                "display": display
            })

        except Exception as e:
            print(f"Message parse error: {e}", flush=True)
            continue

    return messages


# ================= FLASK ROUTES =================

@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "endpoints": {
            "/numbers": "Get list of available numbers",
            "/messages/<phone>": "Fetch messages for a phone number"
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
    detail_url = construct_detail_url(phone)
    msgs = get_messages(detail_url)

    return jsonify({
        "phone": phone,
        "count": len(msgs),
        "messages": msgs
    })


# ================= MAIN =================

if __name__ == '__main__':
    print("Starting Flask Server...", flush=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
