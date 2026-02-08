import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

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
tokens_loaded = False 

# --- 2. CONFIGURATION ---
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
TOKEN_TO_NAME = {} # Mapping for WebSocket updates

# --- 3. AUTO-SCANNER (CRITICAL FOR WEBSOCKET) ---
def fetch_correct_tokens(smart_api):
    global STOCKS, TOKEN_TO_NAME
    print("\n>>> SCANNING TOKENS FOR WEBSOCKET <<<")
    temp_stocks = []
    
    # 1. Stocks
    for item in TARGET_STOCKS:
        name = item["name"]
        time.sleep(0.5)
        try:
            search_data = smart_api.searchScrip("NSE", name)
            scrip_list = search_data['data'] if isinstance(search_data, dict) else search_data
            
            found_token = None
            if scrip_list:
                for s in scrip_list:
                    if s['tradingsymbol'] == name + "-EQ":
                        found_token = s['symboltoken']; break
                if not found_token:
                    for s in scrip_list:
                        if s['tradingsymbol'] == name + "-BE":
                            found_token = s['symboltoken']; break
            
            if found_token:
                print(f"✅ {name}: {found_token}")
                temp_stocks.append({"name": name, "token": found_token, "cat": item["cat"], "price": "0.00"})
                TOKEN_TO_NAME[found_token] = name
            else:
                print(f"❌ FAILED: {name}")
                temp_stocks.append({"name": name, "token": None, "cat": item["cat"], "price": "ERR"})
        except: pass

    # 2. Indices
    for ind in INDICES_LIST:
        TOKEN_TO_NAME[ind["token"]] = ind["name"]

    STOCKS = temp_stocks
    print(">>> SCAN COMPLETE <<<\n")

# --- 4. WEBSOCKET ENGINE (THE FIX) ---
def start_websocket_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code, tokens_loaded
    smart_api = None
    sws = None
    
    while True:
        try:
            # Market Status Check
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            is_market_open = (ist_now.weekday() < 5 and datetime.strptime("09:00", "%H:%M").time() <= ist_now.time() <= datetime.strptime("15:30", "%H:%M").time())
            market_status = "LIVE" if is_market_open else "CLOSED"

            # 1. Login & Token Fetch
            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                
                if data['status']:
                    auth_token = data['data']['jwtToken']
                    feed_token = smart_api.getfeedToken()
                    
                    if not tokens_loaded:
                        fetch_correct_tokens(smart_api)
                        tokens_loaded = True
                    
                    # 2. Initialize WebSocket
                    sws = SmartWebSocketV2(auth_token, API_KEY, CLIENT_ID, feed_token)
                else:
                    print("Login Failed, Retrying...")
                    time.sleep(5)
                    continue

            # 3. Define WebSocket Callbacks
            def on_data(wsapp, msg):
                global ans1_nifty, ans2_sector, winning_sector_code
                
                if 'token' in msg and 'last_traded_price' in msg:
                    token = msg['token'].replace('"', '') # Clean token
                    ltp = float(msg['last_traded_price']) / 100.0 # Convert paise to rupees
                    
                    # Update Stocks
                    if token in TOKEN_TO_NAME:
                        name = TOKEN_TO_NAME[token]
                        live_data[token] = ltp
                        
                        # Update Price in STOCKS list
                        for s in STOCKS:
                            if s["name"] == name: s["price"] = ltp

                        # Index Logic
                        if name == "NIFTY":
                            open_price = float(msg.get('open_price_day', ltp*100)) / 100.0
                            ans1_nifty = "POSITIVE ▲" if ltp > open_price else "NEGATIVE ▼"

                # Calculate Sector (Simple Logic on every tick)
                bank_p = 0; auto_p = 0; it_p = 0
                count = 0
                for s in STOCKS:
                    if s["price"] != "ERR" and s["price"] != "0.00":
                        if s["cat"] == "BANK": bank_p += float(s["price"]); count+=1
                        elif s["cat"] == "AUTO": auto_p += float(s["price"])
                        elif s["cat"] == "IT": it_p += float(s["price"])
                
                # Dynamic Sector Winner
                if bank_p > auto_p and bank_p > it_p: ans2_sector = "BANKING"; winning_sector_code = "BANK"
                elif auto_p > bank_p and auto_p > it_p: ans2_sector = "AUTO"; winning_sector_code = "AUTO"
                else: ans2_sector = "IT / MIX"; winning_sector_code = "IT"

            def on_open(wsapp):
                print("✅ WebSocket Connected!")
                # Prepare Token List for Subscription
                token_list = []
                # Add Stocks
                for s in STOCKS:
                    if s["token"]: token_list.append({"exchangeType": 1, "tokens": [s["token"]]})
                # Add Indices
                for ind in INDICES_LIST:
                    token_list.append({"exchangeType": 1, "tokens": [ind["token"]]}) # NSE CM
                
                # Subscribe
                sws.subscribe(current_mode="LTP", token_list=token_list)

            def on_error(wsapp, error):
                print(f"❌ Socket Error: {error}")

            def on_close(wsapp):
                print("⚠️ Connection Closed. Reconnecting...")
                
            # Assign callbacks
            sws.on_open = on_open
            sws.on_data = on_data
            sws.on_error = on_error
            sws.on_close = on_close

            # 4. Start WebSocket (Blocking, so we put it in thread)
            print(">>> CONNECTING TO WEBSOCKET... <<<")
            sws.connect()
            
            # If connect() exits (due to close), loop will restart and reconnect
            print("WebSocket loop ended, restarting...")
            time.sleep(2)

        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(5)

# Run Engine in Background
t = threading.Thread(target=start_websocket_engine)
t.daemon = True
t.start()

# --- 5. ROUTES ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS)

@app.route('/data')
def data():
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})

# --- 6. HTML TEMPLATE (SAME PREMIUM DESIGN) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER PRO</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root { --bg-main: #0a0e17; --bg-card: #151a25; --text-main: #ffffff; --text-muted: #8b9bb4; --border: #2a3441; --accent-blue: #3772ff; --accent-green: #00e396; --accent-red: #ff4560; }
* { box-sizing: border-box; } body { background: var(--bg-main); color: var(--text-main); font-family: 'Inter', sans-serif; margin: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
.header { padding: 15px 20px; background: rgba(21, 26, 37, 0.95); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.brand { font-size: 18px; font-weight: 800; background: linear-gradient(45deg, #3772ff, #9b5de5); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.dashboard { display: flex; border-bottom: 1px solid var(--border); height: 35%; }
.metric-panel { flex: 1; padding: 20px; display: flex; flex-direction: column; justify-content: center; border-right: 1px solid var(--border); }
.metric-value { font-size: 22px; font-weight: 700; } .txt-green { color: var(--accent-green); } .txt-red { color: var(--accent-red); }
.side-panel { width: 35%; background: #11151e; border-left: 1px solid var(--border); display: flex; flex-direction: column; }
.pick-list { overflow-y: auto; flex: 1; } .pick-item { padding: 10px 15px; font-size: 12px; border-bottom: 1px solid #1e2532; }
.filter-bar { padding: 12px; display: flex; gap: 10px; background: var(--bg-main); border-bottom: 1px solid var(--border); }
.chip { flex: 1; background: var(--bg-card); border: 1px solid var(--border); padding: 10px; border-radius: 8px; font-size: 11px; font-weight: 600; text-align: center; cursor: pointer; }
.chip.active { background: var(--accent-blue); color: white; border-color: var(--accent-blue); }
.market-list { flex: 1; overflow-y: auto; padding: 15px; }
.stock-card { background: var(--bg-card); border: 1px solid var(--border); padding: 16px; border-radius: 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.hidden { display: none !important; }
</style>
<script>
let currentWinner = "ALL"; let activeFilter = "ALL";
function filterStocks(type) { activeFilter = type; document.querySelectorAll('.chip').forEach(b => b.classList.remove('active')); document.getElementById('btn-'+type).classList.add('active'); applyFilter(); }
function applyFilter() { document.querySelectorAll('.stock-card').forEach(card => { const cat = card.getAttribute('data-cat'); card.classList.toggle('hidden', activeFilter === 'TODAY' && currentWinner !== 'ALL' && cat !== currentWinner); }); }
function fetchData() {
    fetch('/data').then(r => r.json()).then(data => {
        document.getElementById('status-disp').innerText = data.status;
        const ans1 = document.getElementById('ans1-disp'); ans1.innerHTML = data.ans1;
        ans1.className = "metric-value " + (data.ans1.includes("POSITIVE") ? "txt-green" : (data.ans1.includes("NEGATIVE") ? "txt-red" : ""));
        document.getElementById('ans2-disp').innerText = data.ans2; currentWinner = data.winner;
        data.stocks.forEach(s => { const el = document.getElementById('price-' + s.name); if(el) { el.innerText = "₹" + s.price; el.style.color = (s.price !== "0.00" && s.price !== "ERR") ? "#00e396" : "#fff"; } });
        applyFilter();
    });
}
setInterval(fetchData, 1000); // Fast UI Updates for WebSocket
</script>
</head>
<body>
<div class="header"> <div class="brand">AI TRADER PRO</div> <div style="font-size:11px; color:#8b9bb4;" id="status-disp">CONNECTING...</div> </div>
<div class="dashboard">
    <div class="metric-panel"> <div style="color:#8b9bb4; font-size:11px; margin-bottom:8px;">MARKET TREND</div> <div class="metric-value" id="ans1-disp">WAIT...</div> </div>
    <div class="metric-panel" style="border-right:none;"> <div style="color:#8b9bb4; font-size:11px; margin-bottom:8px;">STRONGEST SECTOR</div> <div class="metric-value" style="color:var(--accent-blue);" id="ans2-disp">LOADING...</div> </div>
    <div class="side-panel"> <div style="padding:12px; font-size:11px; font-weight:700; color:var(--accent-blue); text-align:center; border-bottom:1px solid #2a3441; background:#161b26;">🔥 HOT PICKS</div>
        <div class="pick-list"> {% for s in stocks %}<div class="pick-item">{{ s.name }}</div>{% endfor %} </div>
    </div>
</div>
<div class="filter-bar"> <div id="btn-ALL" class="chip active" onclick="filterStocks('ALL')">ALL STOCKS</div> <div id="btn-TODAY" class="chip" onclick="filterStocks('TODAY')">🚀 AI PICK</div> </div>
<div class="market-list">
    {% for s in stocks %}
    <div class="stock-card" id="card-{{ s.name }}" data-cat="{{ s.cat }}">
        <div><div style="font-size:14px; font-weight:700;">{{ s.name }}</div><div style="font-size:10px; font-weight:600; color:#8b9bb4; background:#1e2532; padding:3px 6px; border-radius:4px; margin-top:4px; width:fit-content;">{{ s.cat }}</div></div>
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
