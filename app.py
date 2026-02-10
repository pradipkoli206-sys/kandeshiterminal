import os
import pyotp
import time
import threading
import requests
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect
from flask_socketio import SocketIO, emit

# --- NEW IMPORTS FOR SCREENSHOT ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

app = Flask(__name__, static_folder='static') # Configured static folder
app.config['SECRET_KEY'] = 'secret_key_for_websocket'
# WebSocket Initialization (Threading mode used for compatibility)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- ENSURE STATIC FOLDER EXISTS ---
if not os.path.exists('static'):
    os.makedirs('static')

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

# --- 1.1 MARKET HOLIDAYS LIST (2026) ---
NSE_HOLIDAYS = [
    "2026-01-26", # Republic Day
    "2026-02-17", # Mahashivratri
    "2026-03-04", # Holi
    "2026-03-20", # Gudhi Padwa
    "2026-04-03", # Good Friday
    "2026-04-14", # Ambedkar Jayanti
    "2026-05-01", # Maharashtra Day
    "2026-08-15", # Independence Day
    "2026-09-15", # Ganesh Chaturthi
    "2026-10-02", # Gandhi Jayanti
    "2026-10-02", # Gandhi Jayanti
    "2026-10-20", # Dasara
    "2026-11-08", # Diwali (Laxmi Pujan)
    "2026-12-25"  # Christmas
]

# --- MEMORY SYSTEM (JSON FILE HANDLING) ---
STATE_FILE = "trade_state.json"

def save_state(winner, sector_code):
    """Saves the locked winner to a file to survive restarts"""
    try:
        data = {
            "date": str(datetime.now(timezone.utc).date()), # Universal date check
            "winner": winner,
            "code": sector_code
        }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
        print(f"💾 MEMORY SAVED: {winner}")
    except Exception as e:
        print(f"Save Error: {e}")

def load_state():
    """Loads the winner from file if server restarts"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                # Check if the data is from TODAY
                if data.get("date") == str(datetime.now(timezone.utc).date()):
                    print(f"♻️ MEMORY RESTORED: {data['winner']}")
                    return data["winner"], data["code"]
    except Exception as e:
        print(f"Load Error: {e}")
    return None, "ALL"

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
    
    STOCKS = temp_stocks
    print(">>> SCAN COMPLETE <<<\n")

# --- 4. ENGINE (Multi-Threading & Async Logic) ---
def get_single_ltp(smart_api, exchange, symbol, token):
    # ✅ RETRY LOGIC ADDED: Try 3 Times
    for i in range(3):
        try:
            res = smart_api.ltpData(exchange, symbol, token)
            if res and res['status']:
                return res['data']
        except:
            time.sleep(0.1) # Wait 0.1s before retry
    return None

def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code, tokens_loaded
    smart_api = None
    last_ping_time = time.time()
    
    # ✅ RESTORE STATE ON STARTUP
    confirmed_winner, loaded_code = load_state()
    winning_sector_code = loaded_code
    if confirmed_winner:
        ans2_sector = "BANKING" if confirmed_winner == "BANK" else "IT / TECH" if confirmed_winner == "IT" else "AUTO"
    
    tick_count = 0
    sector_points = {"BANK": 0, "IT": 0, "AUTO": 0}

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            today_str = str(ist_now.date()) # Get today's date
            is_weekday = ist_now.weekday() < 5
            
            # --- HOLIDAY CHECK ---
            if today_str in NSE_HOLIDAYS:
                market_status = "HOLIDAY"
                ans1_nifty = "HOLIDAY"
                ans2_sector = "MARKET CLOSED"
                
                # Emit holiday status via Websocket
                socketio.emit('update_data', {
                    "status": market_status, "ans1": ans1_nifty, 
                    "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS
                })
                
                time.sleep(60)
                continue

            t915 = datetime.strptime("09:15", "%H:%M").time()
            t_lock = datetime.strptime("09:20:00", "%H:%M:%S").time() 
            t1530 = datetime.strptime("15:30", "%H:%M").time()
            t1535 = datetime.strptime("15:35", "%H:%M").time()
            t0900 = datetime.strptime("09:00", "%H:%M").time()

            if t0900 <= current_time < t915:
                # 9:00 AM Reset Logic
                if confirmed_winner is not None:
                    save_state(None, "ALL") 
                confirmed_winner = None
                tick_count = 0
                sector_points = {"BANK": 0, "IT": 0, "AUTO": 0}
                ans2_sector = "WAIT FOR 9:15"
                winning_sector_code = "ALL"

            # ✅ TEST MODE ON
            is_market_active = True 

            # ✅ DATA SAVER: Background ping removed/optimized
            if time.time() - last_ping_time > 600:
                if RENDER_URL:
                    try: requests.get(RENDER_URL)
                    except: pass
                last_ping_time = time.time()

            if not is_market_active:
                market_status = "CLOSED"
                socketio.emit('update_data', {
                    "status": market_status, "ans1": ans1_nifty, 
                    "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS
                })
                time.sleep(30)
                continue 

            market_status = "LIVE"

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if data['status'] and not tokens_loaded:
                    fetch_correct_tokens(smart_api)
                    valid_token_count = sum(1 for s in STOCKS if s.get("token"))
                    if valid_token_count > 0:
                        tokens_loaded = True
                        print("✅ Tokens Loaded Successfully")
                    else:
                        print("⚠️ NO TOKENS FOUND. Retrying...")
                        time.sleep(5)
                        smart_api = None
                        continue

            if not tokens_loaded: time.sleep(1); continue

            indices_results = {}
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(get_single_ltp, smart_api, "NSE", ind["symbol"], ind["token"]): ind["name"] for ind in INDICES_LIST}
                for future in futures:
                    name = futures[future]
                    data = future.result()
                    if data:
                        indices_results[name] = data

            bank_pct = -100.0; it_pct = -100.0; auto_pct = -100.0

            for name, data in indices_results.items():
                ltp = float(data['ltp'])
                close = float(data['close'])
                open_price = float(data.get('open', data['close']))
                
                if open_price > 0:
                    pct = ((ltp - open_price) / open_price) * 100
                else:
                    pct = 0.0
                
                if name == "NIFTY": ans1_nifty = "POSITIVE ▲" if ltp > close else "NEGATIVE ▼"
                if name == "BANKNIFTY": bank_pct = pct
                elif name == "NIFTY_IT": it_pct = pct
                elif name == "NIFTY_AUTO": auto_pct = pct

            if confirmed_winner is None:
                if current_time >= t_lock:
                    max_val = max(bank_pct, it_pct, auto_pct)
                    if max_val > 0:
                        if max_val == bank_pct: confirmed_winner = "BANK"
                        elif max_val == it_pct: confirmed_winner = "IT"
                        elif max_val == auto_pct: confirmed_winner = "AUTO"
                        if confirmed_winner:
                            winning_sector_code = confirmed_winner
                            ans2_sector = "BANKING" if confirmed_winner == "BANK" else "IT / TECH" if confirmed_winner == "IT" else "AUTO"
                            save_state(confirmed_winner, winning_sector_code)
                else:
                    ans2_sector = "CALCULATING..."
            else:
                winner_pct = -100.0
                if confirmed_winner == "BANK": winner_pct = bank_pct
                elif confirmed_winner == "IT": winner_pct = it_pct
                elif confirmed_winner == "AUTO": winner_pct = auto_pct
                
                if winner_pct < 0.0:
                    confirmed_winner = None
                    winning_sector_code = "ALL"
                    ans2_sector = "RE-SCANNING..."
                    save_state(None, "ALL")

            with ThreadPoolExecutor(max_workers=4) as executor:
                stock_futures = {executor.submit(get_single_ltp, smart_api, "NSE", s["symbol"], s["token"]): s for s in STOCKS if s.get("token")}
                for future in stock_futures:
                    stock_obj = stock_futures[future]
                    data = future.result()
                    if data:
                        live_data[stock_obj["token"]] = float(data['ltp'])
                        stock_obj["price"] = live_data[stock_obj["token"]]
            
            # ✅ WEBSOCKET EMIT: Push data to all connected clients immediately
            socketio.emit('update_data', {
                "status": market_status,
                "ans1": ans1_nifty,
                "ans2": ans2_sector,
                "winner": winning_sector_code,
                "stocks": STOCKS
            })
            
            # ✅ DATA SAVER & SPEED BALANCED: 1 Second Refresh Rate
            time.sleep(1) 
        except Exception as e:
            print(f"Engine Error: {e}")
            smart_api = None; time.sleep(5)

# Start background thread using Threading (compatible with SocketIO threading mode)
t = threading.Thread(target=start_engine); t.daemon = True; t.start()

# --- 5. ROUTES ---
@app.route('/')
def index():
    current_data = live_data.copy()
    for s in STOCKS:
        if s.get("token"): s["price"] = current_data.get(s["token"], "WAIT...")
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    # Fallback endpoint if needed
    current_data = live_data.copy()
    for s in STOCKS:
        if s.get("token"): s["price"] = current_data.get(s["token"], "WAIT...")
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})

# --- NEW ROUTE: BACKEND PROXY SCREENSHOT ---
@app.route('/get_chart_screenshot/<symbol>')
def get_chart_screenshot(symbol):
    """
    Backend Proxy Logic:
    1. Opens Headless Browser
    2. Goes to TradingView URL for symbol
    3. Takes screenshot
    4. Saves to static/chart_{symbol}.png
    """
    try:
        # Setup Headless Chrome
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        driver = webdriver.Chrome(options=chrome_options)
        
        # URL Construction (Example URL - Adjust specific TradingView logic if needed)
        url = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}"
        driver.get(url)
        
        # Wait for render (adjust sleep as needed)
        time.sleep(3) 
        
        # Save Screenshot
        filename = f"chart_{symbol}.png"
        filepath = os.path.join('static', filename)
        driver.save_screenshot(filepath)
        driver.quit()
        
        return jsonify({"status": "success", "image_url": f"/static/{filename}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- 6. HTML TEMPLATE ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER PRO</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
<style>
/* --- MODERN DARK THEME VARIABLES --- */
:root {
    --bg-main: #0a0e17;      /* Deep Dark Background */
    --bg-card: #151a25;      /* Slightly Lighter Card BG */
    --text-main: #ffffff;    /* Pure White Text */
    --text-muted: #8b9bb4;   /* Muted Text for Labels */
    --border: #2a3441;       /* Subtle Borders */
    --accent-blue: #3772ff;  /* Vibrant Blue Accent */
    --accent-green: #00e396; /* Neon Green for Positives */
    --accent-red: #ff4560;   /* Neon Red for Negatives */
    --accent-purple: #962eff; /* Purple Accent */
    --card-shadow: 0 8px 16px rgba(0,0,0,0.2); /* Soft Shadow for Depth */
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { 
    background-color: var(--bg-main); 
    color: var(--text-main); 
    font-family: 'Inter', sans-serif; 
    margin: 0; height: 100vh; display: flex; flex-direction: column; 
    overflow: hidden;
}

/* --- HEADER --- */
.header {
    padding: 15px 20px; 
    background: rgba(21, 26, 37, 0.95); 
    border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center;
    backdrop-filter: blur(10px);
}
.brand { 
    font-size: 20px; font-weight: 800; letter-spacing: 0.5px; 
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple)); 
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; 
}
.status-badge { 
    font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; 
    background: rgba(0, 227, 150, 0.1); color: var(--accent-green); 
    border: 1px solid rgba(0, 227, 150, 0.3); letter-spacing: 0.5px;
    display: flex; align-items: center; gap: 5px;
}
.status-dot { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 8px var(--accent-green); }

/* --- TOP DASHBOARD AREA --- */
.dashboard-summary {
    display: flex; gap: 15px; padding: 20px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(21, 26, 37, 0.5) 0%, rgba(10, 14, 23, 0) 100%);
}
.summary-card {
    flex: 1; background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 16px; padding: 15px; display: flex; flex-direction: column; gap: 5px;
    box-shadow: var(--card-shadow);
}
.summary-label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.8px; }
.summary-value { font-size: 18px; font-weight: 800; }
.txt-green { color: var(--accent-green); text-shadow: 0 0 10px rgba(0,227,150,0.3); }
.txt-red { color: var(--accent-red); text-shadow: 0 0 10px rgba(255,69,96,0.3); }
.txt-blue { color: var(--accent-blue); text-shadow: 0 0 10px rgba(55,114,255,0.3); }

/* --- FILTER BAR --- */
.filter-bar { 
    padding: 15px 20px; display: flex; gap: 10px; 
    background: var(--bg-main); border-bottom: 1px solid var(--border);
    overflow-x: auto; scrollbar-width: none;
}
.filter-btn { 
    background: var(--bg-card); border: 1px solid var(--border); color: var(--text-muted); 
    padding: 8px 16px; border-radius: 12px; font-size: 12px; font-weight: 700; 
    text-align: center; cursor: pointer; transition: all 0.2s; white-space: nowrap;
}
.filter-btn.active { 
    background: var(--accent-blue); color: white; border-color: var(--accent-blue); 
    box-shadow: 0 4px 12px rgba(55, 114, 255, 0.4); 
}

/* --- MAIN STOCK LIST (Modern Cards) --- */
.stock-list { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
.stock-card { 
    background: var(--bg-card); 
    border: 1px solid var(--border); 
    padding: 18px 20px; 
    border-radius: 16px; 
    display: flex; justify-content: space-between; align-items: center;
    box-shadow: var(--card-shadow);
    transition: transform 0.2s, border-color 0.2s;
    cursor: pointer; /* Clickable */
}
.stock-card:hover { transform: translateY(-2px); border-color: var(--text-muted); }

.st-info { display: flex; flex-direction: column; gap: 4px; }
.st-name { font-size: 16px; font-weight: 800; color: var(--text-main); letter-spacing: 0.3px; }
.st-cat-tag { 
    font-size: 10px; font-weight: 700; color: var(--text-muted); 
    background: rgba(255,255,255,0.05); padding: 3px 8px; 
    border-radius: 6px; width: fit-content; 
}

/* Upload Button Style (Visual Only in Card) */
.upload-btn {
    margin-top: 8px;
    background: rgba(55, 114, 255, 0.15);
    color: var(--accent-blue);
    border: 1px solid rgba(55, 114, 255, 0.3);
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
    width: fit-content;
    display: flex; align-items: center; gap: 4px;
    transition: all 0.2s;
    cursor: pointer;
}
.upload-btn:hover { background: var(--accent-blue); color: white; }

.st-price-box { text-align: right; }
.st-price { font-size: 20px; font-weight: 800; color: var(--accent-green); letter-spacing: 0.5px; }
.st-wait { font-size: 14px; font-weight: 600; color: var(--text-muted); }

/* --- POPUP WINDOW STYLES (NEW) --- */
.modal-overlay {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0, 0, 0, 0.85); z-index: 2000;
    /* ✅ FIX: Aligned to Top with padding instead of Center */
    display: flex; justify-content: center; align-items: flex-start; padding-top: 80px;
    backdrop-filter: blur(8px);
}
.modal-box {
    background: var(--bg-card); width: 85%; max-width: 350px;
    padding: 25px; border-radius: 20px; border: 1px solid var(--border);
    text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.6);
    animation: popin 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    display: flex; flex-direction: column; gap: 15px;
}
.modal-title { font-size: 18px; font-weight: 800; color: var(--text-main); margin-bottom: 5px; }
.modal-close {
    background: var(--accent-blue); color: white;
    border: none; padding: 10px 24px; border-radius: 10px;
    font-weight: 700; cursor: pointer; width: 100%;
}
/* DELETE BUTTON STYLE (ADDED TO MATCH THEME) */
.modal-delete {
    background: transparent; color: var(--accent-red);
    border: 1px solid var(--accent-red); padding: 8px 24px; border-radius: 10px;
    font-weight: 700; cursor: pointer; width: 100%; font-size: 12px;
}
.modal-delete:hover { background: rgba(255, 69, 96, 0.1); }

@keyframes popin { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }

/* --- BOTTOM NAVIGATION (Modern) --- */
.bottom-nav {
    height: 70px; background: rgba(21, 26, 37, 0.95);
    border-top: 1px solid var(--border);
    display: flex; justify-content: space-around; align-items: center;
    padding-bottom: 10px; backdrop-filter: blur(10px);
}
.nav-item {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    color: var(--text-muted); cursor: pointer; flex: 1; height: 100%;
    transition: color 0.2s;
}
.nav-item.active { color: var(--accent-blue); }
.nav-icon { font-size: 26px; margin-bottom: 2px; }
.nav-label { font-size: 10px; font-weight: 600; }

/* --- UTILS & ANIMATION --- */
.hidden { display: none !important; }
@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
.loading-pulse { animation: pulse 1.5s infinite; }

/* Custom Scrollbar */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: var(--bg-main); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
</style>
<script>
// ✅ FIX: Initialize with Server Variable immediately
let currentWinner = "{{ winner }}";
let activeFilter = "ALL";
let stockImages = {};

// --- LOCAL STORAGE LOGIC ---
function loadImagesFromStorage() {
    const cards = document.querySelectorAll('.stock-card');
    cards.forEach(card => {
        const name = card.querySelector('.st-name').innerText;
        const saved = localStorage.getItem('chart_' + name);
        if(saved) stockImages[name] = saved;
    });
}

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
        else if (activeFilter === 'TODAY') show = true;
        else if (activeFilter === 'AI') { 
            if (currentWinner === 'ALL' || cat === currentWinner) show = true;
        }

        const btn = card.querySelector('.upload-btn');
        if(btn) {
            if (activeFilter === 'TODAY') btn.classList.remove('hidden');
            else btn.classList.add('hidden');
        }

        if(show) card.classList.remove('hidden'); else card.classList.add('hidden');
    });
}

function setActiveNav(el) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
}

function triggerUpload(event, stockName) {
    // ✅ PREVENTS BUBBLING: Clicking Upload won't open the card popup
    event.stopPropagation();
    document.getElementById('file-input-' + stockName).click();
}

function handleFileSelect(input, stockName) {
    if (input.files && input.files[0]) {
        var reader = new FileReader();
        reader.onload = function (e) {
            stockImages[stockName] = e.target.result;
            // SAVE TO LOCAL STORAGE
            try {
                localStorage.setItem('chart_' + stockName, e.target.result);
                // 🚫 ALERT REMOVED: Silent Save
            } catch (err) {
                console.log("Image too big to save.");
            }
        }
        reader.readAsDataURL(input.files[0]);
    }
}

// --- DELETE FUNCTION ---
function deleteChart(stockName) {
    // 🚫 CONFIRMATION REMOVED: Deletes immediately on click
    localStorage.removeItem('chart_' + stockName);
    delete stockImages[stockName];
    closePopup();
    // 🚫 SUCCESS ALERT REMOVED: Silent Delete
}

function openCardPopup(stockName) {
    const saved = localStorage.getItem('chart_' + stockName);
    if(saved) stockImages[stockName] = saved;

    // --- 1. SET THE AI SCREENSHOT SOURCE (NEW) ---
    // This points to the static file generated by the backend proxy
    const aiImg = document.getElementById('ai-screenshot');
    aiImg.style.display = 'none'; // Reset to hidden
    // Add cache buster to force reload
    aiImg.src = "/static/chart_" + stockName + ".png?" + new Date().getTime();
    aiImg.onload = function() { this.style.display = 'block'; }; // Show only if loaded

    if(activeFilter === 'TODAY') {
        document.getElementById('popup-name').innerText = stockName;
        const imgContainer = document.getElementById('popup-img-container');
        const delBtn = document.getElementById('popup-delete-btn');
        
        imgContainer.innerHTML = '';
        
        if (stockImages[stockName]) {
            // ✅ FIX: Image is now wrapped in a mini-card DIV with fixed height, keeping it small and neat.
            imgContainer.innerHTML = `
                <div style="width: 100%; height: 200px; border: 1px solid var(--border); border-radius: 10px; background: rgba(0,0,0,0.2); display: flex; align-items: center; justify-content: center; overflow: hidden;">
                    <img src="${stockImages[stockName]}" style="max-width: 100%; max-height: 100%; object-fit: contain;">
                </div>
            `;
            delBtn.classList.remove('hidden');
            delBtn.onclick = function() { deleteChart(stockName); };
        } else {
            imgContainer.innerHTML = '<p style="color: var(--text-muted); font-size: 14px;">No chart uploaded yet.</p>';
            delBtn.classList.add('hidden');
        }
        document.getElementById('modal-overlay').classList.remove('hidden');
    }
    // Allow opening popup in other modes too if needed, but keeping original logic
    else {
         document.getElementById('popup-name').innerText = stockName;
         document.getElementById('popup-img-container').innerHTML = '<p style="color: var(--text-muted); font-size: 14px;">Select TODAY filter to upload.</p>';
         document.getElementById('popup-delete-btn').classList.add('hidden');
         document.getElementById('modal-overlay').classList.remove('hidden');
    }
}

function closePopup() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

// ✅ NEW: WEBSOCKET LOGIC WITH STATUS & ERROR HANDLING
// Explicit namespace connection to avoid default handling issues
var socket = io('/'); 

socket.on('connect', function() {
    console.log("✅ [Socket] Connected to server.");
    // Update Text
    document.getElementById('status-text').innerText = "LIVE SOCKET";
    
    // Update Colors via existing CSS variables (Visual Feedback)
    const dot = document.querySelector('.status-dot');
    const badge = document.querySelector('.status-badge');
    if(dot && badge) {
        dot.style.backgroundColor = "var(--accent-green)";
        badge.style.borderColor = "rgba(0, 227, 150, 0.3)";
        badge.style.color = "var(--accent-green)";
    }
});

socket.on('disconnect', function() {
    console.log("❌ [Socket] Disconnected from server.");
    document.getElementById('status-text').innerText = "OFFLINE";
    
    // Visual Feedback for Error
    const dot = document.querySelector('.status-dot');
    const badge = document.querySelector('.status-badge');
    if(dot && badge) {
        dot.style.backgroundColor = "var(--accent-red)";
        badge.style.borderColor = "var(--accent-red)";
        badge.style.color = "var(--accent-red)";
    }
});

socket.on('connect_error', (err) => {
    console.log("⚠️ [Socket] Connection Error: " + err);
    document.getElementById('status-text').innerText = "ERROR";
});

socket.on('update_data', function(data) {
    console.log("📥 [Socket] Data Received:", data); // Debug Log

    // 1. Update Status Text
    document.getElementById('status-text').innerText = data.status;

    // 2. Update ans1 (Nifty)
    const ans1 = document.getElementById('ans1-disp');
    ans1.innerHTML = data.ans1;
    if(data.ans1.includes("POSITIVE")) ans1.className = "summary-value txt-green";
    else if(data.ans1.includes("NEGATIVE")) ans1.className = "summary-value txt-red";
    else ans1.className = "summary-value loading-pulse";

    // 3. Update ans2 (Sector)
    const ans2 = document.getElementById('ans2-disp');
    ans2.innerText = data.ans2;
    if(data.ans2 !== "LOADING...") ans2.className = "summary-value txt-blue";
    else ans2.className = "summary-value loading-pulse";
    
    // 4. Update Global Winner Variable & Filter
    currentWinner = data.winner;
    
    // 5. Update Stock Prices
    data.stocks.forEach(s => {
        const priceEl = document.getElementById('price-' + s.name);
        if(priceEl) {
            if(s.price === "WAIT..." || s.price === "0.00" || s.price === "ERR") {
                 priceEl.innerText = s.price;
                 priceEl.className = "st-wait loading-pulse";
            } else {
                priceEl.innerText = "₹" + s.price;
                priceEl.className = "st-price";
            }
        }
    });

    // Re-apply filter to update visibility if winner changed
    applyFilter();
});

// Initial Load
window.onload = function() {
    loadImagesFromStorage();
};
</script>
</head>
<body>

<div class="header">
    <div class="brand">AI TRADER PRO</div>
    <div class="status-badge"><div class="status-dot"></div><span id="status-text">CONNECTING...</span></div>
</div>

<div class="dashboard-summary">
    <div class="summary-card">
        <span class="summary-label">Market Trend</span>
        <span class="summary-value" id="ans1-disp">WAIT...</span>
    </div>
    <div class="summary-card">
        <span class="summary-label">Strongest Sector</span>
        <span class="summary-value" id="ans2-disp">LOADING...</span>
    </div>
</div>

<div class="filter-bar">
    <div id="btn-ALL" class="filter-btn active" onclick="filterStocks('ALL')">All Stocks</div>
    <div id="btn-TODAY" class="filter-btn" onclick="filterStocks('TODAY')">Today Stock</div>
    <div id="btn-AI" class="filter-btn" onclick="filterStocks('AI')">🚀 AI Pick</div>
</div>

<div class="stock-list">
    {% for s in stocks %}
    <div class="stock-card" id="card-{{ s.name }}" data-cat="{{ s.cat }}" onclick="openCardPopup('{{ s.name }}')">
        <div class="st-info">
            <span class="st-name">{{ s.name }}</span>
            <span class="st-cat-tag">{{ s.cat }} SECTOR</span>
            <div class="upload-btn hidden" onclick="triggerUpload(event, '{{ s.name }}')">
                <span class="material-icons-outlined" style="font-size: 14px;">upload_file</span>
                Upload Chart
            </div>
            <input type="file" id="file-input-{{ s.name }}" style="display: none;" accept="image/*" onchange="handleFileSelect(this, '{{ s.name }}')">
        </div>
        <div class="st-price-box">
            <div class="st-wait" id="price-{{ s.name }}">{{ s.price }}</div>
        </div>
    </div>
    {% endfor %}
</div>

<div id="modal-overlay" class="modal-overlay hidden">
    <div class="modal-box">
        <div class="modal-title" id="popup-name">STOCK NAME</div>
        
        <div id="popup-img-container">
            <p style="color: var(--text-muted); font-size: 14px;">Chart Upload Window</p>
        </div>

        <img id="ai-screenshot" style="width: 100%; border-radius: 10px; margin-top: 10px; display: none;" onerror="this.style.display='none'">

        <button id="popup-delete-btn" class="modal-delete hidden">DELETE CHART</button>
        <button class="modal-close" onclick="closePopup()">CLOSE</button>
    </div>
</div>

<div class="bottom-nav">
    <div class="nav-item active" onclick="setActiveNav(this)">
        <span class="material-icons-outlined nav-icon">dashboard</span>
        <span class="nav-label">Home</span>
    </div>
    <div class="nav-item" onclick="setActiveNav(this)">
        <span class="material-icons-outlined nav-icon">insights</span>
        <span class="nav-label">Analysis</span>
    </div>
    <div class="nav-item" onclick="setActiveNav(this)">
        <span class="material-icons-outlined nav-icon">notifications</span>
        <span class="nav-label">Alerts</span>
    </div>
    <div class="nav-item" onclick="setActiveNav(this)">
        <span class="material-icons-outlined nav-icon">person</span>
        <span class="nav-label">Profile</span>
    </div>
</div>

</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # ✅ FIX: Allow unsafe werkzeug for Render free tier testing
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
