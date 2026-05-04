from flask import Flask, request, redirect
import threading
import time
import requests
import yfinance as yf
import os
import json

app = Flask(__name__)
DATA_FILE = "stocks.json"

# --- DATABASE FUNCTIONS ---
def load_saved_stocks():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    # Default fallback if the file doesn't exist yet
    return {"RELIANCE.NS": 3000.0} 

def save_stocks(stocks_dict):
    with open(DATA_FILE, "w") as f:
        json.dump(stocks_dict, f, indent=4)

# Load the database into memory when the script starts
my_stocks = load_saved_stocks()

# --- TELEGRAM FUNCTION (Secure Vault) ---
def send_telegram_message(message):
    bot_token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id:
        print("[!] Missing Telegram Tokens. Please add them to Environment Variables.")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": message})
    except Exception as e:
        print(f"Telegram error: {e}")

# --- WEB DASHBOARD ---
@app.route('/')
def dashboard():
    html = """
    <html>
        <body style="font-family: Arial; padding: 20px;">
            <h2>📈 My Stock Alert Dashboard</h2>
            
            <h3>Currently Tracking:</h3>
            <ul>
    """
    for symbol, price in my_stocks.items():
        html += f"<li><b>{symbol}</b> (Target: ₹{price})</li>"
        
    html += """
            </ul>
            
            <hr>
            <h3>Add a New Stock:</h3>
            <form action="/add" method="POST">
                <input type="text" name="symbol" placeholder="Stock Symbol (e.g. TCS.NS)" required style="padding: 5px;">
                <input type="number" step="0.01" name="target" placeholder="Target Price (₹)" required style="padding: 5px;">
                <button type="submit" style="padding: 6px; background: blue; color: white; border: none; border-radius: 4px; cursor: pointer;">Start Tracking</button>
            </form>
        </body>
    </html>
    """
    return html

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form['symbol'].upper()
    # Ensure Indian stocks have .NS
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    target = float(request.form['target'])
    
    # Save it to our database
    my_stocks[symbol] = target
    save_stocks(my_stocks)
    
    # Refresh the page automatically
    return redirect('/')

# --- BACKGROUND MONITOR ---
def stock_monitor():
    print("Background monitor started automatically...")
    alerted_stocks = {} 

    while True:
        try:
            for symbol, target_price in list(my_stocks.items()):
                stock = yf.Ticker(symbol)
                history = stock.history(period="1d")
                if history.empty: continue
                    
                current_price = history['Close'].iloc[-1]
                print(f"Checked {symbol}: ₹{current_price:.2f} (Target: ₹{target_price})")

                if current_price <= target_price:
                    if symbol not in alerted_stocks:
                        alert_msg = f"🚨 ALERT: {symbol} hit ₹{current_price:.2f}! (Target was ₹{target_price})"
                        send_telegram_message(alert_msg)
                        alerted_stocks[symbol] = True
                else:
                    if symbol in alerted_stocks:
                        del alerted_stocks[symbol]

        except Exception as e:
            print(f"Monitor error: {e}")
            
        # Sleep 15 minutes (900 seconds), then repeat
        time.sleep(900) 

# --- STARTUP LOGIC ---
if __name__ == "__main__":
    # 1. Start background monitor
    monitor_thread = threading.Thread(target=stock_monitor)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # 2. Start web server (Render uses the PORT environment variable)
    port = int(os.environ.get('PORT', 8080))
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)
    