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
# Render will provide your URL (e.g., https://my-stock-alert.onrender.com)
EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:5000")
DATA_FILE = "stocks.json"

# --- Data Management ---
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- Alert Senders ---
def send_telegram_alert(ticker, price, limit):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    # Generate action URLs
    continue_url = f"{EXTERNAL_URL}/alert_action/{ticker}/continue"
    remove_url = f"{EXTERNAL_URL}/alert_action/{ticker}/remove"
    
    message = (
        f"🚨 *Stock Alert: {ticker}*\n"
        f"Price dropped below ₹{limit}. Current Price: ₹{price:.2f}\n\n"
        f"Choose an action:\n"
        f"✅ [Continue Monitoring]({continue_url})\n"
        f"❌ [Remove Alert]({remove_url})"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- Background Price Checker ---
def price_checker():
    while True:
        data = load_data()
        updated = False
        
        for ticker, info in list(data.items()):
            try:
                stock = yf.Ticker(ticker)
                current_price = stock.fast_info['last_price']
                limit = info['limit']
                state = info.get('state', 'UNKNOWN')
                
                # If price goes above limit, arm the system
                if current_price > limit and state != "ABOVE":
                    info['state'] = "ABOVE"
                    updated = True
                    
                # If price drops below limit AND was previously above it
                elif current_price < limit and state == "ABOVE":
                    info['state'] = "ALERTED" # Prevent spamming
                    updated = True
                    send_telegram_alert(ticker, current_price, limit)
                    
            except Exception as e:
                print(f"Error checking {ticker}: {e}")
                
        if updated:
            save_data(data)
            
        time.sleep(60) # Check every 60 seconds

# Start the background thread
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
<div class="container mt-5">
    <h2 class="mb-4">📈 Stock Alert Dashboard</h2>
    
    <!-- Add/Modify Form -->
    <div class="card mb-4">
        <div class="card-body">
            <h5 class="card-title">Add or Modify Stock</h5>
            <form action="/add" method="POST" class="row g-3">
                <div class="col-md-5">
                    <input type="text" name="ticker" class="form-control" placeholder="Ticker (e.g., HGINFRA.NS)" required>
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

    <!-- Stock List -->
    <div class="card">
        <div class="card-body">
            <h5 class="card-title">Tracked Stocks</h5>
            <table class="table">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Alert Limit</th>
                        <th>Current State</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {% for ticker, info in stocks.items() %}
                    <tr>
                        <td><strong>{{ ticker }}</strong></td>
                        <td>₹{{ info.limit }}</td>
                        <td>{{ info.state }}</td>
                        <td>
                            <a href="/remove/{{ ticker }}" class="btn btn-sm btn-danger">Remove</a>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" class="text-center">No stocks tracked yet.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
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
    
    # Adding or Modifying updates the dictionary seamlessly
    data[ticker] = {"limit": limit, "state": "UNKNOWN"}
    save_data(data)
    return redirect(url_for('index'))

@app.route('/remove/<ticker>')
def remove_stock(ticker):
    data = load_data()
    if ticker in data:
        del data[ticker]
        save_data(data)
    return redirect(url_for('index'))

# Route triggered by Telegram/WhatsApp Links
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
        # Reset state so it waits for the price to go above the limit again before re-arming
        data[ticker]['state'] = "BELOW"
        save_data(data)
        return f"<h3>✅ Monitoring continued for {ticker}.</h3><p>You will be alerted again when the price goes above ₹{data[ticker]['limit']} and drops back down.</p><br><a href='/'>Back to Dashboard</a>"

    return "Invalid Action", 400

if __name__ == '__main__':
    # Use 0.0.0.0 for deployment environments like Render
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
