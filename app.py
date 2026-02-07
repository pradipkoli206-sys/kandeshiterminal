import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "वाट पहा..."
ans2_sector = "डेटा चेक करत आहे..."
winning_sector_code = "ALL" 

# --- 2. DATA SETUP (REAL TOKENS ADDED) ---
# आता हे डमी नाहीत, हे Angel One चे खरे NSE Tokens आहेत.
TOKEN_MAP = {
    "NIFTY": "99926000",       # Nifty 50 Index
    "BANKNIFTY": "99926009",   # Bank Nifty Index
    "NIFTY_IT": "99926004",    # Nifty IT Index
    "NIFTY_AUTO": "99926002",  # Nifty Auto Index
    
    # तुझे स्टॉक्स (NSE Tokens)
    "SOUTHBANK": "3351",       # South Indian Bank
    "CENTRALBK": "1563",       # Central Bank
    "UCOBANK": "1164",         # UCO Bank
    "IDFCFIRSTB": "11184",     # IDFC First Bank
    "RTNINDIA": "13425",       # RattanIndia Enterprises
    "OLAELEC": "29135",        # Ola Electric
    "TTML": "3515",            # TTML
    "HFCL": "1363"             # HFCL
}

# Stocks Category (Drawing madhe 'Today Stocks' sathi)
STOCK_CATEGORY = {
    "SOUTHBANK": "BANK", "CENTRALBK": "BANK", "UCOBANK": "BANK", "IDFCFIRSTB": "BANK",
    "OLAELEC": "AUTO", "RTNINDIA": "POWER", "TTML": "IT", "HFCL": "IT"
}

STOCKS = []
for name, token in TOKEN_MAP.items():
    if "NIFTY" not in name:
        cat = STOCK_CATEGORY.get(name, "OTHER")
        STOCKS.append({"name": name, "token": token, "price": "0.00", "cat": cat})

# --- 3. ENGINE ---
def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code
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
                time.sleep(60)
                continue

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(5)
                    continue

            # Sector Logic
            bank_change = -100.0; it_change = -100.0; auto_change = -100.0

            for name, token in TOKEN_MAP.items():
                try:
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    # जर Index असेल (Nifty/BankNifty) तर सिम्बॉल वेगळा असतो
                    if "NIFTY" in name:
                         res = smart_api.ltpData("NSE", name, token)
                    
                    if res and res['status']:
                        ltp = float(res['data']['ltp'])
                        close = float(res['data']['close'])
                        live_data[token] = ltp

                        if start_time <= current_time:
                            change = ltp - close
                            pct_change = (change / close) * 100

                            if name == "NIFTY":
                                ans1_nifty = "🟢 POSITIVE" if change > 0 else "🔴 NEGATIVE"
                            
                            if name == "BANKNIFTY": bank_change = pct_change
                            elif name == "NIFTY_IT": it_change = pct_change
                            elif name == "NIFTY_AUTO": auto_change = pct_change
                except:
                    pass
                time.sleep(0.05)
            
            # Winner Logic (Sector Comparison)
            if bank_change > it_change and bank_change > auto_change:
                ans2_sector = "BANK SECTOR"
                winning_sector_code = "BANK"
            elif it_change > bank_change and it_change > auto_change:
                ans2_sector = "IT SECTOR"
                winning_sector_code = "IT"
            elif auto_change > bank_change and auto_change > it_change:
                ans2_sector = "AUTO SECTOR"
                winning_sector_code = "AUTO"
            else:
                ans2_sector = "MIXED / SIDEWAYS"
                winning_sector_code = "ALL"
            
            time.sleep(1)
        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. HTML TEMPLATE (PHOTO STRUCTURE) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Process Mode</title>
<style>
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; --yellow: #ffcc00; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 20px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }

.header { padding: 10px; background: rgba(10, 17, 24, 0.95); border-bottom: 1px solid var(--neon); text-align: center; }
.status-bar { font-size: 0.8rem; color: #ccc; font-weight: bold; }

/* TOP SECTION (Split 70% - 30%) */
.top-container { display: flex; height: 35%; border-bottom: 1px solid #333; }
.left-panel { flex: 7; padding: 10px; border-right: 1px solid #333; display: flex; flex-direction: column; justify-content: center; }
.right-panel { flex: 3; padding: 10px; overflow-y: auto; background: rgba(0,242,255,0.05); }

/* Q&A Style */
.q-box { margin-bottom: 15px; }
.q-text { color: #aaa; font-size: 0.9rem; margin-bottom: 5px; }
.a-text { color: var(--neon); font-size: 1.2rem; font-weight: 900; text-shadow: 0 0 5px var(--neon); }

/* Right Mini List */
.mini-title { font-size: 0.7rem; color: var(--yellow); text-align: center; margin-bottom: 5px; text-decoration: underline; }
.mini-item { font-size: 0.7rem; color: #fff; border-bottom: 1px solid #333; padding: 3px 0; }

/* FILTER OPTIONS (As per photo) */
.filter-bar { display: flex; padding: 10px; gap: 10px; background: #000; overflow-x: auto; }
.filter-btn { 
    background: #111; border: 1px solid #444; color: #888; padding: 8px 15px; border-radius: 20px; 
    font-size: 0.8rem; font-weight: bold; white-space: nowrap; cursor: pointer; flex: 1; text-align: center;
}
.filter-btn.active { background: var(--neon); color: #000; border-color: var(--neon); box-shadow: 0 0 10px var(--neon); }

/* MAIN LIST */
.main-list { flex: 1; overflow-y: auto; padding: 10px; }
.stock-card { 
    background: var(--card); border: 1px solid #333; padding: 15px; border-radius: 10px; margin-bottom: 10px;
    display: flex; justify-content: space-between; align-items: center; 
}
.st-name { font-size: 1rem; font-weight: bold; color: #fff; }
.st-price { font-size: 1rem; font-weight: bold; color: var(--green); }
.hidden { display: none; }
</style>
<script>
function updateTime(){
    const now = new Date(); 
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
} 
setInterval(updateTime,1000); 

// FILTER LOGIC (Button Click)
function filterStocks(type) {
    // Reset Buttons
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-'+type).classList.add('active');

    const winner = "{{ winner }}"; // Server kadun आलेला winner code
    const cards = document.querySelectorAll('.stock-card');

    cards.forEach(card => {
        const cat = card.getAttribute('data-cat');
        
        if (type === 'ALL') {
            card.classList.remove('hidden');
        } else if (type === 'TODAY') {
            if (winner === 'ALL' || cat === winner) {
                card.classList.remove('hidden');
            } else {
                card.classList.add('hidden');
            }
        } else if (type === 'PREV') {
            // Demo logic for 'Previous' (Currently hiding all)
            card.classList.add('hidden'); 
        }
    });
}
setInterval(function(){ location.reload(); }, 5000);
</script>
</head>
<body>

<div class="header">
    <span class="status-bar" id="time-display">--:--</span> | <span class="status-bar">{{ status }}</span>
</div>

<div class="top-container">
    <div class="left-panel">
        <div class="q-box">
            <div class="q-text">① आज निफ्टी पॉझिटिव्ह आहे की निगेटिव्ह?</div>
            <div class="a-text">{{ ans1 }}</div>
        </div>
        <div class="q-box">
            <div class="q-text">② आज कोणता सेक्टर जोरात आहे?</div>
            <div class="a-text">{{ ans2 }}</div>
        </div>
    </div>

    <div class="right-panel">
        <div class="mini-title">TODAY STOCKS</div>
        {% for stock in stocks %}
            {% if winner == 'ALL' or stock.cat == winner %}
            <div class="mini-item">{{ stock.name }}</div>
            {% endif %}
        {% endfor %}
    </div>
</div>

<div class="filter-bar">
    <div id="btn-ALL" class="filter-btn active" onclick="filterStocks('ALL')">ALL STOCK</div>
    <div id="btn-TODAY" class="filter-btn" onclick="filterStocks('TODAY')">TODAY STOCK</div>
    <div id="btn-PREV" class="filter-btn" onclick="filterStocks('PREV')">PREVIOUS</div>
</div>

<div class="main-list">
    {% for stock in stocks %}
    <div class="stock-card" data-cat="{{ stock.cat }}">
        <div class="st-name">{{ stock.name }}</div>
        <div class="st-price">₹{{ stock.price }}</div>
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
