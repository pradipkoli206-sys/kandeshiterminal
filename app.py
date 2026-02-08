import os
import pyotp
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS (UNTOUCHED) ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
data_fetched_once = False
tokens_loaded = False 

# --- 2. STOCK CONFIGURATION ---
TARGET_STOCKS = [
    {"name": "SOUTHBANK", "cat": "BANK"},
    {"name": "CENTRALBK", "cat": "BANK"},
    {"name": "UCOBANK",   "cat": "BANK"},
    {"name": "IDFCFIRSTB","cat": "BANK"},
    {"name": "RTNINDIA",  "cat": "POWER"},
    {"name": "OLAELEC",   "cat": "AUTO"},
    {"name": "TTML",      "cat": "IT"},
    {"name": "HFCL",      "cat": "IT"}
]

INDICES_LIST = [
    {"name": "NIFTY",      "token": "99926000", "symbol": "Nifty 50"},
    {"name": "BANKNIFTY",  "token": "99926009", "symbol": "Nifty Bank"},
    {"name": "NIFTY_IT",   "token": "99926004", "symbol": "Nifty IT"},
    {"name": "NIFTY_AUTO", "token": "99926002", "symbol": "Nifty Auto"}
]

# *** FIX: सुरुवातीलाच लिस्ट भरा म्हणजे स्क्रीन रिकामी दिसणार नाही ***
STOCKS = [{"name": s["name"], "cat": s["cat"], "token": None, "price": "WAIT...", "symbol": s["name"]} for s in TARGET_STOCKS]

# --- 3. AUTO-SCANNER ---
def fetch_correct_tokens(smart_api):
    global STOCKS
    print("\n>>> STARTING NSE TOKEN SCANNER (SAFE MODE) <<<")
    temp_stocks = []
    
    for item in TARGET_STOCKS:
        name = item["name"]
        cat = item["cat"]
        time.sleep(1.2) 
        try:
            search_response = smart_api.searchScrip("NSE", name)
            scrip_list = []
            if search_response and isinstance(search_response, dict) and 'data' in search_response:
                scrip_list = search_response['data']
            elif search_response and isinstance(search_response, list):
                scrip_list = search_response

            found_token = None
            found_symbol = None
            if scrip_list:
                for s in scrip_list:
                    if s['tradingsymbol'] == name + "-EQ":
                        found_token = s['symboltoken']; found_symbol = s['tradingsymbol']
                        break
                if not found_token:
                    for s in scrip_list:
                        if s['tradingsymbol'] == name + "-BE":
                            found_token = s['symboltoken']; found_symbol = s['tradingsymbol']
                            break
            if found_token:
                print(f"✅ FOUND: {name}")
                temp_stocks.append({"name": name, "token": found_token, "symbol": found_symbol, "cat": cat, "price": "0.00"})
            else:
                print(f"❌ FAILED: {name}")
                temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR"})
        except:
            temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR"})
    
    # अपडेट झाल्यावर जुन्या लिस्टला नवीन डेटाने बदला
    STOCKS = temp_stocks
    print(">>> SCAN COMPLETE <<<\n")

# --- 4. ENGINE ---
def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code, data_fetched_once, tokens_loaded
    smart_api = None
    last_ping_time = time.time()

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            is_weekday = ist_now.weekday() < 5
            
            market_open = datetime.strptime("09:00", "%H:%M").time()
            market_close = datetime.strptime("15:35", "%H:%M").time()
            
            is_market_active = is_weekday and (market_open <= current_time <= market_close)

            if time.time() - last_ping_time > 600:
                if RENDER_URL:
                    try:
                        requests.get(RENDER_URL)
                        print(">>> SELF PING SUCCESSFUL (Keep-Alive) <<<")
                    except:
                        pass
                last_ping_time = time.time()

            if not is_market_active:
                market_status = "CLOSED"
                time.sleep(60)
                continue 

            market_status = "LIVE"

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if data['status'] and not tokens_loaded:
                    fetch_correct_tokens(smart_api); tokens_loaded = True

            if not tokens_loaded: time.sleep(1); continue

            bank_change = -100.0; it_change = -100.0; auto_change = -100.0

            for ind in INDICES_LIST:
                try:
                    res = smart_api.ltpData("NSE", ind["symbol"], ind["token"])
                    if res and res['status']:
                        ltp = float(res['data']['ltp']); close = float(res['data']['close'])
                        change = ltp - close; pct = (change / close) * 100
                        if ind["name"] == "NIFTY": ans1_nifty = "POSITIVE ▲" if change > 0 else "NEGATIVE ▼"
                        if ind["name"] == "BANKNIFTY": bank_change = pct
                        elif ind["name"] == "NIFTY_IT": it_change = pct
                        elif ind["name"] == "NIFTY_AUTO": auto_change = pct
                except: pass
                time.sleep(0.3)

            for stock in STOCKS:
                if stock.get("token"):
                    try:
                        res = smart_api.ltpData("NSE", stock["symbol"], stock["token"])
                        if res and res['status']:
                            ltp = float(res['data']['ltp'])
                            live_data[stock["token"]] = ltp; stock["price"] = ltp
                    except: pass
                time.sleep(0.5)

            if bank_change > it_change and bank_change > auto_change:
                ans2_sector = "BANKING"; winning_sector_code = "BANK"
            elif it_change > bank_change and it_change > auto_change:
                ans2_sector = "IT / TECH"; winning_sector_code = "IT"
            elif auto_change > bank_change and auto_change > it_change:
                ans2_sector = "AUTO"; winning_sector_code = "AUTO"
            else:
                ans2_sector = "MIXED"; winning_sector_code = "ALL"
            
            time.sleep(1)
        except:
            smart_api = None; time.sleep(5)

t = threading.Thread(target=start_engine); t.daemon = True; t.start()

# --- 5. ROUTES ---
@app.route('/')
def index():
    # डेटा अपडेट करा
    for s in STOCKS:
        if s.get("token"): s["price"] = live_data.get(s["token"], "WAIT...")
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS)

@app.route('/data')
def data():
    for s in STOCKS:
        if s.get("token"): s["price"] = live_data.get(s["token"], "WAIT...")
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})

# --- 6. WHATSAPP STYLE TEMPLATE (EXACT REPLICA) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhatsApp Stocks</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
<style>
/* --- WHATSAPP DARK THEME --- */
:root {
    --wa-bg: #0b141a;
    --wa-header: #1f2c34;
    --wa-item-hover: #101d25;
    --wa-text-primary: #e9edef;
    --wa-text-secondary: #8696a0;
    --wa-accent: #00a884; 
    --wa-check-blue: #53bdeb;
    --wa-divider: #202c33;
    --wa-fab: #00a884;
    --wa-bottom-bar: #1f2c34;
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { 
    background-color: var(--wa-bg); 
    color: var(--wa-text-primary); 
    font-family: 'Roboto', sans-serif; 
    margin: 0; height: 100vh; display: flex; flex-direction: column; 
    overflow: hidden;
}

/* HEADER */
.wa-header {
    background-color: var(--wa-header);
    padding: 10px 16px;
    display: flex; flex-direction: column; gap: 15px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
    z-index: 10;
}
.top-bar { display: flex; justify-content: space-between; align-items: center; }
.logo { font-size: 22px; font-weight: 500; color: var(--wa-text-secondary); }
.header-icons { display: flex; gap: 20px; color: var(--wa-text-secondary); }
.material-icons { font-size: 24px; cursor: pointer; }

/* FILTER CHIPS (Like Top Tabs) */
.filter-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 5px; }
.chip {
    background: #2a3942; color: var(--wa-text-secondary);
    padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500;
    white-space: nowrap; cursor: pointer; border: 1px solid transparent;
}
.chip.active { background: #0a332c; color: #00a884; border-color: rgba(0,168,132,0.3); }

/* CHAT LIST (STOCK LIST) */
.chat-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
.chat-item {
    display: flex; align-items: center; padding: 12px 16px;
    cursor: pointer; position: relative;
}
.chat-item:active { background: var(--wa-item-hover); }

/* AVATAR */
.avatar {
    width: 45px; height: 45px; border-radius: 50%;
    background: #607d8b; display: flex; justify-content: center; align-items: center;
    font-size: 18px; font-weight: 600; color: white; margin-right: 15px;
    flex-shrink: 0;
}

/* CONTENT */
.chat-info { flex: 1; display: flex; flex-direction: column; gap: 4px; overflow: hidden; }
.row-top { display: flex; justify-content: space-between; align-items: baseline; }
.stock-name { font-size: 17px; font-weight: 500; color: var(--wa-text-primary); }
.current-price { 
    color: var(--wa-accent); font-weight: 500; font-size: 13px; 
}
/* Making price look like the time in WhatsApp */

.row-bottom { display: flex; justify-content: space-between; align-items: center; }
.last-msg { 
    font-size: 14px; color: var(--wa-text-secondary); 
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; 
    display: flex; align-items: center; gap: 4px;
}
.badge {
    background: var(--wa-accent); color: #0b141a;
    font-size: 10px; font-weight: 700; padding: 3px 6px; border-radius: 10px;
    min-width: 18px; text-align: center; display: inline-block;
}
.hidden-badge { display: none; }

/* FLOATING BUTTON */
.fab {
    position: fixed; bottom: 85px; right: 20px;
    width: 50px; height: 50px; border-radius: 14px;
    background: var(--wa-fab); color: #0b141a;
    display: flex; justify-content: center; align-items: center;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3); cursor: pointer;
    z-index: 20;
}

/* BOTTOM NAVBAR */
.bottom-nav {
    height: 75px; background: var(--wa-bottom-bar);
    border-top: 1px solid #2a3942;
    display: flex; justify-content: space-around; align-items: center;
    padding-bottom: 12px;
}
.nav-item {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    color: var(--wa-text-secondary); cursor: pointer; flex: 1;
}
.nav-item.active { color: var(--wa-text-primary); }
.nav-item.active .pill-indicator {
    background: rgba(0, 168, 132, 0.2); 
    padding: 4px 20px; border-radius: 16px;
}
.nav-icon { font-size: 24px; margin-bottom: 0; }
.nav-label { font-size: 12px; font-weight: 500; }
.nav-item.active .nav-icon { color: var(--wa-text-primary); }

/* Animation */
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.animate-price { animation: fadeIn 0.5s ease-in-out; }

</style>
<script>
let currentFilter = 'ALL';

function filterStocks(type) {
    currentFilter = type;
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    document.getElementById('chip-'+type).classList.add('active');
    
    document.querySelectorAll('.chat-item').forEach(item => {
        const cat = item.getAttribute('data-cat');
        if (type === 'ALL' || cat === type) item.style.display = 'flex';
        else item.style.display = 'none';
    });
}

function setActiveNav(el) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
}

function fetchData() {
    fetch('/data')
    .then(response => response.json())
    .then(data => {
        data.stocks.forEach(s => {
            const priceEl = document.getElementById('price-' + s.name);
            const badgeEl = document.getElementById('badge-' + s.name);
            
            if(priceEl) {
                // Formatting price
                const displayPrice = (s.price === "WAIT..." || s.price === "0.00") ? s.price : "₹" + s.price;
                priceEl.innerText = displayPrice;
                
                // Animate only if price changed (simple check)
                priceEl.classList.remove('animate-price');
                void priceEl.offsetWidth; 
                priceEl.classList.add('animate-price');
            }
            
            // Show badge if price is live
            if(badgeEl) {
                if(s.price !== "0.00" && s.price !== "WAIT..." && s.price !== "ERR") {
                    badgeEl.classList.remove('hidden-badge');
                }
            }
        });
    });
}
setInterval(fetchData, 2000);
</script>
</head>
<body>

<div class="wa-header">
    <div class="top-bar">
        <div class="logo">WhatsApp</div>
        <div class="header-icons">
            <span class="material-icons">camera_alt</span>
            <span class="material-icons">search</span>
            <span class="material-icons">more_vert</span>
        </div>
    </div>
    
    <div class="filter-row">
        <div id="chip-ALL" class="chip active" onclick="filterStocks('ALL')">All</div>
        <div id="chip-BANK" class="chip" onclick="filterStocks('BANK')">Banking</div>
        <div id="chip-IT" class="chip" onclick="filterStocks('IT')">IT Sector</div>
        <div id="chip-AUTO" class="chip" onclick="filterStocks('AUTO')">Auto</div>
    </div>
</div>

<div class="chat-list">
    {% for s in stocks %}
    <div class="chat-item" data-cat="{{ s.cat }}">
        <div class="avatar" style="background: {% if s.cat=='BANK' %}#00a884{% elif s.cat=='IT' %}#34b7f1{% else %}#607d8b{% endif %};">
            {{ s.name[0] }}
        </div>
        
        <div class="chat-info">
            <div class="row-top">
                <span class="stock-name">{{ s.name }}</span>
                <span class="current-price" id="price-{{ s.name }}">{{ s.price }}</span>
            </div>
            <div class="row-bottom">
                <div class="last-msg">
                    <span class="material-icons" style="font-size:14px; color:#53bdeb;">done_all</span>
                    {{ s.cat }} Sector Update
                </div>
                <div class="badge hidden-badge" id="badge-{{ s.name }}">1</div>
            </div>
        </div>
    </div>
    <div style="height:1px; background:#202c33; margin-left:75px;"></div>
    {% endfor %}
</div>

<div class="fab">
    <span class="material-icons" style="font-size:24px; color:#0b141a;">add_comment</span>
</div>

<div class="bottom-nav">
    <div class="nav-item active" onclick="setActiveNav(this)">
        <div class="pill-indicator"><span class="material-icons nav-icon">chat</span></div>
        <span class="nav-label">All Stocks</span>
    </div>
    
    <div class="nav-item" onclick="setActiveNav(this)">
        <div class="pill-indicator"><span class="material-icons nav-icon">donut_large</span></div>
        <span class="nav-label">Today Stock</span>
    </div>
    
    <div class="nav-item" onclick="setActiveNav(this)">
        <div class="pill-indicator"><span class="material-icons nav-icon">groups</span></div>
        <span class="nav-label">Signal</span>
    </div>
    
    <div class="nav-item" onclick="setActiveNav(this)">
        <div class="pill-indicator"><span class="material-icons nav-icon">call</span></div>
        <span class="nav-label">Prev Day</span>
    </div>
</div>

</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
