import os
import pyotp
import time
import threading
import json
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key_for_websocket'
# WebSocket Initialization (Threading mode used for compatibility)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- 1. KEYS (UNTOUCHED) ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Global Variables
smart_api = None
sws = None # SmartWebSocket Object
live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
tokens_loaded = False 

# --- 1.1 MARKET HOLIDAYS LIST (2026) ---
NSE_HOLIDAYS = [
    "2026-01-26", "2026-02-17", "2026-03-04", "2026-03-20", 
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15", 
    "2026-09-15", "2026-10-02", "2026-10-20", "2026-11-08", "2026-12-25"
]

# --- MEMORY SYSTEM ---
STATE_FILE = "trade_state.json"

def save_state(winner, sector_code):
    try:
        data = { "date": str(datetime.now(timezone.utc).date()), "winner": winner, "code": sector_code }
        with open(STATE_FILE, "w") as f: json.dump(data, f)
        print(f"💾 MEMORY SAVED: {winner}")
    except Exception as e: print(f"Save Error: {e}")

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == str(datetime.now(timezone.utc).date()):
                    print(f"♻️ MEMORY RESTORED: {data['winner']}")
                    return data["winner"], data["code"]
    except Exception as e: print(f"Load Error: {e}")
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

# Master List for easy lookup
STOCKS = [{"name": s["name"], "cat": s["cat"], "token": None, "price": "WAIT...", "symbol": s["name"]} for s in TARGET_STOCKS]
TOKEN_MAP = {} # Token -> Stock Object Mapping

# --- 3. AUTO-SCANNER ---
def fetch_correct_tokens(api_obj):
    global STOCKS, TOKEN_MAP
    print("\n>>> STARTING NSE TOKEN SCANNER <<<")
    temp_stocks = []
    TOKEN_MAP = {} # Clear map
    
    # 1. Fetch Stocks
    for item in TARGET_STOCKS:
        name = item["name"]; cat = item["cat"]
        time.sleep(0.5)
        try:
            search_response = api_obj.searchScrip("NSE", name)
            scrip_list = search_response['data'] if search_response and 'data' in search_response else []
            
            found_token = None
            if scrip_list:
                for s in scrip_list:
                    if s['tradingsymbol'] == name + "-EQ": found_token = s['symboltoken']; break
                if not found_token:
                    for s in scrip_list:
                        if s['tradingsymbol'] == name + "-BE": found_token = s['symboltoken']; break
            
            if found_token:
                print(f"✅ FOUND: {name} -> {found_token}")
                stock_obj = {"name": name, "token": found_token, "cat": cat, "price": "0.00"}
                temp_stocks.append(stock_obj)
                TOKEN_MAP[found_token] = stock_obj # Map Token to Object
            else:
                print(f"❌ FAILED: {name}")
                temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR"})
        except:
            temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR"})
    
    STOCKS = temp_stocks

    # 2. Add Indices to Token Map (Hardcoded Tokens)
    for ind in INDICES_LIST:
        TOKEN_MAP[ind["token"]] = {"name": ind["name"], "cat": "INDEX", "price": 0.0, "open": 0.0}

    print(">>> SCAN COMPLETE <<<\n")

# --- 4. ANGEL ONE WEBSOCKET LOGIC ---
def on_data(wsapp, msg):
    global live_data, ans1_nifty, ans2_sector, winning_sector_code, market_status
    
    # Check if market is open/live based on data flow
    market_status = "LIVE" 
    
    # Parse Binary/JSON data from Angel One
    # Note: SmartWebSocketV2 usually returns a dictionary directly
    if isinstance(msg, dict):
        token = msg.get("token")
        ltp = float(msg.get("last_traded_price", 0))
        open_price = float(msg.get("open_price_of_the_day", 0)) # Important for % calc
        
        if token in TOKEN_MAP:
            # Update Price in Memory
            TOKEN_MAP[token]["price"] = ltp
            if open_price > 0: TOKEN_MAP[token]["open"] = open_price # Cache open price
            
            # --- LOGIC PROCESSING ---
            # 1. Update Stock Prices in List
            if TOKEN_MAP[token]["cat"] != "INDEX":
                # Find stock in main list and update
                for s in STOCKS:
                    if s.get("token") == token:
                        s["price"] = ltp
                        break
            
            # 2. Update Indices & Calculate Logic
            bank_pct = -100.0; it_pct = -100.0; auto_pct = -100.0
            
            # Check all Indices status
            nifty_obj = TOKEN_MAP.get("99926000") # Nifty 50
            if nifty_obj and nifty_obj.get("price") > 0:
                # Nifty Logic
                op = nifty_obj.get("open", 0)
                if op > 0:
                    ans1_nifty = "POSITIVE ▲" if nifty_obj["price"] > op else "NEGATIVE ▼" # Vs Open
            
            # Calculate Sector %
            for ind in INDICES_LIST:
                t = ind["token"]
                obj = TOKEN_MAP.get(t)
                if obj and obj.get("price") > 0 and obj.get("open", 0) > 0:
                    pct = ((obj["price"] - obj["open"]) / obj["open"]) * 100
                    if ind["name"] == "BANKNIFTY": bank_pct = pct
                    elif ind["name"] == "NIFTY_IT": it_pct = pct
                    elif ind["name"] == "NIFTY_AUTO": auto_pct = pct

            # --- WINNER LOCK LOGIC (9:20 AM) ---
            t_lock = datetime.strptime("09:20:00", "%H:%M:%S").time()
            current_time = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).time()
            
            # Only Lock if not already locked
            if winning_sector_code == "ALL":
                if current_time >= t_lock:
                    max_val = max(bank_pct, it_pct, auto_pct)
                    if max_val > 0:
                        if max_val == bank_pct: winning_sector_code = "BANK"
                        elif max_val == it_pct: winning_sector_code = "IT"
                        elif max_val == auto_pct: winning_sector_code = "AUTO"
                        
                        if winning_sector_code != "ALL":
                            ans2_sector = "BANKING" if winning_sector_code == "BANK" else "IT / TECH" if winning_sector_code == "IT" else "AUTO"
                            save_state(winning_sector_code, winning_sector_code)
                else:
                    ans2_sector = "CALCULATING..."
            else:
                # Safety Check: If Winner goes Negative, Reset
                w_pct = -100
                if winning_sector_code == "BANK": w_pct = bank_pct
                elif winning_sector_code == "IT": w_pct = it_pct
                elif winning_sector_code == "AUTO": w_pct = auto_pct
                
                if w_pct < 0:
                    winning_sector_code = "ALL"
                    ans2_sector = "RE-SCANNING..."
                    save_state(None, "ALL")

            # --- PUSH DATA TO FRONTEND (INSTANT) ---
            socketio.emit('update_data', {
                "status": market_status,
                "ans1": ans1_nifty,
                "ans2": ans2_sector,
                "winner": winning_sector_code,
                "stocks": STOCKS
            })

def on_open(wsapp):
    print(">>> Angel One WebSocket CONNECTED <<<")
    # Subscribe to Tokens (Mode 1: LTP)
    token_list = [{"exchangeType": 1, "tokens": [t]} for t in TOKEN_MAP.keys()]
    # Note: ExchangeType 1 is NSE
    if token_list:
        sws.subscribe_correlation_id("Stream_1")
        sws.subscribe(1, 1, token_list) # Mode 1 (LTP), Exchange 1 (NSE)

def on_error(wsapp, error):
    print(f"WebSocket Error: {error}")

def on_close(wsapp):
    print("WebSocket Closed. Reconnecting...")
    # Auto Reconnect Logic can be added here if needed

def init_angel_websocket():
    global sws, smart_api, tokens_loaded, winning_sector_code, ans2_sector
    
    # 1. Login & Setup
    try:
        totp = pyotp.TOTP(TOTP_KEY).now()
        smart_api = SmartConnect(api_key=API_KEY)
        data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
        auth_token = data['data']['jwtToken']
        feed_token = smart_api.getfeedToken()
        
        # 2. Fetch Tokens
        fetch_correct_tokens(smart_api)
        tokens_loaded = True

        # 3. Load State
        w, c = load_state()
        winning_sector_code = c
        if w: ans2_sector = "BANKING" if w == "BANK" else "IT / TECH" if w == "IT" else "AUTO"

        # 4. Start WebSocket
        sws = SmartWebSocketV2(auth_token, API_KEY, CLIENT_ID, feed_token)
        sws.on_data = on_data
        sws.on_open = on_open
        sws.on_error = on_error
        sws.on_close = on_close
        sws.connect()
        
    except Exception as e:
        print(f"Init Error: {e}")
        time.sleep(10)
        init_angel_websocket() # Retry

# Keep-Alive Thread (For Render)
def keep_alive():
    while True:
        time.sleep(300) # 5 Mins
        if RENDER_URL:
            try: requests.get(RENDER_URL); print("Ping Sent")
            except: pass

# Start Background Threads
t_ws = threading.Thread(target=init_angel_websocket); t_ws.daemon = True; t_ws.start()
t_ping = threading.Thread(target=keep_alive); t_ping.daemon = True; t_ping.start()

# --- 5. ROUTES ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})

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
    display: flex; justify-content: center; align-items: center;
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

// ✅ FIX: Added event.stopPropagation() here
function triggerUpload(event, stockName) {
    event.stopPropagation();
    document.getElementById('file-input-' + stockName).click();
}

// ✅ ALERT REMOVED
function handleFileSelect(input, stockName) {
    if (input.files && input.files[0]) {
        var reader = new FileReader();
        reader.onload = function (e) {
            stockImages[stockName] = e.target.result;
            // SAVE TO LOCAL STORAGE
            try {
                localStorage.setItem('chart_' + stockName, e.target.result);
                // alert("Chart saved to memory!"); // REMOVED
            } catch (err) {
                console.log("Storage full"); // Silent fail
            }
        }
        reader.readAsDataURL(input.files[0]);
    }
}

// ✅ CONFIRM & ALERT REMOVED
function deleteChart(stockName) {
    // Removed Confirm
    localStorage.removeItem('chart_' + stockName);
    delete stockImages[stockName];
    closePopup();
    // Removed Alert
}

function openCardPopup(stockName) {
    const saved = localStorage.getItem('chart_' + stockName);
    if(saved) stockImages[stockName] = saved;

    if(activeFilter === 'TODAY') {
        document.getElementById('popup-name').innerText = stockName;
        const imgContainer = document.getElementById('popup-img-container');
        const delBtn = document.getElementById('popup-delete-btn');
        
        // ✅ NEW IMAGE STYLE: Max Height 250px, Centered Horizontally, Top of Container
        imgContainer.innerHTML = saved ? 
            '<img src="' + saved + '" style="max-width: 100%; max-height: 250px; width: auto; height: auto; border-radius: 10px; display: block; margin: 0 auto; object-fit: contain;">' 
            : '<p style="color: var(--text-muted); font-size: 14px;">No chart uploaded yet.</p>';
            
        saved ? delBtn.classList.remove('hidden') : delBtn.classList.add('hidden');
        delBtn.onclick = function() { deleteChart(stockName); };
        document.getElementById('modal-overlay').classList.remove('hidden');
    }
}

function closePopup() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

// ✅ NEW: WEBSOCKET LOGIC (Replaces setInterval)
var socket = io();

socket.on('connect', function() {
    console.log("Connected to Server via WebSocket");
    document.getElementById('status-text').innerText = "LIVE SOCKET";
});

socket.on('update_data', function(data) {
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
