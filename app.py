import os
import pyotp
import time
import threading
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS FROM RENDER ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

# --- 2. DATA STORE ---
live_data = {} 
TOKEN_MAP = {
    "RELIANCE": "2885", "TATASTEEL": "3499", "HDFCBANK": "1333", "INFY": "1594",
    "SBIN": "3045", "ICICIBANK": "4963", "AXISBANK": "5900", "WIPRO": "3787",
    "ADANIENT": "25", "MARUTI": "10999", "BAJFINANCE": "317", "ASIANPAINT": "236"
}

STOCKS = [{"name": k, "price": 0.00, "sig": "WAIT", "token": v} for k, v in TOKEN_MAP.items()]
SIGNALS = [{"symbol": "SYSTEM", "type": "INFO", "price": "0.00", "time": "LTP MODE ACTIVE"}]

# --- 3. UNBREAKABLE LTP ENGINE ---
def start_engine():
    global live_data
    smart_api = None
    while True:
        try:
            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(10)
                    continue
            
            # दर १ सेकंदाला भाव अपडेट करणे
            for stock in STOCKS:
                try:
                    res = smart_api.ltpData("NSE", stock["name"] + "-EQ", stock["token"])
                    if res and res['status']:
                        live_data[stock["token"]] = res['data']['ltp']
                except:
                    pass
                time.sleep(0.05) # Rate limit safety
            
            time.sleep(1) # Refresh Rate
        except Exception as e:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. YOUR ORIGINAL NEON DESIGN ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>कान्हादेशी ट्रेडर</title>
<style>
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 80px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
.header { padding: 10px 15px; background: rgba(10, 17, 24, 0.95); border-bottom: 2px solid var(--neon); flex-shrink: 0; display: flex; justify-content: space-between; align-items: center; }
h1 { margin: 0; text-shadow: 0 0 10px var(--neon); font-size: 1.4rem; }
.split-container { display: flex; flex: 1; overflow: hidden; width: 100%; }
.panel { width: 50%; padding: 10px; overflow-y: auto; display: flex; flex-direction: column; }
.vertical-separator { width: 2px; background: var(--neon); height: 100%; box-shadow: 0 0 15px var(--neon); }
.pro-card { background: var(--card); border-radius: 8px; margin-bottom: 8px; padding: 10px; display: flex; justify-content: space-between; border: 1px solid rgba(0, 242, 255, 0.2); box-shadow: 0 0 5px var(--neon); }
.stock-name { font-size: 0.85rem; font-weight: 900; color: var(--neon); }
.stock-price { font-size: 0.9rem; font-weight: bold; color: var(--green); }
</style>
<script>setInterval(function(){ location.reload(); }, 2000);</script>
</head>
<body>
<div class="header"><h1>🔱 कान्हादेशी ट्रेडर</h1></div>
<div class="split-container">
<div class="panel">
    <div style="text-align:center; color:var(--neon); margin-bottom:10px;">WATCHLIST</div>
    {% for stock in stocks %}
    <div class="pro-card">
        <div class="stock-name">{{ stock.name }}</div>
        <div class="stock-price">₹{{ stock.price }}</div>
    </div>
    {% endfor %}
</div>
<div class="vertical-separator"></div>
<div class="panel">
    <div style="text-align:center; color:var(--neon); margin-bottom:10px;">SIGNALS</div>
    <div class="pro-card">SYSTEM: ACTIVE</div>
</div>
</div>
</body>
</html>
'''

@app.route('/')
def index():
    for stock in STOCKS:
        stock["price"] = live_data.get(stock["token"], 0.00)
    return render_template_string(HTML_TEMPLATE, stocks=STOCKS)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
