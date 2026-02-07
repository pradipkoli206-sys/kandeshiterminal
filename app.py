import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..."

# --- 2. DATA SETUP (Only Tokens Updated, Logic is Old) ---
TOKEN_MAP = {
    # INDICES
    "NIFTY": "99926000",       
    "BANKNIFTY": "99926009",   
    "NIFTY_IT": "99926004",    
    "NIFTY_AUTO": "99926002",  

    # STOCKS (Updated Tokens to match Angel One)
    "SOUTHBANK": "3351",       
    "CENTRALBK": "17835",       
    "UCOBANK": "1164",         
    "IDFCFIRSTB": "11184",     
    "RTNINDIA": "13425",       
    "OLAELEC": "29135",        
    "TTML": "3515",            
    "HFCL": "1363"             
}

STOCK_CATEGORY = {
    "SOUTHBANK": "BANK", "CENTRALBK": "BANK", "UCOBANK": "BANK", "IDFCFIRSTB": "BANK",
    "OLAELEC": "AUTO", "RTNINDIA": "POWER", "TTML": "IT", "HFCL": "IT"
}

STOCKS = []
for name, token in TOKEN_MAP.items():
    if "NIFTY" not in name:
        cat = STOCK_CATEGORY.get(name, "OTHER")
        STOCKS.append({"name": name, "token": token, "price": "0.00", "cat": cat})

# --- 3. ENGINE (EXACT OLD LOGIC) ---
def start_engine():
    global live_data, market_status
    smart_api = None
    
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            weekday = ist_now.weekday()
            
            start_time = datetime.strptime("09:00", "%H:%M").time()
            end_time = datetime.strptime("15:30", "%H:%M").time()

            if weekday < 5 and start_time <= current_time <= end_time:
                market_status = "LIVE MARKET"
            else:
                market_status = "MARKET CLOSED"
                if len(live_data) > 0: # Data ekda aala asel tar loop chalu theva
                    time.sleep(10)
                    continue

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(5)
                    continue

            for name, token in TOKEN_MAP.items():
                try:
                    # --- OLD STYLE SYMBOL MAPPING ---
                    # Only simple fix for Indices, rest is name + "-EQ" (Old Logic)
                    if name == "NIFTY": symbol = "Nifty 50"
                    elif name == "BANKNIFTY": symbol = "Nifty Bank"
                    elif name == "NIFTY_IT": symbol = "Nifty IT"
                    elif name == "NIFTY_AUTO": symbol = "Nifty Auto"
                    else: symbol = name + "-EQ"

                    res = smart_api.ltpData("NSE", symbol, token)
                    
                    if res and res['status']:
                        # OLD LOGIC: Save by TOKEN (Juna code yach padhatine save karat hota)
                        live_data[token] = res['data']['ltp']
                except:
                    pass
                time.sleep(0.05)
            time.sleep(1)
        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. HTML TEMPLATE (New UI, Old Data Binding) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root { --bg-color: #0b1120; --card-bg: #1e293b; --border-color: #334155; --text-white: #ffffff; --text-grey: #94a3b8; --green: #22c55e; --red: #ef4444; --blue: #3b82f6; }
* { box-sizing: border-box; }
body { background: var(--bg-color); color: var(--text-white); font-family: 'Roboto', sans-serif; margin: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
.header { padding: 12px 15px; background: #111827; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; }
.brand { font-weight: 700; font-size: 1rem; color: var(--blue); letter-spacing: 1px; }
.date-time { font-size: 0.8rem; color: var(--text-grey); font-weight: 500; text-align: right; }
.status-ind { font-size: 0.7rem; color: #fff; margin-top:2px; display:flex; align-items:center; justify-content:flex-end; gap:5px;}
.dot { width: 8px; height: 8px; background: var(--green); border-radius: 50%; }
.top-container { display: flex; height: 40%; border-bottom: 1px solid var(--border-color); }
.left-panel { flex: 6.5; padding: 20px; display: flex; flex-direction: column; justify-content: center; border-right: 1px solid var(--border-color); background: var(--bg-color); }
.q-box { margin-bottom: 25px; }
.q-label { color: var(--text-grey); font-size: 0.85rem; font-weight: 500; margin-bottom: 5px; }
.a-value { font-size: 1.5rem; font-weight: 700; color: var(--text-white); letter-spacing: 0.5px; }
.right-panel { flex: 3.5; background: #111827; display: flex; flex-direction: column; }
.panel-header { padding: 10px; font-size: 0.75rem; font-weight: 700; text-align: center; border-bottom: 1px solid var(--border-color); color: var(--blue); background: #1f2937; }
.mini-list-content { overflow-y: auto; flex: 1; }
.mini-item { font-size: 0.85rem; padding: 10px; border-bottom: 1px solid #1f2937; color: var(--text-white); font-weight: 500; }
.filter-bar { padding: 10px 15px; display: flex; gap: 10px; background: #0f172a; border-bottom: 1px solid var(--border-color); }
.filter-btn { flex: 1; background: #1e293b; border: 1px solid var(--border-color); color: var(--text-grey); padding: 8px 0; border-radius: 6px; font-size: 0.8rem; font-weight: 500; cursor: pointer; text-align: center; }
.filter-btn.active { background: var(--blue); color: white; border-color: var(--blue); }
.main-list { flex: 1; overflow-y: auto; padding: 15px; background: var(--bg-color); }
.stock-card { background: var(--card-bg); border: 1px solid var(--border-color); padding: 15px; border-radius: 8px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
.st-name { font-size: 1rem; font-weight: 700; color: var(--text-white); }
.st-cat { font-size: 0.7rem; color: var(--text-grey); margin-top: 3px; }
.st-price { font-size: 1.1rem; font-weight: 700; color: var(--green); }
.hidden { display: none !important; }
</style>
<script>
function updateTime(){
    const now = new Date();
    document.getElementById('dt-disp').innerText = now.toLocaleDateString('en-GB') + " | " + now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
} 
setInterval(updateTime,1000); 

function filterStocks(type) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-'+type).classList.add('active');
    const cards = document.querySelectorAll('.stock-card');
    cards.forEach(card => {
        const cat = card.getAttribute('data-cat');
        if (type === 'ALL' || type === 'TODAY' || type === 'PREV') card.classList.remove('hidden'); 
        // Logic simple thevle ahe currently
    });
}

function fetchData() {
    fetch('/data')
    .then(response => response.json())
    .then(data => {
        document.getElementById('status-disp').innerText = data.status;
        data.stocks.forEach(s => {
            const el = document.getElementById('price-' + s.token); // OLD LOGIC: ID token varun shodha
            if(el) el.innerText = "₹" + s.price;
        });
    });
}
setInterval(fetchData, 1000); 
</script>
</head>
<body>
<div class="header">
    <div class="brand">AI TRADER</div>
    <div>
        <div class="date-time" id="dt-disp">-- | --:--</div>
        <div class="status-ind"><span class="dot"></span><span id="status-disp">{{ status }}</span></div>
    </div>
</div>
<div class="top-container">
    <div class="left-panel">
        <div class="q-box"><div class="q-label">01. MARKET TREND</div><div class="a-value">WAIT...</div></div>
        <div class="q-box"><div class="q-label">02. TOP SECTOR</div><div class="a-value" style="color:var(--blue);">LOADING...</div></div>
    </div>
    <div class="right-panel">
        <div class="panel-header">🚀 TODAY'S PICKS</div>
        <div class="mini-list-content">
            {% for stock in stocks %}
            <div class="mini-item">{{ stock.name }}</div>
            {% endfor %}
        </div>
    </div>
</div>
<div class="filter-bar">
    <div id="btn-ALL" class="filter-btn active" onclick="filterStocks('ALL')">ALL STOCKS</div>
    <div id="btn-TODAY" class="filter-btn" onclick="filterStocks('TODAY')">TODAY'S PICK</div>
    <div id="btn-PREV" class="filter-btn" onclick="filterStocks('PREV')">PREVIOUS</div>
</div>
<div class="main-list">
    {% for stock in stocks %}
    <div class="stock-card" id="card-{{ stock.name }}" data-cat="{{ stock.cat }}">
        <div class="st-info"><span class="st-name">{{ stock.name }}</span><span class="st-cat">{{ stock.cat }}</span></div>
        <div class="st-price" id="price-{{ stock.token }}">₹{{ stock.price }}</div>
    </div>
    {% endfor %}
</div>
</body>
</html>
'''

# --- 5. ROUTES (EXACT OLD LOGIC) ---
@app.route('/')
def index():
    # OLD LOGIC: Update price from live_data using TOKEN
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
    return render_template_string(HTML_TEMPLATE, status=market_status, stocks=STOCKS)

@app.route('/data')
def data():
    stock_list = []
    # OLD LOGIC: Create list using TOKEN match
    for stock in STOCKS:
        token = stock["token"]
        price = live_data.get(token, "0.00")
        stock_list.append({"token": token, "price": price})
    
    return jsonify({
        "status": market_status,
        "stocks": stock_list
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
