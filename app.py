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
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
data_fetched_once = False

# --- 2. DATA SETUP (CORRECT TOKENS & EXCHANGES) ---
# Southbank BSE var aahe, bakiche NSE var aahet.
TOKEN_MAP = {
    # INDICES
    "NIFTY":      {"token": "99926000", "exch": "BSE", "symbol": "Nifty 50"},
    "BANKNIFTY":  {"token": "99926009", "exch": "BSE", "symbol": "Nifty Bank"},
    "NIFTY_IT":   {"token": "99926004", "exch": "BSE", "symbol": "Nifty IT"},
    "NIFTY_AUTO": {"token": "99926002", "exch": "BSE", "symbol": "Nifty Auto"},

    # STOCKS (Corrected Tokens)
    "SOUTHBANK":  {"token": "532218", "exch": "BSE", "symbol": "SOUTHBANK"}, # BSE Token
    "CENTRALBK":  {"token": "1563",   "exch": "BSE", "symbol": "CENTRALBK"},
    "UCOBANK":    {"token": "1164",   "exch": "BSE", "symbol": "UCOBANK"},
    "IDFCFIRSTB": {"token": "11184",  "exch": "BSE", "symbol": "IDFCFIRSTB"},
    "RTNINDIA":   {"token": "13425",  "exch": "BSE", "symbol": "RTNINDIA"}, # Back to EQ
    "OLAELEC":    {"token": "29135",  "exch": "BSE", "symbol": "OLAELEC"},
    "TTML":       {"token": "3515",   "exch": "BSE", "symbol": "TTML"},     # Back to EQ
    "HFCL":       {"token": "1363",   "exch": "BSE", "symbol": "HFCL"}
}

STOCK_CATEGORY = {
    "SOUTHBANK": "BANK", "CENTRALBK": "BANK", "UCOBANK": "BANK", "IDFCFIRSTB": "BANK",
    "OLAELEC": "AUTO", "RTNINDIA": "POWER", "TTML": "IT", "HFCL": "IT"
}

STOCKS = []
for name, details in TOKEN_MAP.items():
    if "NIFTY" not in name:
        cat = STOCK_CATEGORY.get(name, "OTHER")
        STOCKS.append({"name": name, "token": details["token"], "price": "0.00", "cat": cat})

# --- 3. ENGINE (OPTIMIZED LOGIC) ---
def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code, data_fetched_once
    smart_api = None
    
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            weekday = ist_now.weekday()
            
            start_time = datetime.strptime("09:00", "%H:%M").time()
            end_time = datetime.strptime("15:30", "%H:%M").time()

            is_market_open = (weekday < 5 and start_time <= current_time <= end_time)
            
            if is_market_open:
                market_status = "LIVE"
            else:
                market_status = "CLOSED"

            if not is_market_open and data_fetched_once:
                time.sleep(10)
                continue

            if smart_api is None:
                try:
                    totp = pyotp.TOTP(TOTP_KEY).now()
                    smart_api = SmartConnect(api_key=API_KEY)
                    data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                    if not data['status']:
                        print("Login Failed:", data)
                        time.sleep(5)
                        continue
                except Exception as e:
                    print("Login Error:", e)
                    time.sleep(5)
                    continue

            bank_change = -100.0; it_change = -100.0; auto_change = -100.0

            for name, details in TOKEN_MAP.items():
                try:
                    # Specific Exchange and Symbol from Map
                    exch = details["exch"]
                    symbol = details["symbol"]
                    token = details["token"]

                    res = smart_api.ltpData(exch, symbol, token)
                    
                    if res and res['status']:
                        ltp = float(res['data']['ltp'])
                        close = float(res['data']['close'])
                        live_data[token] = ltp

                        change = ltp - close
                        pct_change = (change / close) * 100
                        
                        if name == "NIFTY": 
                            ans1_nifty = "POSITIVE ▲" if change > 0 else "NEGATIVE ▼"
                        
                        if name == "BANKNIFTY": bank_change = pct_change
                        elif name == "NIFTY_IT": it_change = pct_change
                        elif name == "NIFTY_AUTO": auto_change = pct_change
                    else:
                        pass
                        # print(f"Error for {name}: {res}") 

                except Exception as e:
                    print(f"Exception for {name}: {e}")
                    pass
                
                # IMPORTANT: Slow down to avoid Rate Limit (AB1018 often comes from spamming)
                time.sleep(0.3)
            
            # Sector Logic
            if bank_change > -90 and it_change > -90 and auto_change > -90:
                if bank_change > it_change and bank_change > auto_change:
                    ans2_sector = "BANKING"
                    winning_sector_code = "BANK"
                elif it_change > bank_change and it_change > auto_change:
                    ans2_sector = "IT / TECH"
                    winning_sector_code = "IT"
                elif auto_change > bank_change and auto_change > it_change:
                    ans2_sector = "AUTO"
                    winning_sector_code = "AUTO"
                else:
                    ans2_sector = "MIXED"
                    winning_sector_code = "ALL"
            else:
                 if ans2_sector == "LOADING...":
                     ans2_sector = "WAIT..."

            if not is_market_open:
                data_fetched_once = True
            
            time.sleep(1)
        except Exception as e:
            print("Engine Crash:", e)
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. HTML TEMPLATE ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
/* --- SHARP UI --- */
:root {
    --bg-color: #0b1120;
    --card-bg: #1e293b;
    --border-color: #334155;
    --text-white: #ffffff;
    --text-grey: #94a3b8;
    --green: #22c55e;
    --red: #ef4444;
    --blue: #3b82f6;
}
* { box-sizing: border-box; }
body { 
    background: var(--bg-color); 
    color: var(--text-white); 
    font-family: 'Roboto', sans-serif; 
    margin: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}

/* HEADER */
.header {
    padding: 12px 15px; background: #111827; border-bottom: 1px solid var(--border-color);
    display: flex; justify-content: space-between; align-items: center;
}
.brand { font-weight: 700; font-size: 1rem; color: var(--blue); letter-spacing: 1px; }
.date-time { font-size: 0.8rem; color: var(--text-grey); font-weight: 500; text-align: right; }
.status-ind { font-size: 0.7rem; color: #fff; margin-top:2px; display:flex; align-items:center; justify-content:flex-end; gap:5px;}
.dot { width: 8px; height: 8px; background: var(--green); border-radius: 50%; }

/* TOP SECTION */
.top-container { display: flex; height: 40%; border-bottom: 1px solid var(--border-color); }
.left-panel { flex: 6.5; padding: 20px; display: flex; flex-direction: column; justify-content: center; border-right: 1px solid var(--border-color); background: var(--bg-color); }
.q-box { margin-bottom: 25px; }
.q-label { color: var(--text-grey); font-size: 0.85rem; font-weight: 500; margin-bottom: 5px; }
.a-value { font-size: 1.5rem; font-weight: 700; color: var(--text-white); letter-spacing: 0.5px; }
.green-txt { color: var(--green); }
.red-txt { color: var(--red); }

.right-panel { flex: 3.5; background: #111827; display: flex; flex-direction: column; }
.panel-header { padding: 10px; font-size: 0.75rem; font-weight: 700; text-align: center; border-bottom: 1px solid var(--border-color); color: var(--blue); background: #1f2937; }
.mini-list-content { overflow-y: auto; flex: 1; }
.mini-item { font-size: 0.85rem; padding: 10px; border-bottom: 1px solid #1f2937; color: var(--text-white); font-weight: 500; }

/* FILTER BAR */
.filter-bar { padding: 10px 15px; display: flex; gap: 10px; background: #0f172a; border-bottom: 1px solid var(--border-color); }
.filter-btn { flex: 1; background: #1e293b; border: 1px solid var(--border-color); color: var(--text-grey); padding: 8px 0; border-radius: 6px; font-size: 0.8rem; font-weight: 500; cursor: pointer; text-align: center; }
.filter-btn.active { background: var(--blue); color: white; border-color: var(--blue); }

/* MAIN LIST */
.main-list { flex: 1; overflow-y: auto; padding: 15px; background: var(--bg-color); }
.stock-card { background: var(--card-bg); border: 1px solid var(--border-color); padding: 15px; border-radius: 8px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
.st-name { font-size: 1rem; font-weight: 700; color: var(--text-white); }
.st-cat { font-size: 0.7rem; color: var(--text-grey); margin-top: 3px; }
.st-price { font-size: 1.1rem; font-weight: 700; color: var(--green); }
.hidden { display: none !important; }
</style>
<script>
let currentWinner = "ALL";
let activeFilter = "ALL";

function updateTime(){
    const now = new Date();
    const dateStr = now.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: '2-digit'});
    const timeStr = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
    document.getElementById('dt-disp').innerText = dateStr + " | " + timeStr;
} 
setInterval(updateTime,1000); 

function filterStocks(type) {
    activeFilter = type;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-'+type).classList.add('active');
    applyFilter();
}

function applyFilter() {
    const cards = document.querySelectorAll('.stock-card');
    cards.forEach(card => {
        const cat = card.getAttribute('data-cat');
        let show = false;
        if (activeFilter === 'ALL') show = true;
        else if (activeFilter === 'TODAY') {
            if (currentWinner === 'ALL' || cat === currentWinner) show = true;
        }
        if(show) card.classList.remove('hidden'); else card.classList.add('hidden');
    });
    
    // Update Mini List
    const miniItems = document.querySelectorAll('.mini-item');
    miniItems.forEach(item => {
        const cat = item.getAttribute('data-cat');
        if (currentWinner === 'ALL' || cat === currentWinner) item.style.display = 'block'; else item.style.display = 'none';
    });
}

function fetchData() {
    fetch('/data')
    .then(response => response.json())
    .then(data => {
        document.getElementById('status-disp').innerText = data.status;
        
        const ans1El = document.getElementById('ans1-disp');
        ans1El.innerText = data.ans1;
        if(data.ans1.includes("POSITIVE")) { ans1El.className = "a-value green-txt"; }
        else if(data.ans1.includes("NEGATIVE")) { ans1El.className = "a-value red-txt"; }
        else { ans1El.className = "a-value"; }

        document.getElementById('ans2-disp').innerText = data.ans2;
        currentWinner = data.winner;
        
        data.stocks.forEach(s => {
            const el = document.getElementById('price-' + s.name);
            if(el) el.innerText = "₹" + s.price;
        });
        applyFilter();
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
        <div class="q-box">
            <div class="q-label">01. MARKET TREND</div>
            <div class="a-value" id="ans1-disp">{{ ans1 }}</div>
        </div>
        <div class="q-box">
            <div class="q-label">02. TOP SECTOR</div>
            <div class="a-value" style="color:var(--blue);" id="ans2-disp">{{ ans2 }}</div>
        </div>
    </div>
    <div class="right-panel">
        <div class="panel-header">🚀 TODAY'S PICKS</div>
        <div class="mini-list-content">
            {% for stock in stocks %}
            <div class="mini-item" data-cat="{{ stock.cat }}">{{ stock.name }}</div>
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
        <div class="st-info">
            <span class="st-name">{{ stock.name }}</span>
            <span class="st-cat">{{ stock.cat }}</span>
        </div>
        <div class="st-price" id="price-{{ stock.name }}">₹{{ stock.price }}</div>
    </div>
    {% endfor %}
</div>

</body>
</html>
'''

# --- 5. ROUTES ---
@app.route('/')
def index():
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
    
    return jsonify({
        "status": market_status,
        "ans1": ans1_nifty,
        "ans2": ans2_sector,
        "winner": winning_sector_code,
        "stocks": STOCKS
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
