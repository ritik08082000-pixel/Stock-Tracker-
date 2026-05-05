import os
import json
import time
import threading
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, redirect, url_for, render_template_string
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:5000")
DATA_FILE = "stocks.json"

if EXTERNAL_URL.endswith('/'):
    EXTERNAL_URL = EXTERNAL_URL[:-1]

# --- Data Management ---
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- BULLETPROOF PRICE MATCHER ---
def get_live_price(ticker):
    """Tries Yahoo first, falls back to Google Finance if Yahoo blocks Render's IP"""
    # 1. Try Yahoo Finance
    try:
        stock = yf.Ticker(ticker)
        price = float(stock.fast_info['last_price'])
        if price > 0: return price
    except:
        pass

    # 2. Fallback to Google Finance
    try:
        # Convert Yahoo format (TCS.NS) to Google format (TCS:NSE)
        if ticker.endswith('.NS'):
            g_ticker = f"{ticker[:-3]}:NSE"
        elif ticker.endswith('.BO'):
            g_ticker = f"{ticker[:-3]}:BOM"
        else:
            g_ticker = ticker
            
        url = f"https://www.google.com/finance/quote/{g_ticker}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Google Finance CSS class for the main price
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        if price_div:
            price_str = price_div.text.replace('₹', '').replace(',', '').replace('$', '').strip()
            return float(price_str)
    except Exception as e:
        print(f"Google fallback failed for {ticker}: {e}")
        
    return 0.0

# --- Fetch Stock Fundamentals ---
def fetch_stock_details(ticker):
    current_price = get_live_price(ticker)
    
    # Try to get fundamentals from Yahoo (might still return N/A if blocked, but price will work)
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
    except:
        info = {}
    
    def safe_get(key, format_type=None):
        val = info.get(key)
        if val is None: return "N/A"
        try:
            if format_type == "percent": return f"{val * 100:.2f}%"
            if format_type == "large":
                if val >= 1e12: return f"{val/1e12:.2f}T"
                elif val >= 1e9: return f"{val/1e9:.2f}B"
                elif val >= 1e6: return f"{val/1e6:.2f}M"
                return str(val)
            return round(val, 2) if isinstance(val, float) else val
        except:
            return str(val)

    details = {
        "current_price": current_price,
        "market_cap": safe_get('marketCap', 'large'),
        "pe": safe_get('trailingPE'),
        "industry_pe": "N/A",  
        "pb": safe_get('priceToBook'),
        "debt_to_equity": safe_get('debtToEquity'),
        "revenue_growth": safe_get('revenueGrowth', 'percent'),
        "profit_growth": safe_get('earningsGrowth', 'percent')
    }
    return current_price, details

# --- Alert Senders ---
def send_telegram_alert(ticker, price, limit, is_immediate=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ ERROR: Telegram credentials are missing!")
        return
    
    continue_url = f"{EXTERNAL_URL}/alert_action/{ticker}/continue"
    remove_url = f"{EXTERNAL_URL}/alert_action/{ticker}/remove"
    
    if is_immediate:
        header = f"⚡ <b>INSTANT ALERT: {ticker}</b>"
        body = f"You just added this stock and it is ALREADY below your limit!\nLimit: ₹{limit}\nCurrent Price: ₹{price:.2f}"
    else:
        header = f"🚨 <b>STOCK ALERT: {ticker}</b>"
        body = f"Price has dropped below ₹{limit}!\nCurrent Price: ₹{price:.2f}"

    message = (
        f"{header}\n{body}\n\n"
        f"Choose an action:\n"
        f"✅ <a href='{continue_url}'>Continue Monitoring</a>\n"
        f"❌ <a href='{remove_url}'>Remove Alert</a>"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

# --- Background Price Checker ---
def price_checker():
    while True:
        data = load_data()
        updated = False
        
        for ticker, info in list(data.items()):
            try:
                current_price = get_live_price(ticker)
                
                # Only process if we actually got a real price
                if current_price > 0:
                    if 'details' not in info:
                        info['details'] = {}
                    
                    if info['details'].get('current_price') != current_price:
                        info['details']['current_price'] = current_price
                        updated = True

                    limit = info['limit']
                    state = info.get('state', 'UNKNOWN')
                    
                    if current_price > limit and state != "ABOVE":
                        info['state'] = "ABOVE"
                        updated = True
                        
                    elif current_price <= limit and state == "ABOVE":
                        info['state'] = "ALERTED" 
                        updated = True
                        send_telegram_alert(ticker, current_price, limit, is_immediate=False)
                        
            except Exception as e:
                pass
                
        if updated:
            save_data(data)
            
        time.sleep(60)

threading.Thread(target=price_checker, daemon=True).start()

# --- HTML Interface ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Stock Alert Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body class="bg-light">
<div class="container mt-4 mb-5">
    <h2 class="mb-4">📈 Stock Alert Dashboard</h2>
    
    <div class="card mb-4 shadow-sm">
        <div class="card-body">
            <h5 class="card-title">Add or Modify Stock</h5>
            <form action="/add" method="POST" class="row g-3">
                <div class="col-md-5">
                    <input type="text" name="ticker" class="form-control" placeholder="Ticker (e.g., TCS.NS)" required>
                </div>
                <div class="col-md-5">
                    <input type="number" step="0.01" name="limit" class="form-control" placeholder="Alert Limit (₹)" required>
                </div>
                <div class="col-md-2">
                    <button type="submit" class="btn btn-primary w-100">Save</button>
                </div>
            </form>
        </div>
    </div>

    <div class="card shadow-sm">
        <div class="card-body p-0">
            <table class="table table-hover mb-0">
                <thead class="table-light">
                    <tr>
                        <th class="ps-3">Ticker</th>
                        <th>Alert Limit</th>
                        <th>State</th>
                        <th class="pe-3">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for ticker, info in stocks.items() %}
                    {% set safe_ticker = ticker|replace('.', '') %}
                    <tr>
                        <td class="ps-3">
                            <strong>{{ ticker }}</strong><br>
                            <small class="text-muted">Live: ₹{{ info.details.current_price if info.details and info.details.current_price > 0 else 'N/A' }}</small>
                        </td>
                        <td class="align-middle">₹{{ info.limit }}</td>
                        <td class="align-middle">
                            {% if info.state == 'ALERTED' %}
                                <span class="badge bg-danger">Triggered</span>
                            {% elif info.state == 'ABOVE' %}
                                <span class="badge bg-success">Armed (Above)</span>
                            {% else %}
                                <span class="badge bg-secondary">Waiting</span>
                            {% endif %}
                        </td>
                        <td class="align-middle pe-3">
                            <button class="btn btn-sm btn-outline-info" type="button" data-bs-toggle="collapse" data-bs-target="#collapse{{ safe_ticker }}">
                                Data
                            </button>
                            <a href="/remove/{{ ticker }}" class="btn btn-sm btn-danger">Remove</a>
                        </td>
                    </tr>
                    <tr class="collapse" id="collapse{{ safe_ticker }}">
                        <td colspan="4" class="bg-light p-3">
                            <div class="row text-muted" style="font-size: 0.9em;">
                                <div class="col-6 col-md-3 mb-2"><strong>Mkt Cap:</strong> {{ info.details.market_cap if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3 mb-2"><strong>P/E:</strong> {{ info.details.pe if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3 mb-2"><strong>Ind P/E:</strong> {{ info.details.industry_pe if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3 mb-2"><strong>P/B:</strong> {{ info.details.pb if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3"><strong>D/E:</strong> {{ info.details.debt_to_equity if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3"><strong>Rev Growth:</strong> {{ info.details.revenue_growth if info.details else 'N/A' }}</div>
                                <div class="col-6 col-md-3"><strong>Profit Growth:</strong> {{ info.details.profit_growth if info.details else 'N/A' }}</div>
                            </div>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" class="text-center py-4">No stocks tracked yet.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

# --- Web Routes ---
@app.route('/')
def index():
    stocks = load_data()
    return render_template_string(HTML_TEMPLATE, stocks=stocks)

@app.route('/add', methods=['POST'])
def add_stock():
    ticker = request.form['ticker'].upper()
    limit = float(request.form['limit'])
    data = load_data()
    
    # 1. Fetch live price with Google Fallback included
    current_price, details = fetch_stock_details(ticker)
    
    # 2. Check logic and alert!
    if current_price > 0 and current_price <= limit:
        state = "ALERTED"
        send_telegram_alert(ticker, current_price, limit, is_immediate=True)
    else:
        state = "ABOVE"
        
    data[ticker] = {
        "limit": limit, 
        "state": state,
        "details": details
    }
    save_data(data)
    return redirect(url_for('index'))

@app.route('/remove/<ticker>')
def remove_stock(ticker):
    data = load_data()
    if ticker in data:
        del data[ticker]
        save_data(data)
    return redirect(url_for('index'))

@app.route('/alert_action/<ticker>/<action>')
def alert_action(ticker, action):
    data = load_data()
    if ticker not in data:
        return "Ticker not found.", 404
        
    if action == "remove":
        del data[ticker]
        save_data(data)
        return f"<h3>✅ Alert removed.</h3><br><a href='/'>Back to Dashboard</a>"
        
    elif action == "continue":
        data[ticker]['state'] = "ALERTED"
        save_data(data)
        return f"<h3>✅ Monitoring continued.</h3><br><a href='/'>Back to Dashboard</a>"

    return "Invalid Action", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
        
