import os
import json
import time
import threading
import yfinance as yf
import requests
from flask import Flask, request, redirect, url_for, render_template_string
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:5000")
DATA_FILE = "stocks.json"

# Clean up URL trailing slashes just in case
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

# --- Fetch Stock Fundamentals ---
def fetch_stock_details(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    
    def safe_get(key, format_type=None):
        val = info.get(key)
        if val is None:
            return "N/A"
        try:
            if format_type == "percent":
                return f"{val * 100:.2f}%"
            if format_type == "large":
                if val >= 1e12: return f"{val/1e12:.2f}T"
                elif val >= 1e9: return f"{val/1e9:.2f}B"
                elif val >= 1e6: return f"{val/1e6:.2f}M"
                return str(val)
            return round(val, 2) if isinstance(val, float) else val
        except:
            return str(val)

    try:
        current_price = stock.fast_info['last_price']
    except:
        current_price = safe_get('currentPrice')
        
    if current_price == "N/A" or current_price is None:
        current_price = 0.0

    details = {
        "current_price": round(current_price, 2) if isinstance(current_price, (int, float)) else current_price,
        "market_cap": safe_get('marketCap', 'large'),
        "pe": safe_get('trailingPE'),
        "industry_pe": "N/A",  
        "pb": safe_get('priceToBook'),
        "debt_to_equity": safe_get('debtToEquity'),
        "revenue_growth": safe_get('revenueGrowth', 'percent'),
        "profit_growth": safe_get('earningsGrowth', 'percent')
    }
    return current_price, details

# --- Alert Senders (UPDATED FOR HTML AND LOGGING) ---
def send_telegram_alert(ticker, price, limit, is_immediate=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ ERROR: Telegram credentials are missing in Environment Variables!")
        return
    
    continue_url = f"{EXTERNAL_URL}/alert_action/{ticker}/continue"
    remove_url = f"{EXTERNAL_URL}/alert_action/{ticker}/remove"
    
    # Using HTML to prevent Markdown V1/V2 parsing crashes
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
    
    try:
        # Added detailed logging so we can see exactly what Telegram says
        response = requests.post(url, json=payload)
        print(f"--- TELEGRAM API RESPONSE FOR {ticker} ---")
        print(response.text)
        print("------------------------------------------")
    except Exception as e:
        print(f"Failed to connect to Telegram: {e}")

# --- Background Price Checker ---
def price_checker():
    while True:
        data = load_data()
        updated = False
        
        for ticker, info in list(data.items()):
            try:
                stock = yf.Ticker(ticker)
                current_price = stock.fast_info['last_price']
                
                if 'details' not in info:
                    info['details'] = {}
                
                if info['details'].get('current_price') != round(current_price, 2):
                    info['details']['current_price'] = round(current_price, 2)
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
                            <small class="text-muted">Live: ₹{{ info.details.current_price if info.details else 'N/A' }}</small>
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
    
    try:
        current_price, details = fetch_stock_details(ticker)
    except:
        current_price = 0.0
        details = {}
    
    if current_price and current_price <= limit:
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
        return "Ticker not found or already removed.", 404
        
    if action == "remove":
        del data[ticker]
        save_data(data)
        return f"<h3>✅ Alert for {ticker} permanently removed.</h3><br><a href='/'>Back to Dashboard</a>"
        
    elif action == "continue":
        data[ticker]['state'] = "ALERTED"
        save_data(data)
        return f"<h3>✅ Monitoring continued for {ticker}.</h3><p>You will be alerted again only after the price goes above ₹{data[ticker]['limit']} and drops back down.</p><br><a href='/'>Back to Dashboard</a>"

    return "Invalid Action", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
    
