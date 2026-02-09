import os
import pyotp
import time
import threading
import requests # 1. हे नवीन ऍड केले आहे (वेबसाईट झोपू नये म्हणून)
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS (UNTOUCHED) ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") # 2. रेंडरची लिंक घेण्यासाठी

live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
data_fetched_once = False
tokens_loaded = False 

# --- 2. STOCK CONFIGURATION (UNTOUCHED) ---
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

STOCKS = []

# --- 3. AUTO-SCANNER (UNTOUCHED - SAFE MODE) ---
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
    STOCKS = temp_stocks
    print(">>> SCAN COMPLETE <<<\n")

# --- 4. ENGINE (MARKET TIME + KEEP ALIVE ADDED) ---
def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code, data_fetched_once, tokens_loaded
    smart_api = None
    last_ping_time = time.time() # 3. हे टाइमर आहे (वेबसाईट चालू ठेवण्यासाठी)

    while True:
        try:
            # --- 4. MARKET TIME CHECK ---
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            is_weekday = ist_now.weekday() < 5 # सोमवार ते शुक्रवार
            
            # मार्केट वेळ: सकाळी 09:00 ते दुपारी 03:35
            market_open = datetime.strptime("09:00", "%H:%M").time()
            market_close = datetime.strptime("15:35", "%H:%M").time()
            
            is_market_active = is_weekday and (market_open <= current_time <= market_close)

            # --- 5. KEEP ALIVE LOGIC (PING) ---
            # दर १० मिनिटांनी (६०० सेकंद) वेबसाईटला स्वतः पिंग करा
            if time.time() - last_ping_time > 600:
                if RENDER_URL:
                    try:
                        requests.get(RENDER_URL)
                        print(">>> SELF PING SUCCESSFUL (Keep-Alive) <<<")
                    except:
                        pass
                last_ping_time = time.time()

            # जर मार्केट बंद असेल, तर डेटा फेच करू नका (फक्त झोपा)
            if not is_market_active:
                market_status = "CLOSED"
                time.sleep(60) # १ मिनिट थांबा
                continue 

            market_status = "LIVE"

            # --- 6. ORIGINAL LOGIC (UNTOUCHED) ---
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
                if stock["token"]:
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
    for s in STOCKS:
        if s["token"]: s["price"] = live_data.get(s["token"], "0.00")
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS)

@app.route('/data')
def data():
    for s in STOCKS:
        if s["token"]: s["price"] = live_data.get(s["token"], "0.00")
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})

# --- 6. NEW PREMIUM DESIGN (HTML/CSS) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER PRO</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
/* --- PREMIUM THEME --- */
:root {
    --bg-main: #0a0e17;
    --bg-card: #151a25;
    --text-main: #ffffff;
    --text-muted: #8b9bb4;
    --border: #2a3441;
    --accent-blue: #3772ff;
    --accent-green: #00e396;
    --accent-red: #ff4560;
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { 
    background-color: var(--bg-main); 
    color: var(--text-main); 
    font-family: 'Inter', sans-serif; 
    margin: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}

/* HEADER */
.header {
    padding: 15px 20px; 
    background: rgba(21, 26, 37, 0.95); 
    border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center;
    backdrop-filter: blur(10px);
}
.brand { font-size: 18px; font-weight: 800; letter-spacing: 0.5px; background: linear-gradient(45deg, #3772ff, #9b5de5); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.status-badge { font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px; background: #1c222e; color: var(--text-muted); border: 1px solid var(--border); }

/* TOP METRICS */
.dashboard { display: flex; border-bottom: 1px solid var(--border); height: 35%; }
.metric-panel { flex: 1; padding: 20px; display: flex; flex-direction: column; justify-content: center; border-right: 1px solid var(--border); }
.metric-label { color: var(--text-muted); font-size: 11px; font-weight: 600; letter-spacing: 1px; margin-bottom: 8px; text-transform: uppercase; }
.metric-value { font-size: 22px; font-weight: 700; line-height: 1.2; }
.txt-green { color: var(--accent-green); }
.txt-red { color: var(--accent-red); }

/* SIDE PANEL (TODAY'S PICK) */
.side-panel { width: 35%; background: #11151e; border-left: 1px solid var(--border); display: flex; flex-direction: column; }
.panel-head { padding: 12px; font-size: 11px; font-weight: 700; color: var(--accent-blue); text-align: center; border-bottom: 1px solid var(--border); background: #161b26; letter-spacing: 0.5px; }
.pick-list { overflow-y: auto; flex: 1; }
.pick-item { padding: 10px 15px; font-size: 12px; font-weight: 500; border-bottom: 1px solid #1e2532; color: #cbd5e1; }

/* FILTERS */
.filter-bar { padding: 12px 15px; display: flex; gap: 10px; background: var(--bg-main); border-bottom: 1px solid var(--border); }
.chip { flex: 1; background: var(--bg-card); border: 1px solid var(--border); color: var(--text-muted); padding: 10px; border-radius: 8px; font-size: 11px; font-weight: 600; text-align: center; cursor: pointer; transition: all 0.2s; }
.chip.active { background: var(--accent-blue); color: white; border-color: var(--accent-blue); box-shadow: 0 4px 12px rgba(55, 114, 255, 0.3); }

/* STOCK LIST */
.market-list { flex: 1; overflow-y: auto; padding: 15px; }
.stock-card { 
    background: var(--bg-card); 
    border: 1px solid var(--border); 
    padding: 16px; 
    border-radius: 12px; 
    margin-bottom: 12px; 
    display: flex; justify-content: space-between; align-items: center;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}
.st-info { display: flex; flex-direction: column; }
.st-name { font-size: 14px; font-weight: 700; color: var(--text-main); margin-bottom: 4px; }
.st-cat { font-size: 10px; font-weight: 600; color: var(--text-muted); background: #1e2532; padding: 3px 6px; border-radius: 4px; width: fit-content; }
.st-price { font-size: 16px; font-weight: 700; color: var(--text-main); letter-spacing: 0.5px; }
.hidden { display: none !important; }

/* ANIMATION */
@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
.loading { animation: pulse 1.5s infinite; }
</style>
<script>
let currentWinner = "ALL";
let activeFilter = "ALL";

function filterStocks(type) {
    activeFilter = type;
    document.querySelectorAll('.chip').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-'+type).classList.add('active');
    applyFilter();
}

function applyFilter() {
    const cards = document.querySelectorAll('.stock-card');
    cards.forEach(card => {
        const cat = card.getAttribute('data-cat');
        let show = false;
        if (activeFilter === 'ALL') show = true;
        else if (activeFilter === 'TODAY') show = true;
        else if (activeFilter === 'AI') {
            if (currentWinner === 'ALL' || cat === currentWinner) show = true;
        }
        if(show) card.classList.remove('hidden'); else card.classList.add('hidden');
    });
}

function fetchData() {
    fetch('/data')
    .then(response => response.json())
    .then(data => {
        document.getElementById('status-disp').innerText = data.status;
        
        const ans1 = document.getElementById('ans1-disp');
        ans1.innerHTML = data.ans1;
        if(data.ans1.includes("POSITIVE")) ans1.className = "metric-value txt-green";
        else if(data.ans1.includes("NEGATIVE")) ans1.className = "metric-value txt-red";
        else ans1.className = "metric-value loading";

        document.getElementById('ans2-disp').innerText = data.ans2;
        currentWinner = data.winner;
        
        data.stocks.forEach(s => {
            const el = document.getElementById('price-' + s.name);
            if(el) {
                el.innerText = "₹" + s.price;
                // Simple color logic based on price (optional)
                el.style.color = (s.price !== "0.00" && s.price !== "ERR") ? "#00e396" : "#ffffff";
            }
        });
        applyFilter();
    });
}
setInterval(fetchData, 2000);
</script>
</head>
<body>

<div class="header">
    <div class="brand">AI TRADER PRO</div>
    <div class="status-badge" id="status-disp">CONNECTING...</div>
</div>

<div class="dashboard">
    <div class="metric-panel">
        <div class="metric-label">MARKET TREND</div>
        <div class="metric-value" id="ans1-disp">WAIT...</div>
    </div>
    <div class="metric-panel" style="border-right: none;">
        <div class="metric-label">STRONGEST SECTOR</div>
        <div class="metric-value" style="color: var(--accent-blue);" id="ans2-disp">LOADING...</div>
    </div>
    <div class="side-panel">
        <div class="panel-head">🔥 HOT PICKS</div>
        <div class="pick-list">
            {% for s in stocks %}
            <div class="pick-item">{{ s.name }}</div>
            {% endfor %}
        </div>
    </div>
</div>

<div class="filter-bar">
    <div id="btn-ALL" class="chip active" onclick="filterStocks('ALL')">ALL STOCKS</div>
    <div id="btn-TODAY" class="chip" onclick="filterStocks('TODAY')">TODAY STOCK</div>
    <div id="btn-AI" class="chip" onclick="filterStocks('AI')">🚀 AI PICK</div>
</div>

<div class="market-list">
    {% for s in stocks %}
    <div class="stock-card" id="card-{{ s.name }}" data-cat="{{ s.cat }}">
        <div class="st-info">
            <span class="st-name">{{ s.name }}</span>
            <span class="st-cat">{{ s.cat }}</span>
        </div>
        <div class="st-price" id="price-{{ s.name }}">₹{{ s.price }}</div>
    </div>
    {% endfor %}
</div>

</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
