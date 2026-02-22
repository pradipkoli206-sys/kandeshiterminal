import socket
socket.setdefaulttimeout(30)
import base64
import os
import time
import threading
import requests
import json
import csv 
import gzip
import io
import websocket # ✅ NEW: For Upstox WebSocket
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

sentry_sdk.init(
    dsn="https://aac0bc931df658a93346ea2f56b635c9@o4510906603732992.ingest.us.sentry.io/4510906683424768",  
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,
    profiles_sample_rate=1.0,
)
# --- NEW IMPORTS FOR REAL GEMINI AI ---
import google.generativeai as genai
from PIL import Image

# --- NEW IMPORTS FOR TECHNICAL INDICATORS & FAST CHARTS ---
import pandas as pd
import matplotlib
matplotlib.use('Agg') # Headless optimization for server
import mplfinance as mpf

app = Flask(__name__, static_folder='static') # Configured static folder
app.config['SECRET_KEY'] = 'secret_key_for_websocket'

# --- WEBSOCKET CONFIGURATION ---
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- ENSURE STATIC FOLDER EXISTS ---
if not os.path.exists('static'):
    os.makedirs('static')

# --- 1. KEYS & TELEGRAM CONFIG (✅ UPDATED FOR UPSTOX) ---
API_KEY = os.environ.get("API_KEY") 
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "YOUR_UPSTOX_ACCESS_TOKEN") # ✅ Upstox Token
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

# ✅ UPSTOX GLOBAL HEADERS
upstox_headers = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {ACCESS_TOKEN}'
}

# ✅ TELEGRAM KEYS
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ✅ 10 GEMINI KEYS (UNLIMITED QUOTA)
GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1"),
    os.environ.get("GEMINI_API_KEY_2"),
    os.environ.get("GEMINI_API_KEY_3"),
    os.environ.get("GEMINI_API_KEY_4"),
    os.environ.get("GEMINI_API_KEY_5"),
    os.environ.get("GEMINI_API_KEY_6"),
    os.environ.get("GEMINI_API_KEY_7"),
    os.environ.get("GEMINI_API_KEY_8"),
    os.environ.get("GEMINI_API_KEY_9"),
    os.environ.get("GEMINI_API_KEY_10")
]
current_key_index = 0

# Configure First Available Gemini Key
for key in GEMINI_KEYS:
    if key:
        genai.configure(api_key=key)
        break

# --- GLOBAL DATA STORE ---
live_feed = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
confirmed_winner = None 
data_fetched_once = False
tokens_loaded = False 
sws = None 
system_active = True 
upstox_ws_url = None # ✅ Upstox WebSocket Auth URL

# ✅ NEW: STORE FOR SMC MATCHED STOCKS
SMC_MATCHED_STOCKS = {}

# ✅ NEW: STORE FOR PROCESS LOGS (TERMINAL)
PROCESS_LOGS = []

# --- CACHE & VARS ---
PRECALCULATED_DATA = {}
LIVE_VIX = 15.0 
paper_margin = 10000.0
paper_positions = {} 
paper_orders = []    

# ==========================================
# 📩 TELEGRAM ALERT FUNCTION
# ==========================================
def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram Send Error: {e}")

# ==========================================
# 🧠 THE MAGIC: 4 SEPARATE SMART CACHE COMPARTMENTS
# ==========================================
SMART_CACHE = {
    "15m_chart": {}, 
    "1h_chart": {},  
    "15m_ind": {},   
    "1d_ind": {}     
}

# --- NEW CACHE PERSISTENCE LOGIC ---
CACHE_FILE = "trading_cache.json"

def load_smart_cache():
    global SMART_CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                SMART_CACHE = json.load(f)
            print("✅ Smart Cache Loaded from Disk!")
        except Exception as e:
            print(f"⚠️ Cache Load Error: {e}")

def save_smart_cache():
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(SMART_CACHE, f)
    except Exception as e:
        print(f"⚠️ Cache Save Error: {e}")

# Load cache immediately on startup
load_smart_cache()

# ==========================================
# 📊 AUTOMATED PnL JOURNAL (NEW FEATURE)
# ==========================================
JOURNAL_FILE = "trading_journal.csv"

def log_trade_to_csv(symbol, trade_type, price, qty, time_str):
    """हा फंक्शन आपोआप तुझे सर्व ट्रेड्स एका CSV फाईलमध्ये सेव्ह करेल."""
    file_exists = os.path.isfile(JOURNAL_FILE)
    try:
        with open(JOURNAL_FILE, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['Date_Time', 'Symbol', 'Type', 'Qty', 'Entry_Price', 'Status'])
            writer.writerow([time_str, symbol, trade_type, qty, price, 'Executed'])
    except Exception as e:
        print(f"⚠️ Error saving to CSV Journal: {e}")

# ==========================================
# 🚀 THE ULTIMATE SOLUTION: SAFE API CALLER (✅ UPSTOX SUPERFAST)
# ==========================================
# Upstox ची स्पीड ५० रिक्वेस्ट/सेकंद आहे, त्यामुळे time.sleep() काढून टाकले आहे!
api_lock = threading.Lock()

def safe_api_call(url):
    try:
        # No artificial delays needed for Upstox
        response = requests.get(url, headers=upstox_headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429: # Rate limit just in case
            time.sleep(1)
            return requests.get(url, headers=upstox_headers, timeout=10).json()
    except Exception as e:
        print(f"❌ API Error: {e}")
    return None

# ==========================================
# ✅ NEW: SMART LOG EMITTER (TERMINAL)
# ==========================================
def emit_log(msg):
    """बॅकग्राउंड प्रोसेसचे लॉग्स UI च्या Process टॅबवर पाठवण्यासाठी."""
    global PROCESS_LOGS
    now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S")
    full_msg = f"[{now}] {msg}"
    PROCESS_LOGS.append(full_msg)
    if len(PROCESS_LOGS) > 100:  
        PROCESS_LOGS.pop(0)
    socketio.emit('new_process_log', {'msg': full_msg}) 

# ==========================================
# 🧠 SMART DATA FETCHER (✅ UPSTOX API INTEGRATION)
# ==========================================
def fetch_smart_data(instrument_key, interval_str, days_back, compartment_name):
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    cutoff_dt = ist_now - timedelta(days=days_back)
    
    # Upstox Formatting
    todate_str = ist_now.strftime("%Y-%m-%d")
    fromdate_str = cutoff_dt.strftime("%Y-%m-%d")
    
    upstox_interval = 'day'
    if interval_str == "FIFTEEN_MINUTE": upstox_interval = '15minute'
    elif interval_str == "ONE_HOUR": upstox_interval = '60minute'
    elif interval_str == "ONE_DAY": upstox_interval = 'day'

    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{upstox_interval}/{todate_str}/{fromdate_str}"
    
    cached_data = SMART_CACHE[compartment_name].get(instrument_key, [])
    
    res = safe_api_call(url)
    if res and res.get('status') == 'success' and 'data' in res and 'candles' in res['data']:
        formatted_data = []
        # Upstox returns newest data first, so we reverse it to oldest first
        for candle in reversed(res['data']['candles']):
            ts = candle[0][:16].replace("T", " ") # Keep timestamp format compatible
            formatted_data.append([ts, candle[1], candle[2], candle[3], candle[4], candle[5]])
            
        SMART_CACHE[compartment_name][instrument_key] = formatted_data
        save_smart_cache()
        return formatted_data
        
    return cached_data

# --- 1.1 HOLIDAYS (✅ BYPASSED FOR 24/7 RUNNING) ---
NSE_HOLIDAYS = [] # No holidays! System will run continuously

def get_next_market_open(current_ist):
    return "ALWAYS OPEN (24/7)"

# --- MEMORY SYSTEM ---
STATE_FILE = "trade_state.json"

def save_state(winner, sector_code):
    try:
        data = {"date": str(datetime.now(timezone.utc).date()), "winner": winner, "code": sector_code}
        with open(STATE_FILE, "w") as f: json.dump(data, f)
    except: pass

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == str(datetime.now(timezone.utc).date()):
                    return data["winner"], data["code"]
    except: pass
    return None, "ALL"

# --- 2. STOCK CONFIGURATION ---
TARGET_STOCKS = [
    {"name": "SOUTHBANK", "cat": "BANK"}, {"name": "CENTRALBK", "cat": "BANK"},
    {"name": "UCOBANK", "cat": "BANK"}, {"name": "IDFCFIRSTB","cat": "BANK"},
    {"name": "RTNINDIA", "cat": "POWER"}, {"name": "OLAELEC", "cat": "AUTO"},
    {"name": "TTML", "cat": "IT"}, {"name": "HFCL", "cat": "IT"},
    {"name": "NHPC", "cat": "POWER"}, {"name": "NMDC", "cat": "METAL"},
    {"name": "GMRAIRPORT", "cat": "INFRA"}, {"name": "BAJAJHFL", "cat": "FINANCE"},
    {"name": "NTPCGREEN", "cat": "POWER"}, {"name": "PNB", "cat": "BANK"},
    {"name": "SJVN", "cat": "POWER"}, {"name": "IRFC", "cat": "RAILWAY"},
    {"name": "IRB", "cat": "INFRA"}, {"name": "TRIDENT", "cat": "TEXTILE"},
    {"name": "NETWORK18", "cat": "MEDIA"}, {"name": "J&KBANK", "cat": "BANK"},
    {"name": "BANKINDIA", "cat": "BANK"}, {"name": "MOREPENLAB", "cat": "PHARMA"},
    {"name": "IBREALEST", "cat": "REALTY"}, {"name": "PARADEEP", "cat": "CHEMICAL"},
    {"name": "DCBBANK", "cat": "BANK"}, {"name": "NBCC", "cat": "INFRA"},
    {"name": "IDBI", "cat": "BANK"}, {"name": "INOXWIND", "cat": "POWER"},
    {"name": "EQUITASBNK", "cat": "BANK"}, {"name": "NFL", "cat": "CHEMICAL"}
]

# ✅ Updated Indices to Match Upstox Format (Can be updated dynamically later)
INDICES_LIST = [
    {"name": "NIFTY", "token": "NSE_INDEX|Nifty 50", "symbol": "Nifty 50"},
    {"name": "BANKNIFTY", "token": "NSE_INDEX|Nifty Bank", "symbol": "Nifty Bank"},
    {"name": "NIFTY_IT", "token": "NSE_INDEX|Nifty IT", "symbol": "Nifty IT"},
    {"name": "NIFTY_AUTO", "token": "NSE_INDEX|Nifty Auto", "symbol": "Nifty Auto"},
    {"name": "INDIA_VIX", "token": "NSE_INDEX|India VIX", "symbol": "India VIX"}
]

STOCKS = [{"name": s["name"], "cat": s["cat"], "token": None, "price": "WAIT...", "change": "WAIT...", "symbol": s["name"], "status_msg": "Initializing..."} for s in TARGET_STOCKS]

def execute_paper_order(symbol, signal, price):
    global paper_margin, paper_positions, paper_orders
    if signal not in ["BUY", "SELL"]: return
    qty = 10; cost = qty * price
    time_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%d-%m-%Y %I:%M %p")
    
    if signal == "BUY" and paper_margin >= cost:
        paper_margin -= cost
        paper_orders.append({'symbol': symbol, 'type': 'BUY', 'price': price, 'qty': qty, 'status': 'Executed', 'time': time_str})
        if symbol in paper_positions: paper_positions[symbol]['qty'] += qty
        else: paper_positions[symbol] = {'qty': qty, 'avg_price': price, 'type': 'BUY', 'm2m': 0.0}
        log_trade_to_csv(symbol, "BUY", price, qty, time_str) 
        emit_log(f"📝 PAPER TRADE: BUY 10 Qty of {symbol} @ ₹{price}")
        
    elif signal == "SELL":
        paper_orders.append({'symbol': symbol, 'type': 'SELL', 'price': price, 'qty': qty, 'status': 'Executed', 'time': time_str})
        if symbol in paper_positions:
            paper_positions[symbol]['qty'] -= qty
            if paper_positions[symbol]['qty'] <= 0: del paper_positions[symbol]
        else: paper_positions[symbol] = {'qty': qty, 'avg_price': price, 'type': 'SELL', 'm2m': 0.0}
        log_trade_to_csv(symbol, "SELL", price, qty, time_str)
        emit_log(f"📝 PAPER TRADE: SELL 10 Qty of {symbol} @ ₹{price}")

# --- 3. AUTO-SCANNER (GET TOKENS FROM UPSTOX CSV) ---
def fetch_correct_tokens():
    global STOCKS
    emit_log("🔎 STARTING UPSTOX DYNAMIC TOKEN SCANNER (30 STOCKS)...")
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
    
    req_headers = {'User-Agent': 'Mozilla/5.0'}
    temp_stocks = []
    
    try:
        res = requests.get(url, headers=req_headers, timeout=20)
        if res.status_code == 200:
            with gzip.open(io.BytesIO(res.content), 'rt', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                mapping = {}
                for row in reader:
                    if row.get('instrument_type') == 'EQUITY':
                        mapping[row['tradingsymbol']] = row['instrument_key']
                
                for item in TARGET_STOCKS:
                    name = item["name"]
                    cat = item["cat"]
                    if name in mapping:
                        found_token = mapping[name]
                        emit_log(f"✅ FOUND: {name} -> Token {found_token}")
                        temp_stocks.append({"name": name, "token": found_token, "symbol": name, "cat": cat, "price": "0.00", "change": "0.00", "status_msg": "Token Found."})
                    else:
                        emit_log(f"❌ FAILED: {name} Token not found.")
                        temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR", "change": "ERR", "status_msg": "❌ Error: Token Not Found"})
    except Exception as e:
        emit_log(f"⚠️ Fetch Error: {str(e)[:30]}")
    
    if temp_stocks:
        STOCKS = temp_stocks
    emit_log("✅ SCAN COMPLETE: Ready for Market Data.")

# --- 4. UPSTOX LIVE DATA FEED (SUPER FAST REST REPLACING WEBSOCKET) ---
# Upstox v3 WS requires Protobuf decoding. To keep the system 100% crash-free and identical in output,
# we use the Upstox v2 Quotes API which gives real-time tick data exactly like WS but in JSON format.
def start_websocket_thread():
    global live_feed, system_active, STOCKS, INDICES_LIST
    emit_log("🌐 Starting Upstox Fast Live Data Stream (1-sec intervals)...")
    
    while system_active:
        try:
            # Gather all tokens
            all_tokens = [ind["token"] for ind in INDICES_LIST]
            all_tokens += [s["token"] for s in STOCKS if s.get("token")]
            
            if not all_tokens:
                time.sleep(2)
                continue
                
            # Request live quotes for all tokens at once
            keys_str = ",".join(all_tokens)
            url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys_str}"
            
            res = requests.get(url, headers=upstox_headers, timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', {})
                for key, quote in data.items():
                    # Safely extract best bid/ask
                    best_bid = 0.0
                    best_ask = 0.0
                    try:
                        best_bid = quote.get('depth', {}).get('buy', [{'price': 0}])[0].get('price', 0)
                        best_ask = quote.get('depth', {}).get('sell', [{'price': 0}])[0].get('price', 0)
                    except: pass

                    live_feed[key] = {
                        'ltp': quote.get('last_price', 0.0),
                        'open': quote.get('open', 0.0),
                        'low': quote.get('low', 0.0),
                        'volume': quote.get('volume', 0),
                        'best_bid': best_bid,
                        'best_ask': best_ask
                    }
            time.sleep(1) # Refresh every 1 second (Safe under 50 req/sec limit)
        except Exception as e:
            time.sleep(2)

# --- 5. MAIN LOGIC ENGINE ---
def fetch_offline_prices():
    global STOCKS, live_feed
    emit_log("🌐 Fetching Initial Prices...")
    for s in STOCKS:
        try:
            if s.get("token"):
                url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={s['token']}"
                res = safe_api_call(url)
                if res and res.get('status') == 'success' and res.get('data'):
                    quote = res['data'].get(s['token'], {})
                    last_price = quote.get('last_price', 0.0)
                    open_price = quote.get('open', 0.0)
                    
                    s["price"] = f"{last_price:.2f}"
                    s["change"] = f"{(last_price - open_price):+.2f}"
                    s["status_msg"] = "Loaded Offline Prices"
                    
                    live_feed[s["token"]] = {
                        'ltp': last_price, 
                        'open': open_price,
                        'low': quote.get('low', 0.0), 
                        'volume': quote.get('volume', 0)
                    }
        except Exception as e:
            s["status_msg"] = "⚠️ Fetch error"

def start_engine():
    global live_feed, market_status, ans1_nifty, ans2_sector, winning_sector_code, tokens_loaded, system_active, confirmed_winner, STOCKS, LIVE_VIX, SMC_MATCHED_STOCKS, PROCESS_LOGS
    
    confirmed_winner, loaded_code = load_state()
    winning_sector_code = loaded_code
    if confirmed_winner:
        ans2_sector = confirmed_winner + " SECTOR"
    
    while True:
        try:
            if not system_active:
                market_status = "PAUSED"
                ans1_nifty = "OFF"; ans2_sector = "OFF"
                socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS, "paper_margin": paper_margin, "paper_positions": paper_positions, "smc_matches": SMC_MATCHED_STOCKS, "logs": PROCESS_LOGS})
                time.sleep(4) 
                continue
            
            # ✅ ALL TIME & MARKET HOLIDAY CHECKS COMPLETELY REMOVED! (Runs 24/7)
            market_status = "LIVE"

            # --- INITIALIZATION ---
            if not tokens_loaded:
                fetch_correct_tokens()
                valid_cnt = sum(1 for s in STOCKS if s.get("token"))
                if valid_cnt > 0:
                    tokens_loaded = True
                    emit_log("🚀 Engine Started. Fetching Upstox Live Data...")
                    
                    # Start the fast live data thread
                    ws_thread = threading.Thread(target=start_websocket_thread)
                    ws_thread.daemon = True
                    ws_thread.start()
                else:
                    time.sleep(5)
                    continue

            # --- ✅ DYNAMIC SECTOR CALCULATIONS & LIVE VIX (24/7 RUNNING) ---
            sector_totals = {}
            sector_counts = {}
            
            for s in STOCKS:
                tk = s.get("token")
                cat = s.get("cat")
                if tk and tk in live_feed and cat:
                    ltp = live_feed[tk].get('ltp', 0)
                    opn = live_feed[tk].get('open', 0)
                    if opn > 0 and ltp > 0:
                        pct = ((ltp - opn) / opn) * 100
                        sector_totals[cat] = sector_totals.get(cat, 0.0) + pct
                        sector_counts[cat] = sector_counts.get(cat, 0) + 1
            
            sector_avgs = {}
            for cat in sector_totals:
                sector_avgs[cat] = sector_totals[cat] / sector_counts[cat]
            
            for ind in INDICES_LIST:
                tk = ind["token"]
                if tk in live_feed:
                    l = live_feed[tk].get('ltp', 0)
                    o = live_feed[tk].get('open', 0)
                    
                    if ind["name"] == "INDIA_VIX" and l > 0:
                        if l < 100: 
                            LIVE_VIX = l
                        
                    if ind["name"] == "NIFTY" and o > 0:
                        ans1_nifty = f"{(l - o):+.2f}"
            
            # ✅ TIME LOCK REMOVED: Calculates Winner Instantly 24/7
            if sector_avgs:
                max_sector = max(sector_avgs, key=sector_avgs.get)
                max_val = sector_avgs[max_sector]

                if confirmed_winner is None:
                    if max_val > -100: # Removed strict >0 check for 24/7
                        confirmed_winner = max_sector
                        winning_sector_code = confirmed_winner
                        ans2_sector = confirmed_winner + " SECTOR"
                        emit_log(f"🏆 Strongest Sector Identified: {confirmed_winner}")
                        save_state(confirmed_winner, winning_sector_code)
                else:
                    curr_val = sector_avgs.get(confirmed_winner, -100.0)
                    if max_sector != confirmed_winner and (max_val - curr_val) > 0.1:
                        confirmed_winner = max_sector
                        winning_sector_code = confirmed_winner
                        ans2_sector = confirmed_winner + " SECTOR"
                        emit_log(f"🔄 Sector Shift! New Winner: {confirmed_winner}")
                        save_state(confirmed_winner, winning_sector_code)
            else:
                ans2_sector = "CALCULATING..."

            # --- UPDATE STOCKS FROM LIVE FEED ---
            for s in STOCKS:
                tk = s.get("token")
                if tk and tk in live_feed:
                    ltp = live_feed[tk].get("ltp", 0); opn = live_feed[tk].get("open", 0)
                    if ltp > 0:
                        s["price"] = f"{ltp:.2f}"
                        s["change"] = f"{(ltp - opn):+.2f}" if opn > 0 else "WAIT..."
                        if s["status_msg"] in ["Initializing...", "Token Found.", "Loaded Offline Prices"]:
                            s["status_msg"] = "Live Tracking Active 🟢"
                    else: 
                        s["price"] = "WAIT..."
                        s["status_msg"] = "Waiting for live feed..."

            # --- UPDATE PAPER TRADING M2M ---
            for p_sym, p_data in paper_positions.items():
                p_tk = next((x["token"] for x in STOCKS if x["name"] == p_sym), None)
                if p_tk and p_tk in live_feed:
                    p_ltp = live_feed[p_tk].get("ltp", 0)
                    if p_ltp > 0:
                        if p_data['type'] == 'BUY': p_data['m2m'] = (p_ltp - p_data['avg_price']) * p_data['qty']
                        else: p_data['m2m'] = (p_data['avg_price'] - p_ltp) * p_data['qty']

            socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS, "paper_margin": paper_margin, "paper_positions": paper_positions, "smc_matches": SMC_MATCHED_STOCKS, "logs": PROCESS_LOGS})
            
            time.sleep(1) 

        except Exception as e:
            emit_log(f"❌ Main Engine Error: {str(e)[:30]}")
            time.sleep(5) 

# --- NEW: FAST PYTHON CHARTS (IN-MEMORY & SMART CACHE) ---
def generate_mpl_chart(symbol, interval):
    global STOCKS
    
    token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token: return False, None

    try:
        if interval == "15m":
            cached_data = fetch_smart_data(token, "FIFTEEN_MINUTE", 5, "15m_chart")
        else:
            cached_data = fetch_smart_data(token, "ONE_HOUR", 30, "1h_chart")
            
        if not cached_data: return False, None
            
        cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        df = pd.DataFrame(cached_data, columns=cols)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        
        df = df.dropna()
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna()
        if df.empty: return False, None
        
        # Chart Drawing
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='#39FF14', down='#FF3131', edge='inherit', wick='inherit', volume='in', ohlc='i')
        s  = mpf.make_mpf_style(marketcolors=mc, facecolor='#000508', edgecolor='#00ffff', figcolor='#000508', gridcolor='#003333', gridstyle='--')
        
        mpf.plot(df, type='candle', style=s, volume=False, savefig=dict(fname=buf, format='png', dpi=100, bbox_inches='tight', pad_inches=0.1), figsize=(6, 3))
        
        buf.seek(0)
        img_bytes = buf.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        base64_string = f"data:image/png;base64,{img_base64}"
        pil_image = Image.open(io.BytesIO(img_bytes))
        
        return base64_string, pil_image
        
    except Exception as e:
        print(f"⚠️ MPL Chart Error {symbol}: {e}")
        return False, None

# --- FUNCTION: CALCULATE INDICATORS (SMART CACHE MODE) ---
def get_technical_indicators(symbol):
    global STOCKS
    token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token: return "N/A", "N/A", "N/A", 0

    try:
        data_15m = fetch_smart_data(token, "FIFTEEN_MINUTE", 5, "15m_ind")
        data_daily = fetch_smart_data(token, "ONE_DAY", 15, "1d_ind")
        
        avg_vol_10d = 0
        if data_daily:
            df_daily = pd.DataFrame(data_daily, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df_daily['Volume'] = pd.to_numeric(df_daily['Volume'], errors='coerce')
            avg_vol_10d = df_daily['Volume'].tail(10).mean()
        
        if not data_15m: return "N/A", "N/A", "N/A", avg_vol_10d
            
        df = pd.DataFrame(data_15m, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        df = df.dropna()
        if df.empty: return "N/A", "N/A", "N/A", avg_vol_10d
            
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_12 - ema_26
        
        latest = df.iloc[-1]
        return f"{latest['RSI_14']:.2f}", f"{latest['SMA_20']:.2f}", f"{latest['MACD']:.2f}", avg_vol_10d
    except: return "Error", "Error", "Error", 0

# --- CORE ANALYSIS FUNCTION (SOLVES THE 10s WAIT ERROR) ---
def run_smart_analysis(symbol, is_cron=False):
    global PRECALCULATED_DATA, live_feed, STOCKS, LIVE_VIX, INDICES_LIST, paper_positions, current_key_index, GEMINI_KEYS, SMART_CACHE, SMC_MATCHED_STOCKS
    
    s = next((x for x in STOCKS if x["name"] == symbol), None)
    if not s: return None

    if s: s["status_msg"] = "⚙️ Checking Math Rules..."
    if not is_cron: emit_log(f"⚙️ {symbol}: Manual scan requested. Checking Math Filters...")

    nifty_pct = 0.0
    n_tk = next((i["token"] for i in INDICES_LIST if i["name"] == "NIFTY"), None)
    if n_tk and n_tk in live_feed:
        l = live_feed[n_tk].get('ltp', 0); o = live_feed[n_tk].get('open', 0)
        if o > 0: nifty_pct = ((l - o) / o) * 100

    stock_token = s.get("token")
    live_ltp = 0.0; live_open = 0.0; live_low = 0.0; live_vol = 0
    live_bid = "N/A"; live_ask = "N/A"; stock_pct = 0.0
    
    if stock_token and stock_token in live_feed:
        f = live_feed[stock_token]
        live_ltp = f.get("ltp", 0.0); live_open = f.get("open", 0.0)
        live_low = f.get("low", 0.0); live_vol = f.get("volume", 0)
        live_bid = f.get("best_bid", "N/A"); live_ask = f.get("best_ask", "N/A")
        if live_open > 0: stock_pct = ((live_ltp - live_open) / live_open) * 100

    rsi_val, sma_val, macd_val, avg_vol_10d = get_technical_indicators(symbol)
    
    math_smc_passed = False
    vol_spike_1_5x = False
    math_msg = "Math Data Unavailable"
    try:
        cached_15m = SMART_CACHE["15m_chart"].get(stock_token, [])
        if len(cached_15m) >= 6:
            df_math = pd.DataFrame(cached_15m, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df_math['Volume'] = pd.to_numeric(df_math['Volume'], errors='coerce')
            df_math['Low'] = pd.to_numeric(df_math['Low'], errors='coerce')
            df_math['High'] = pd.to_numeric(df_math['High'], errors='coerce')
            
            last_5_vol_avg = df_math['Volume'].iloc[-6:-1].mean()
            latest_vol = df_math['Volume'].iloc[-1]
            if latest_vol > (1.5 * last_5_vol_avg):
                vol_spike_1_5x = True
            
            recent_lows = df_math['Low'].iloc[-6:-1].min()
            recent_highs = df_math['High'].iloc[-6:-1].max()
            curr_low = df_math['Low'].iloc[-1]
            curr_high = df_math['High'].iloc[-1]
            
            sweep_detected = (curr_low < recent_lows) or (curr_high > recent_highs)
            
            if vol_spike_1_5x and sweep_detected:
                math_smc_passed = True
                math_msg = "✅ TRUE (Mathematical Sweep & 1.5x Volume Spike Detected!)"
            else:
                math_msg = f"❌ FALSE (Sweep: {sweep_detected}, 1.5x Vol Spike: {vol_spike_1_5x})"
    except:
        pass

    filter_passed = True; rejection_reason = ""
    # ✅ is_weekend check removed for 24/7 scanning!

    if LIVE_VIX > 20:
        filter_passed = False
        rejection_reason = f"VIX > 20 ({LIVE_VIX:.2f})"
    elif stock_pct <= nifty_pct:
        filter_passed = False
        rejection_reason = f"RS Fail ({stock_pct:.2f}% < {nifty_pct:.2f}%)"
    elif live_ltp <= live_open:
        filter_passed = False
        rejection_reason = f"Stock is Not Positive (LTP: {live_ltp} <= Open: {live_open})"
    # Note: Volume check kept relaxed

    #if not filter_passed:
       # if s: s["status_msg"] = f"⚠️ Skipped: {rejection_reason}"
        #if is_cron: emit_log(f"⏭️ {symbol}: SKIPPED. Reason: {rejection_reason}")
        
        fallback_data = {
            "status": "success",  
            "charts": {"15m": "", "1h": ""},
            "ai_analysis": f"<span style='color:var(--accent-red); font-weight:800;'>🚨 ALGO WARNING: Math Filter Failed!</span><br><span style='font-size:12px; font-weight:normal; color:#E0E0E0;'>Reason: {rejection_reason}</span>",
            "ai_analysis_2": "Skipped. No chart generation needed.",
            "ai_analysis_3": "Skipped. Waiting for perfect setup.",
            "ai_signal": "HOLD (SKIPPED)", "ai_table_data": "0|0|0|0",
            "ai_smc_data": "Skipped.",
            "ai_trap_data": "Skipped."
        }
        PRECALCULATED_DATA[symbol] = fallback_data
        return fallback_data

    if s: s["status_msg"] = "🧠 Processing AI Request..."
    emit_log(f"✅ {symbol}: PASSED Math Filter! Generating Charts...")
    
    c15_b64, img15_pil = generate_mpl_chart(symbol, "15m")
    c1h_b64, img1h_pil = generate_mpl_chart(symbol, "1h")

    warning_html = "<span style='color:var(--accent-green); font-weight:800;'>✅ ALGO PASSED: Smart Money Detected!</span><br><br>"

    valid_keys = [k for k in GEMINI_KEYS if k]
    if valid_keys:
        try:
            emit_log(f"🤖 {symbol}: Sending data to Gemini AI for Deep Analysis...")
            # 🧠 THE ULTIMATE ANTI-TRAP AI PROMPT (UPDATED FOR RELAXED FILTER)
            prompt_text = f"""
            You are an expert stock market technical analyst. 
            Analyze these two charts (15-minute and 1-hour) for the stock '{symbol}'.
            
            [IMPORTANT LIVE MARKET DATA]
            - Current Price (LTP): {live_ltp}
            - Today's Open: {live_open} (Stock is Positive/Green)
            - Total Volume Today: {live_vol}
            - Best Buyer Price (Best Bid): {live_bid}
            - Best Seller Price (Best Ask): {live_ask}
            - INDIA VIX (Simulated Panic Level): {LIVE_VIX}
            - 🧮 PYTHON MATH SMC CHECK (Sweep + 1.5x Vol Spike): {math_msg}
            
            [LIVE TECHNICAL INDICATORS DATA (15m Timeframe)]
            - RSI (14): {rsi_val}
            - SMA (20): {sma_val}
            - MACD: {macd_val}
            
            Please answer the following questions strictly in MARATHI language.
            You must split your response into EIGHT sections using EXACTLY the word '---SPLIT---' between them.
            
            SECTION 1 Questions:
            1) दोन्ही चार्ट आणि व्हॉल्यूम डेटा काय सांगत आहेत, अपट्रेड आहे की डाउनट्रेड?
            2) smc lines, fvg, ob आणि दिलेले RSI, MACD व SMA इंडिकेटर्स काय सांगत आहे? 
            3) smc नुसार आणि इंडिकेटर्स नुसार किती पर्सेंटेज आहे buy/sell साठी?
            4) ट्रेंड केव्हा बदलेल?

            ---SPLIT---

            SECTION 2 Questions (Smart Money Concepts, Liquidity & Volume Trap):
            1) या झोन मध्ये येणे आधी किंमतीने मागचा हाय किंवा लो स्वीप करून स्टॉपलॉस उडवले आहेत का?
            2) या झोनच्यावर किंवा खाली रिटेलर्स ना अडकवण्यासाठी काही आमिष (Liquidity) आहे का?
            3) जरी येथे इंड्युसमेंट नसेल तर त्याला फेक म्हणून घोषित कर?
            4) ऑपरेटर चा ट्रॅप फेल झाला असेल आणि रस्ता मोकळा असेल तेव्हा सांग?

            ---SPLIT---

            SECTION 3 Questions (Final Action):
            entry kuthe gyaychi stoploss kuthe taragate kuthe risk rewrd kiti kiti extreme detail madhe theu 10000 capital var kiti shares geun 10000 capital var risk kiti? 

            ---SPLIT---

            SECTION 4 (Signal & VIX Kill Switch):
            If INDIA VIX > 20 or if you detect a FAKE Trap/Inducement based on the rules, strictly output: '🚫 PANIC - NO TRADE' or 'HOLD (TRAP DETECTED)'.
            Otherwise, strictly output ONLY ONE WORD based on your analysis: 'BUY', 'SELL', or 'HOLD'. 

            ---SPLIT---

            SECTION 5 (Table Data):
            Strictly output ONLY precise numerical values separated by a pipe character '|' in this exact order:
            Entry|Stoploss|Target|RiskReward
            Example: 150.25|148.10|155.00|1:2

            ---SPLIT---

            SECTION 6 (SMC Strategy specific questions):
            Answer these 5 questions IN EXTREME DETAIL, EXPLAINING THE 'WHY' STRICTLY IN MARATHI language. Do not just say yes/no, provide the reasoning based on the charts:
            1) मार्केट अपट्रेंड मध्ये आहे का?
            2) price support level var aali ka?
            3) तिथे बुलिश hammer candle banli aahe ka?  
            4) volume vadhla aahe ka?
            5) indicators ky sangt aahet?

            ---SPLIT---

            SECTION 7 (Operator Trap Analysis):
            Answer these 5 questions IN EXTREME DETAIL, STRICTLY IN MARATHI language. Analyze deeply to save retail traders from Operator Traps and Smart Money Manipulation:
            1) हा ब्रेकआऊट 'Fakeout' (खोटा) आहे का? (लगेच मोठी लाल कॅन्डल तयार झाली असेल तर तसे सांगा).
            2) व्हॉल्यूममध्ये अचानक 'Abnormal' वाढ झाली आहे का? (Accumulation/Distribution होत आहे का ते ओळखा).
            3) लांब Wick/Shadow वाली कॅन्डल बनून Stop Loss Hunting झाले গান্ধ आहे का?
            4) 'V-Shape Recovery' द्वारे वीक हँड्सना (Weak Hands) बाहेर काढले आहे का?
            5) एन्ट्रीसाठी 'Retest' किंवा कन्फर्मेशन मिळाले आहे का?

            ---SPLIT---

            SECTION 8 (Telegram Alert Flag):
            Evaluate your answers from SECTION 6 and SECTION 7. If the MAIN SMC conditions are met (i.e. Market is in Uptrend OR strong Operator Volume Spike detected AND Stop Loss Hunting / Liquidity Sweep occurred), strictly output the exact word 'ALERT_TRUE'. If these main SMC/Trap conditions are NOT met, strictly output 'ALERT_FALSE'. Do not write any other text here.
            """
            
            contents = [prompt_text]
            if img15_pil and img1h_pil:
                contents.append(img15_pil)
                contents.append(img1h_pil)
            else:
                raise ValueError("Charts missing")

            # ✅ 10-KEY ROTATION LOGIC
            max_retries = len(valid_keys)
            full_text = ""

            for attempt in range(max_retries):
                try:
                    if s: s["status_msg"] = f"🤖 AI Generating (Attempt {attempt+1})..."
                    genai.configure(api_key=valid_keys[current_key_index])
                    model = genai.GenerativeModel('gemini-3-flash-preview')
                    response = model.generate_content(contents)
                    full_text = response.text
                    break 
                except Exception as ai_err:
                    emit_log(f"⚠️ API Key {current_key_index+1} failed. Switching to next...")
                    current_key_index = (current_key_index + 1) % len(valid_keys)
                    time.sleep(1)
                    continue
            parts = full_text.split("---SPLIT---")
            
            if len(parts) >= 7:
                ai_signal_text = parts[3].strip().upper()
                emit_log(f"✨ {symbol}: AI Analysis Complete. Signal -> {ai_signal_text}")
                
                result_data = {
                    "status": "success",
                    "charts": {"15m": c15_b64 if c15_b64 else "", "1h": c1h_b64 if c1h_b64 else ""},
                    "ai_analysis": warning_html + parts[0].strip().replace("\n", "<br>"),
                    "ai_analysis_2": parts[1].strip().replace("\n", "<br>"),
                    "ai_analysis_3": parts[2].strip().replace("\n", "<br>"),
                    "ai_signal": ai_signal_text,
                    "ai_table_data": parts[4].strip(),
                    "ai_smc_data": parts[5].strip().replace("\n", "<br>"),
                    "ai_trap_data": parts[6].strip().replace("\n", "<br>")
                }
                PRECALCULATED_DATA[symbol] = result_data
                if s: s["status_msg"] = f"✅ Signal: {ai_signal_text}"
                
                # 🤖 AUTO PAPER TRADING & CSV LOGGING
                # ✅ is_weekend removed from condition to allow 24/7 simulation
                if ("BUY" in ai_signal_text or "SELL" in ai_signal_text) and "TRAP" not in ai_signal_text and "PANIC" not in ai_signal_text and symbol not in paper_positions and filter_passed:
                    action = "BUY" if "BUY" in ai_signal_text else "SELL"
                    if live_ltp > 0:
                        execute_paper_order(symbol, action, live_ltp)

                # 📩 NEW: TELEGRAM SMC ALERT & UI DYNAMIC UPDATE
                if len(parts) >= 8:
                    alert_flag = parts[7].strip().upper()
                    if "ALERT_TRUE" in alert_flag and "TRAP" not in ai_signal_text and "PANIC" not in ai_signal_text:
                        
                        # ✅ SAVE TO LIVE SMC SCANNER UI
                        time_now_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%I:%M %p")
                        SMC_MATCHED_STOCKS[symbol] = {
                            "symbol": symbol,
                            "time": time_now_str,
                            "signal": ai_signal_text,
                            "ai_smc_data": result_data["ai_smc_data"],
                            "ai_trap_data": result_data["ai_trap_data"]
                        }
                        emit_log(f"🟢 {symbol} Added to LIVE SMC SCANNER!")

                        # ✅ SEND TELEGRAM ALERT
                        emit_log(f"📩 {symbol}: Telegram Alert Triggered! 🚀")
                        t_data = parts[4].strip().split('|')
                        entry_pr = t_data[0] if len(t_data) > 0 else live_ltp
                        sl_pr = t_data[1] if len(t_data) > 1 else "N/A"
                        tgt_pr = t_data[2] if len(t_data) > 2 else "N/A"
                        
                        tg_msg = f"🚀 <b>AI TRADER PRO - SMC ALERT</b> 🚀\n\n" \
                                 f"<b>Stock:</b> {symbol}\n" \
                                 f"<b>Live Price:</b> ₹{live_ltp}\n" \
                                 f"<b>Signal:</b> {ai_signal_text}\n\n" \
                                 f"✅ <i>SMC Strategy Main Conditions Matched! (Trend/Volume/StopLoss Hunting)</i>\n\n" \
                                 f"🎯 <b>Entry:</b> ₹{entry_pr}\n" \
                                 f"🛑 <b>Stoploss:</b> ₹{sl_pr}\n" \
                                 f"💰 <b>Target:</b> ₹{tgt_pr}"
                        
                        threading.Thread(target=send_telegram_alert, args=(tg_msg,), daemon=True).start()
                
                return result_data
                
        except Exception as e:
            emit_log(f"❌ AI Error for {symbol}: {str(e)[:30]}")
            if s: s["status_msg"] = "❌ AI Error Occurred"
            fallback_data = {
                "status": "success",
                "charts": {"15m": c15_b64 if c15_b64 else "", "1h": c1h_b64 if c1h_b64 else ""},
                "ai_analysis": warning_html + f"⚠️ AI Error: {e}",
                "ai_analysis_2": "N/A", "ai_analysis_3": "N/A",
                "ai_signal": "HOLD", "ai_table_data": "0|0|0|0",
                "ai_smc_data": "Error occurred.",
                "ai_trap_data": "Error occurred."
            }
            PRECALCULATED_DATA[symbol] = fallback_data
            return fallback_data

    return None

# --- 6. BACKGROUND CRON JOB (✅ 24/7 RUNNING - NO TIME LIMITS) ---
def background_ai_cron_job():
    global system_active, STOCKS
    emit_log("⚙️ Cron Job Initialized. Running 24/7 Mode...")
    while True:
        if not system_active: 
            time.sleep(10)
            continue
        
        # ✅ Removed 09:30 to 15:30 and Weekend checks completely!
        
        emit_log("🚀 Background AI Scanning Started (Iterating 30 Stocks)...")
        for s in STOCKS:
            if s: s["status_msg"] = "⏳ Scheduled for AI Scan"
            time.sleep(1.0) # ✅ Reduced from 4.0 to 1.0 since Upstox handles 50 req/sec!
            run_smart_analysis(s["name"], is_cron=True)
            
        emit_log("⏳ Scan Round Complete! Resting for 5 Minutes (300 seconds)...")
        time.sleep(30) 

# --- 7. FLASK ROUTES (✅ UNCHANGED) --- 
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    return jsonify({
        "status": market_status, 
        "ans1": ans1_nifty, 
        "ans2": ans2_sector, 
        "winner": winning_sector_code, 
        "stocks": STOCKS,
        "paper_margin": paper_margin,
        "paper_positions": paper_positions,
        "paper_orders": paper_orders,
        "smc_matches": SMC_MATCHED_STOCKS,
        "logs": PROCESS_LOGS 
    })
    
@socketio.on('toggle_engine')
def handle_toggle(data):
    global system_active
    system_active = data.get('state', True)
    status_msg = "SYSTEM STARTED" if system_active else "SYSTEM STOPPED"
    emit_log(f"⚙️ {status_msg}")

@app.route('/get_chart_screenshot/<symbol>')
def get_chart_screenshot(symbol):
    global system_active, PRECALCULATED_DATA
    if not system_active:
         return jsonify({"status": "error", "message": "System Paused"})

    if symbol in PRECALCULATED_DATA:
        return jsonify(PRECALCULATED_DATA[symbol])
    else:
        result = run_smart_analysis(symbol, is_cron=False)
        if result:
            return jsonify(result)
        else:
            return jsonify({"status": "error", "message": "Analysis Failed."})
# --- 8. HTML TEMPLATE (NEON CYBERPUNK THEME) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER PRO</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
<style>
/* --- NEON CYBERPUNK THEME VARIABLES --- */
:root {
    --bg-main: #000508;      /* Deepest Black */
    --bg-card: rgba(1, 12, 20, 0.7); /* Glassmorphism Transparent Dark */
    --text-main: #ffffff;    
    --text-muted: #00ffff;   /* Cyan Labels */
    --border: #00ffff;       /* Neon Border */
    --accent-blue: #00ffff;  /* Cyan Neon */
    --accent-green: #39FF14; /* Neon Green */
    --accent-red: #FF3131;   /* Neon Red */
    --accent-purple: #962eff; 
    --card-shadow: 0 0 15px rgba(0, 255, 255, 0.15); /* Soft Neon Glow */
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { 
    background-color: var(--bg-main); 
    color: var(--text-main); 
    font-family: 'Inter', sans-serif; 
    margin: 0; height: 100vh; display: flex; flex-direction: column; 
    overflow: hidden;
    font-weight: 700; /* Bold typography */
}

/* --- HEADER --- */
.header {
    padding: 15px 20px; 
    background: rgba(0, 5, 8, 0.9); /* Dark Glass */
    border-bottom: 1px solid rgba(0, 255, 255, 0.2);
    display: flex; justify-content: space-between; align-items: center;
    backdrop-filter: blur(10px);
}
.brand { 
    font-size: 20px; font-weight: 800; letter-spacing: 1px; 
    background: linear-gradient(135deg, var(--accent-blue), var(--text-main)); 
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; 
    text-transform: uppercase; /* Uppercase Typography */
    text-shadow: 0 0 10px rgba(0, 255, 255, 0.5); /* Brand Glow */
}
.header-controls {
    display: flex; align-items: center; gap: 10px;
}
.status-badge { 
    font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; 
    background: rgba(0, 255, 255, 0.1); color: var(--accent-blue); 
    border: 1px solid var(--accent-blue); letter-spacing: 0.5px;
    display: flex; align-items: center; gap: 5px;
    box-shadow: 0 0 5px var(--accent-blue); /* Badge Glow */
}
.status-dot { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 8px var(--accent-green); }

/* --- NEW SWITCH STYLES (Cyberpunk) --- */
.switch { position: relative; display: inline-block; width: 40px; height: 20px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
  background-color: rgba(255, 255, 255, 0.1); transition: .4s;
  border: 1px solid var(--accent-red);
}
.slider:before {
  position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 2px;
  background-color: var(--accent-red); transition: .4s; box-shadow: 0 0 5px var(--accent-red);
}
input:checked + .slider { background-color: rgba(0, 255, 255, 0.2); border-color: var(--accent-green); }
input:checked + .slider:before {
  transform: translateX(18px); background-color: var(--accent-green); box-shadow: 0 0 5px var(--accent-green);
}
.slider.round { border-radius: 20px; }
.slider.round:before { border-radius: 50%; }

/* --- NEW MARATHI STATUS BAR (LIVR PATII) --- */
.status-bar-marathi {
    background: rgba(0, 5, 8, 0.8); color: var(--accent-blue); font-size: 11px;
    text-align: center; padding: 5px; border-bottom: 1px solid var(--border);
    display: none; font-weight: 800;
}

/* --- TOP DASHBOARD AREA --- */
.dashboard-summary {
    display: flex; gap: 15px; padding: 20px; border-bottom: 1px solid rgba(0, 255, 255, 0.1);
    background: linear-gradient(180deg, rgba(0, 255, 255, 0.05) 0%, rgba(0,0,0,0) 100%);
}
.summary-card {
    flex: 1; background: var(--bg-card); border: 1px solid rgba(0, 255, 255, 0.3);
    border-radius: 16px; padding: 15px; display: flex; flex-direction: column; gap: 5px;
    box-shadow: 0 0 10px rgba(0, 255, 255, 0.1); backdrop-filter: blur(5px);
}
.summary-label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.8px; }
.summary-value { font-size: 18px; font-weight: 800; text-transform: uppercase; }
.txt-green { color: var(--accent-green); text-shadow: 0 0 10px rgba(57, 255, 20, 0.6); }
.txt-red { color: var(--accent-red); text-shadow: 0 0 10px rgba(255, 49, 49, 0.6); }
.txt-blue { color: var(--accent-blue); text-shadow: 0 0 10px rgba(0, 255, 255, 0.6); }

/* --- FILTER BAR --- */
.filter-bar { 
    padding: 15px 20px; display: flex; gap: 10px; 
    background: var(--bg-main); border-bottom: 1px solid rgba(0, 255, 255, 0.1);
    overflow-x: auto; scrollbar-width: none;
}
.filter-btn { 
    background: rgba(1, 12, 20, 0.8); border: 1px solid rgba(0, 255, 255, 0.3); color: var(--text-muted); 
    padding: 8px 16px; border-radius: 12px; font-size: 12px; font-weight: 700; 
    text-align: center; cursor: pointer; transition: all 0.3s; white-space: nowrap;
    text-transform: uppercase;
}
.filter-btn.active { 
    background: rgba(0, 255, 255, 0.2); color: white; border-color: var(--accent-blue); 
    box-shadow: 0 0 15px rgba(0, 255, 255, 0.4);
}

/* --- MAIN STOCK LIST --- */
.stock-list { 
    flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; touch-action: pan-y; 
}
.stock-card { 
    background: var(--bg-card); border: 1px solid rgba(0, 255, 255, 0.4); 
    padding: 18px 20px; border-radius: 16px; display: flex; justify-content: space-between; align-items: center;
    box-shadow: 0 0 8px rgba(0, 255, 255, 0.2); backdrop-filter: blur(8px);
    transition: all 0.3s ease; transform: translate3d(0, 0, 0); backface-visibility: hidden; cursor: pointer;
}
.stock-card:hover { transform: translateY(-2px) translate3d(0,0,0); box-shadow: 0 0 20px rgba(0, 255, 255, 0.5); border-color: var(--accent-blue); }

.st-info { display: flex; flex-direction: column; gap: 4px; }
.st-name { font-size: 16px; font-weight: 800; color: var(--text-main); text-transform: uppercase; }
.st-cat-tag { font-size: 10px; font-weight: 700; color: var(--text-muted); background: rgba(0, 255, 255, 0.1); padding: 3px 8px; border-radius: 6px; width: fit-content; text-transform: uppercase; border: 1px solid rgba(0, 255, 255, 0.2); }
.st-price-box { text-align: right; }
.st-price { font-size: 20px; font-weight: 800; color: var(--accent-green); text-shadow: 0 0 10px rgba(57, 255, 20, 0.6); }
.st-wait { font-size: 14px; font-weight: 600; color: var(--text-muted); }
.st-change { font-size: 12px; font-weight: 600; margin-top: 2px; }

/* --- SMC STRATEGY CONTAINER (MAIN PAGE) --- */
.smc-container { display: none; flex-direction: column; gap: 15px; padding: 20px; }
.smc-select {
    width: 100%; background: var(--bg-card); border: 1px solid var(--accent-purple); color: var(--accent-purple);
    padding: 12px; border-radius: 10px; font-weight: 800; outline: none; text-transform: uppercase;
    box-shadow: 0 0 8px rgba(150, 46, 255, 0.2); margin-bottom: 10px; font-family: 'Inter', sans-serif;
}
.smc-select option { background: var(--bg-main); color: var(--text-main); }
.smc-btn {
    width: 100%; background: rgba(150, 46, 255, 0.1); border: 1px solid var(--accent-purple); color: var(--accent-purple);
    padding: 12px; border-radius: 10px; font-weight: 800; cursor: pointer; transition: all 0.3s;
    box-shadow: 0 0 10px rgba(150, 46, 255, 0.2); font-family: 'Inter', sans-serif; letter-spacing: 1px;
}
.smc-btn:hover { background: rgba(150, 46, 255, 0.3); box-shadow: 0 0 15px rgba(150, 46, 255, 0.4); }

.gemini-card {
    background: rgba(150, 46, 255, 0.1); border: 1px solid var(--accent-purple); border-radius: 12px;
    padding: 15px; text-align: left; box-shadow: 0 0 10px rgba(150, 46, 255, 0.2); margin-bottom: 10px;
}
.gemini-title { font-size: 14px; font-weight: 800; color: var(--accent-purple); display: flex; align-items: center; gap: 5px; margin-bottom: 8px; text-transform: uppercase; }
.gemini-text { font-size: 12px; color: #E0E0E0; line-height: 1.5; font-weight: 600; }

/* --- POPUP WINDOW --- */
.modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 5, 8, 0.9); z-index: 2000; display: flex; justify-content: center; align-items: flex-start; padding-top: 40px; backdrop-filter: blur(10px); overflow-y: auto; }
.modal-box { background: rgba(1, 12, 20, 0.9); width: 85%; max-width: 350px; padding: 25px; border-radius: 20px; border: 1px solid var(--accent-blue); text-align: center; box-shadow: 0 0 30px rgba(0, 255, 255, 0.3); animation: popin 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); display: flex; flex-direction: column; gap: 15px; margin-bottom: 40px; position: relative; }
.modal-title { font-size: 18px; font-weight: 800; color: var(--text-main); margin-bottom: 5px; text-transform: uppercase; text-shadow: 0 0 5px var(--accent-blue); display: flex; align-items: center; justify-content: center; gap: 10px;}
.modal-close-icon { position: absolute; top: 15px; right: 15px; color: var(--accent-red); font-size: 24px; cursor: pointer; font-weight: 800; text-shadow: 0 0 5px var(--accent-red); }
.chart-label { font-size: 12px; font-weight: 700; color: var(--accent-blue); text-align: left; margin-bottom: 5px; margin-top: 10px; text-transform: uppercase; }
.ai-selector { width: 100%; background: var(--bg-card); border: 1px solid var(--accent-purple); color: var(--accent-purple); padding: 10px; border-radius: 10px; font-weight: 800; margin-top: 15px; cursor: pointer; outline: none; text-transform: uppercase; text-align: center; font-family: 'Inter', sans-serif; box-shadow: 0 0 8px rgba(150, 46, 255, 0.2); display: none; }
.ai-selector option { background: var(--bg-main); color: var(--text-main); }
.signal-badge { font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border); display: none; }
.signal-buy { background: rgba(57, 255, 20, 0.2); color: var(--accent-green); border-color: var(--accent-green); box-shadow: 0 0 8px rgba(57, 255, 20, 0.5); }
.signal-sell { background: rgba(255, 49, 49, 0.2); color: var(--accent-red); border-color: var(--accent-red); box-shadow: 0 0 8px rgba(255, 49, 49, 0.5); }
.signal-hold { background: rgba(0, 255, 255, 0.2); color: var(--accent-blue); border-color: var(--accent-blue); }

.action-table-container { margin-top: 15px; display: none; background: rgba(1, 12, 20, 0.8); border-radius: 10px; border: 1px solid var(--accent-purple); overflow: hidden; box-shadow: 0 0 10px rgba(150, 46, 255, 0.2); }
.action-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.action-table th { background: rgba(150, 46, 255, 0.2); color: var(--accent-purple); padding: 8px; text-transform: uppercase; font-weight: 800; border-bottom: 1px solid var(--accent-purple); }
.action-table td { color: #fff; padding: 10px 5px; font-weight: 700; text-align: center; border-right: 1px solid rgba(150, 46, 255, 0.2); }
.action-table td:last-child { border-right: none; }
@keyframes popin { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }

/* --- POSITIONS & MENU STYLES --- */
.page-section { display: none; flex: 1; overflow-y: auto; padding: 20px; flex-direction: column; gap: 12px; touch-action: pan-y; }
.page-section.active { display: flex; }
.trade-card { background: var(--bg-card); border: 1px solid rgba(0, 255, 255, 0.4); padding: 15px; border-radius: 12px; box-shadow: 0 0 8px rgba(0, 255, 255, 0.2); backdrop-filter: blur(8px); }
.trade-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px dashed rgba(0,255,255,0.2); padding-bottom: 8px; }
.trade-title { font-size: 16px; font-weight: 800; color: var(--text-main); }
.trade-type { font-size: 10px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; }
.type-buy { color: var(--accent-green); background: rgba(57, 255, 20, 0.1); border: 1px solid var(--accent-green); }
.type-sell { color: var(--accent-red); background: rgba(255, 49, 49, 0.1); border: 1px solid var(--accent-red); }
.trade-details { display: flex; justify-content: space-between; align-items: center; }
.trade-col { display: flex; flex-direction: column; gap: 5px; }
.trade-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; }
.trade-val { font-size: 14px; font-weight: 800; color: var(--text-main); }
.trade-col-right { text-align: right; }
.m2m-val { font-size: 16px; font-weight: 800; }
.m2m-green { color: var(--accent-green); text-shadow: 0 0 5px rgba(57,255,20,0.5); }
.m2m-red { color: var(--accent-red); text-shadow: 0 0 5px rgba(255,49,49,0.5); }
.orders-tab-bar { display: flex; gap: 10px; margin-bottom: 15px; }
.order-tab { flex: 1; text-align: center; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; font-weight: 700; color: var(--text-muted); cursor: pointer; transition: all 0.3s; text-transform: uppercase; }
.order-tab.active { background: rgba(0,255,255,0.2); color: white; box-shadow: 0 0 10px rgba(0,255,255,0.3); border-color: var(--accent-blue); }
.menu-profile { text-align: center; padding: 20px 0; border-bottom: 1px solid rgba(0,255,255,0.2); }
.menu-greeting { font-size: 16px; font-weight: 700; color: var(--text-muted); }
.menu-funds-label { font-size: 12px; font-weight: 700; margin-top: 15px; color: var(--text-muted); text-transform: uppercase;}
.menu-funds-val { font-size: 32px; font-weight: 800; color: var(--accent-green); text-shadow: 0 0 15px rgba(57, 255, 20, 0.6); margin-top: 5px; }
.menu-list { margin-top: 20px; display: flex; flex-direction: column; gap: 15px; }
.menu-item { display: flex; align-items: center; gap: 15px; padding: 15px; background: var(--bg-card); border: 1px solid rgba(0,255,255,0.2); border-radius: 12px; color: white; font-weight: 700; cursor: pointer; }
.menu-item .material-icons-outlined { color: var(--accent-blue); }
.empty-state { text-align: center; color: var(--text-muted); padding: 40px 20px; font-size: 14px; font-weight: 600; }

/* --- BOTTOM NAVIGATION --- */
.bottom-nav { height: 70px; background: rgba(0, 5, 8, 0.95); border-top: 1px solid rgba(0, 255, 255, 0.2); display: flex; justify-content: space-around; align-items: center; padding-bottom: 10px; backdrop-filter: blur(10px); }
.nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: rgba(255, 255, 255, 0.5); cursor: pointer; flex: 1; height: 100%; transition: all 0.2s; }
.nav-item.active { color: var(--accent-blue); text-shadow: 0 0 10px rgba(0, 255, 255, 0.6); }
.nav-icon { font-size: 26px; margin-bottom: 2px; }
.nav-label { font-size: 10px; font-weight: 600; text-transform: uppercase; }

.hidden { display: none !important; }
@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
.loading-pulse { animation: pulse 1.5s infinite; }
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: var(--bg-main); }
::-webkit-scrollbar-thumb { background: var(--accent-blue); border-radius: 10px; }
</style>
<script>
// ✅ FIX: Initialize with Server Variable immediately
let currentWinner = "{{ winner }}";
let activeFilter = "ALL";
let currentOrderTab = 'EXECUTED';

// --- NEW: MEMORY LATCHING (CACHE) ---
let stockDataCache = {}; // Stores AI analysis data temporarily

function filterStocks(type) {
    activeFilter = type;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-'+type).classList.add('active');
    
    // ✅ NEW: SMC STRATEGY TOGGLE LOGIC
    const normalList = document.getElementById('normal-stock-list');
    const smcContainer = document.getElementById('smc-container');
    
    if(type === 'SMC') {
        if(normalList) normalList.style.display = 'none';
        if(smcContainer) smcContainer.style.display = 'flex';
    } else {
        if(normalList) normalList.style.display = 'block';
        if(smcContainer) smcContainer.style.display = 'none';
        applyFilter();
    }
}

function applyFilter() {
    const cards = document.querySelectorAll('.stock-card');
    cards.forEach(card => {
        const cat = card.getAttribute('data-cat');
        const stockName = card.id.replace('card-', '');
        const statusEl = document.getElementById('status-' + stockName);
        
        let show = false;
        
        if (activeFilter === 'ALL') {
            show = true;
            if(statusEl) statusEl.style.display = 'block'; 
        }
        else if (activeFilter === 'TODAY') {
            show = true;
            if(statusEl) statusEl.style.display = 'none'; 
        }
        else if (activeFilter === 'AI') { 
            if (currentWinner === 'ALL' || cat === currentWinner) show = true;
            if(statusEl) statusEl.style.display = 'none'; 
        }

        if(show) card.classList.remove('hidden'); else card.classList.add('hidden');
    });
}

function switchPage(pageId, navElement) {
    document.querySelectorAll('.page-section').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + pageId).classList.add('active');
    
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    navElement.classList.add('active');
    
    const dash = document.querySelector('.dashboard-summary');
    const filt = document.querySelector('.filter-bar');
    if(pageId === 'home') {
        if(dash) dash.style.display = 'flex';
        if(filt) filt.style.display = 'flex';
    } else {
        if(dash) dash.style.display = 'none';
        if(filt) filt.style.display = 'none';
    }
}

function switchOrderTab(tab) {
    currentOrderTab = tab;
    document.getElementById('tab-exec').classList.remove('active');
    document.getElementById('tab-pend').classList.remove('active');
    if(tab === 'EXECUTED') document.getElementById('tab-exec').classList.add('active');
    else document.getElementById('tab-pend').classList.add('active');
    if (latestData) updateDashboard(latestData);
}

function changeAITab() {
    const selectedTab = document.getElementById('ai-selector').value;
    const c1 = document.getElementById('gemini-card');
    const c2 = document.getElementById('gemini-card-2');
    const c3 = document.getElementById('gemini-card-3');

    if(c1) c1.style.display = 'none';
    if(c2) c2.style.display = 'none';
    if(c3) c3.style.display = 'none';

    if (selectedTab === 'tab1' && c1) c1.style.display = 'block';
    else if (selectedTab === 'tab2' && c2) c2.style.display = 'block';
    else if (selectedTab === 'tab3' && c3) c3.style.display = 'block';
}
function openCardPopup(stockName) {
    if (activeFilter !== 'TODAY') return;

    // ✅ FIX: ९:३० ते ३:३० आणि मार्केट वेळेचे सर्व निर्बंध काढून टाकले आहेत! 
    // आता हा पॉपअप 24/7 कधीही ओपन होईल.

    document.getElementById('popup-name').innerText = stockName;
    document.getElementById('modal-overlay').classList.remove('hidden');

    const img15 = document.getElementById('img-15m');
    const img1h = document.getElementById('img-1h');
    const statusContainer = document.getElementById('status-container');
    const processMsg = document.getElementById('process-msg');
    const aiSelector = document.getElementById('ai-selector');
    const signalBadge = document.getElementById('ai-signal-badge');
    const tableContainer = document.getElementById('action-table-container');
    const geminiText = document.getElementById('gemini-text');
    const geminiText2 = document.getElementById('gemini-text-2');
    const geminiText3 = document.getElementById('gemini-text-3');

    if (stockDataCache[stockName]) {
        const cache = stockDataCache[stockName];
        img15.src = cache.img15;
        img1h.src = cache.img1h;
        img15.style.display = 'block';
        img1h.style.display = 'block';
        statusContainer.style.display = 'none';
        
        geminiText.innerHTML = cache.ai1;
        geminiText2.innerHTML = cache.ai2;
        if(geminiText3) geminiText3.innerHTML = cache.ai3;

        if(aiSelector) {
            aiSelector.style.display = 'block';
            aiSelector.value = 'tab1';
            changeAITab();
        }

        if (cache.signal) {
            signalBadge.innerText = cache.signal;
            signalBadge.style.display = 'inline-block';
            if(cache.signal.includes('BUY')) signalBadge.className = 'signal-badge signal-buy';
            else if(cache.signal.includes('SELL')) signalBadge.className = 'signal-badge signal-sell';
            else signalBadge.className = 'signal-badge signal-hold';
        }

        if (cache.table && cache.table.length >= 4) {
            document.getElementById('tb-entry').innerHTML = cache.table[0];
            document.getElementById('tb-stoploss').innerHTML = cache.table[1];
            document.getElementById('tb-target').innerHTML = cache.table[2];
            document.getElementById('tb-rr').innerHTML = cache.table[3];
            tableContainer.style.display = 'block';
        }

    } else {
        if(aiSelector) { aiSelector.style.display = 'none'; aiSelector.value = 'tab1'; }
        if(signalBadge) { signalBadge.style.display = 'none'; signalBadge.className = 'signal-badge'; signalBadge.innerText = ''; }
        if(tableContainer) tableContainer.style.display = 'none';
        document.getElementById('tb-entry').innerHTML = '-';
        document.getElementById('tb-stoploss').innerHTML = '-';
        document.getElementById('tb-target').innerHTML = '-';
        document.getElementById('tb-rr').innerHTML = '-';
        img15.style.display = 'none'; img15.src = "";
        img1h.style.display = 'none'; img1h.src = "";
        statusContainer.style.display = 'block';
        processMsg.innerText = "Ready to Scan";
        geminiText.innerHTML = "";
        geminiText2.innerHTML = "";
        if(geminiText3) geminiText3.innerHTML = "";
    }

    document.getElementById('btn-chart-read').onclick = function() {
        img15.style.display = 'none';
        img1h.style.display = 'none';
        statusContainer.style.display = 'block';
        processMsg.innerText = "Initializing...";
        
        geminiText.innerHTML = '<span class="loading-pulse">✨ AI विश्लेषण करत आहे (Analyzing)...</span>';
        geminiText2.innerHTML = '<span class="loading-pulse">✨ AI विश्लेषण करत आहे (Analyzing)...</span>';
        if(geminiText3) geminiText3.innerHTML = '<span class="loading-pulse">✨ AI विश्लेषण करत आहे (Analyzing)...</span>';

        if(aiSelector) aiSelector.style.display = 'block';
        changeAITab();

        fetch('/get_chart_screenshot/' + stockName)
        .then(response => response.json())
        .then(data => {
            if(data.status === 'success') {
                const img15Url = data.charts['15m']; 
                const img1hUrl = data.charts['1h'];
                
                img15.src = img15Url;
                img1h.src = img1hUrl;
                
                img15.onload = function() { statusContainer.style.display = 'none'; img15.style.display = 'block'; };
                img1h.onload = function() { img1h.style.display = 'block'; };
                
                geminiText.innerHTML = data.ai_analysis || "AI कडून माहिती मिळाली नाही.";
                geminiText2.innerHTML = data.ai_analysis_2 || "AI कडून माहिती मिळाली नाही.";
                if(geminiText3) geminiText3.innerHTML = data.ai_analysis_3 || "AI कडून माहिती मिळाली नाही.";

                let sigText = '';
                if (data.ai_signal && signalBadge) {
                    sigText = data.ai_signal.trim().toUpperCase();
                    signalBadge.innerText = sigText;
                    signalBadge.style.display = 'inline-block';
                    if(sigText.includes('BUY')) signalBadge.className = 'signal-badge signal-buy';
                    else if(sigText.includes('SELL')) signalBadge.className = 'signal-badge signal-sell';
                    else signalBadge.className = 'signal-badge signal-hold';
                }

                let tData = [];
                if (data.ai_table_data && tableContainer) {
                    const tParts = data.ai_table_data.split('|');
                    if (tParts.length >= 4) {
                        tData = [tParts[0].trim(), tParts[1].trim(), tParts[2].trim(), tParts[3].trim()];
                        document.getElementById('tb-entry').innerHTML = tData[0];
                        document.getElementById('tb-stoploss').innerHTML = tData[1];
                        document.getElementById('tb-target').innerHTML = tData[2];
                        document.getElementById('tb-rr').innerHTML = tData[3];
                        tableContainer.style.display = 'block';
                    }
                }

                stockDataCache[stockName] = {
                    img15: img15Url,
                    img1h: img1hUrl,
                    ai1: geminiText.innerHTML,
                    ai2: geminiText2.innerHTML,
                    ai3: geminiText3 ? geminiText3.innerHTML : "",
                    signal: sigText,
                    table: tData,
                    ai_smc: data.ai_smc_data,
                    ai_trap: data.ai_trap_data
                };

            } else {
                statusContainer.style.display = 'none'; 
                processMsg.innerText = "Error: " + data.message;
                processMsg.style.color = "var(--accent-red)";
                geminiText.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
                geminiText2.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
                if(geminiText3) geminiText3.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
            }
        })
        .catch(err => {
            statusContainer.style.display = 'none';
            processMsg.innerText = "Connection Failed";
            processMsg.style.color = "var(--accent-red)";
        });
    };
}

function closePopup() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

// ✅ NEW: WEBSOCKET LOGIC
var socket = io('/'); 
let latestData = null; 

socket.on('connect', function() {
    console.log("✅ [Socket] Connected.");
    document.getElementById('status-text').innerText = "LIVE SOCKET";
    const dot = document.querySelector('.status-dot');
    const badge = document.querySelector('.status-badge');
    if(dot && badge) {
        dot.style.backgroundColor = "var(--accent-green)";
        badge.style.borderColor = "var(--accent-blue)";
        badge.style.color = "var(--accent-green)";
    }
    const pati = document.getElementById('marathi-status');
    pati.style.display = 'block'; pati.style.color = "var(--accent-green)"; pati.style.background = "rgba(0, 5, 8, 0.8)"; pati.innerText = "वेबसाॅकेट कनेक्ट झाले आहे (LIVE)";
});

socket.on('disconnect', function() {
    document.getElementById('status-text').innerText = "OFFLINE";
    const dot = document.querySelector('.status-dot');
    const badge = document.querySelector('.status-badge');
    if(dot && badge) {
        dot.style.backgroundColor = "var(--accent-red)";
        badge.style.borderColor = "var(--accent-red)";
        badge.style.color = "var(--accent-red)";
    }
    const pati = document.getElementById('marathi-status');
    pati.style.display = 'block'; pati.style.color = "var(--accent-red)"; pati.style.background = "rgba(255, 49, 49, 0.1)"; pati.innerText = "वेबसाॅकेट बंद आहे (DISCONNECTED)";
});

socket.on('process_log', function(data) {
    const processMsg = document.getElementById('process-msg');
    if(processMsg) { processMsg.innerText = data.msg; processMsg.style.color = "var(--text-muted)"; }
});

// ✅ NEW: LISTENER FOR TERMINAL LOGS (PROCESS TAB)
socket.on('new_process_log', function(data) {
    const logContainer = document.getElementById('terminal-logs');
    if(logContainer) {
        let div = document.createElement('div');
        div.style.marginBottom = '6px';
        div.style.lineHeight = '1.4';
        div.style.borderBottom = '1px solid rgba(0, 255, 255, 0.1)';
        div.style.paddingBottom = '4px';
        
        let msg = data.msg;
        // Color coding logic based on the message content
        if(msg.includes('❌') || msg.includes('SKIPPED') || msg.includes('Error') || msg.includes('Failed') || msg.includes('🛑') || msg.includes('⚠️')) {
            div.style.color = 'var(--accent-red)';
        } else if(msg.includes('✅') || msg.includes('PASSED') || msg.includes('BUY') || msg.includes('SELL') || msg.includes('🟢') || msg.includes('🏆') || msg.includes('📝')) {
            div.style.color = 'var(--accent-green)';
        } else if(msg.includes('⚙️') || msg.includes('🤖') || msg.includes('✨') || msg.includes('🚀')) {
            div.style.color = 'var(--accent-purple)';
        } else {
            div.style.color = 'var(--accent-blue)';
        }
        div.innerText = msg;
        logContainer.appendChild(div);
        
        // Auto-scroll to the bottom of the terminal
        logContainer.scrollTop = logContainer.scrollHeight; 
    }
});

socket.on('update_data', function(data) {
    latestData = data; 
});

setInterval(() => {
    if (latestData) {
        updateDashboard(latestData);
        latestData = null; 
    }
}, 1000); 

function updateDashboard(data) {
    document.getElementById('status-text').innerText = data.status;
    
    if (data.paper_margin !== undefined) {
        document.getElementById('disp-funds').innerText = "₹" + parseFloat(data.paper_margin).toFixed(2);
    }

    const ans1 = document.getElementById('ans1-disp');
    ans1.innerHTML = data.ans1;
    if(data.ans1.includes("+")) ans1.className = "summary-value txt-green";
    else if(data.ans1.includes("-")) ans1.className = "summary-value txt-red";
    else ans1.className = "summary-value loading-pulse";

    const ans2 = document.getElementById('ans2-disp');
    ans2.innerText = data.ans2;
    if(data.ans2 !== "LOADING..." && data.ans2 !== "OFF") ans2.className = "summary-value txt-blue";
    else ans2.className = "summary-value loading-pulse";
    
    currentWinner = data.winner;
    
    let activePopupStock = document.getElementById('popup-name') ? document.getElementById('popup-name').innerText : "";
    let isPopupOpen = document.getElementById('modal-overlay') && !document.getElementById('modal-overlay').classList.contains('hidden');

    data.stocks.forEach(s => {
        const priceEl = document.getElementById('price-' + s.name);
        const changeEl = document.getElementById('change-' + s.name);
        const statusEl = document.getElementById('status-' + s.name); 

        if(statusEl && s.status_msg) {
            statusEl.innerText = s.status_msg;
            if (s.status_msg.includes('Error') || s.status_msg.includes('❌') || s.status_msg.includes('Skipped')) {
                statusEl.style.color = "var(--accent-red)";
            } else if (s.status_msg.includes('✅') || s.status_msg.includes('🟢') || s.status_msg.includes('Live')) {
                statusEl.style.color = "var(--accent-green)";
            } else if (s.status_msg.includes('🤖') || s.status_msg.includes('⚙️')) {
                statusEl.style.color = "var(--accent-purple)";
            } else {
                statusEl.style.color = "var(--text-muted)";
            }
        }
        
        if(priceEl) {
            if(s.price === "WAIT..." || s.price === "0.00" || s.price === "ERR") {
                 priceEl.innerText = s.price;
                 priceEl.className = "st-wait loading-pulse";
                 if(changeEl) changeEl.innerText = "";
            } else {
                priceEl.innerText = "₹" + s.price;
                priceEl.className = "st-price";
                
                if(changeEl) {
                    changeEl.innerText = s.change;
                    if(s.change.includes("+")) changeEl.className = "st-change txt-green";
                    else if(s.change.includes("-")) changeEl.className = "st-change txt-red";
                    else changeEl.className = "st-change st-wait";
                }

                if (isPopupOpen && s.name === activePopupStock) {
                    let slEl = document.getElementById('tb-stoploss');
                    let entryEl = document.getElementById('tb-entry');
                    
                    if (slEl && entryEl && !slEl.innerHTML.includes('HIT')) {
                        let slVal = parseFloat(slEl.innerText);
                        let entryVal = parseFloat(entryEl.innerText);
                        let ltpVal = parseFloat(s.price);

                        if (!isNaN(slVal) && !isNaN(entryVal) && !isNaN(ltpVal)) {
                            if (entryVal > slVal && ltpVal <= slVal) {
                                slEl.innerHTML = "<span class='txt-red' style='font-weight:900;'>SL HIT</span>";
                                if(stockDataCache[s.name]) stockDataCache[s.name].table[1] = slEl.innerHTML;
                            }
                            else if (entryVal < slVal && ltpVal >= slVal) {
                                slEl.innerHTML = "<span class='txt-red' style='font-weight:900;'>SL HIT</span>";
                                if(stockDataCache[s.name]) stockDataCache[s.name].table[1] = slEl.innerHTML;
                            }
                        }
                    }
                }
            }
        }
    });

    applyFilter(); 

    // ✅ NEW: RENDER DYNAMIC SMC LIST
    const smcListContainer = document.getElementById('smc-dynamic-list');
    if (smcListContainer && data.smc_matches) {
        if (Object.keys(data.smc_matches).length > 0) {
            let html = '';
            for (const [sym, info] of Object.entries(data.smc_matches)) {
                let badgeClass = info.signal.includes('BUY') ? 'signal-buy' : (info.signal.includes('SELL') ? 'signal-sell' : 'signal-hold');
                html += `
                <div style="border: 1px solid var(--accent-purple); padding: 15px; border-radius: 12px; margin-bottom: 15px; background: rgba(150, 46, 255, 0.05); box-shadow: 0 0 10px rgba(150, 46, 255, 0.1);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <span style="font-size:18px; font-weight:800; color:var(--text-main); text-shadow: 0 0 5px var(--accent-blue);">${sym}</span>
                        <span class="signal-badge ${badgeClass}" style="display:inline-block;">${info.signal}</span>
                    </div>
                    <div style="font-size:10px; font-weight:700; color:var(--text-muted); margin-bottom:10px; text-transform:uppercase;">AI Detected at: ${info.time}</div>
                    
                    <div class="gemini-card" style="margin-bottom:10px; border-color:rgba(0, 255, 255, 0.4);">
                        <div class="gemini-title"><span class="material-icons-outlined" style="font-size: 18px;">analytics</span>SMC ANALYSIS</div>
                        <div class="gemini-text">${info.ai_smc_data}</div>
                    </div>
                    <div class="gemini-card" style="border-color:rgba(0, 255, 255, 0.4);">
                        <div class="gemini-title"><span class="material-icons-outlined" style="font-size: 18px;">radar</span>OPERATOR TRAP ANALYSIS</div>
                        <div class="gemini-text">${info.ai_trap_data}</div>
                    </div>
                </div>`;
            }
            smcListContainer.innerHTML = html;
        } else {
            smcListContainer.innerHTML = `<div class="empty-state">Waiting for High Probability setups...<br><br><span class="loading-pulse" style="color:var(--accent-purple);">⚙️ Live AI Scanning Active...</span></div>`;
        }
    }

    // ✅ INITIAL LOAD FOR TERMINAL LOGS
    const logContainer = document.getElementById('terminal-logs');
    if (logContainer && data.logs && logContainer.children.length === 0) {
        data.logs.forEach(log => {
            let div = document.createElement('div');
            div.style.marginBottom = '6px';
            div.style.lineHeight = '1.4';
            div.style.borderBottom = '1px solid rgba(0, 255, 255, 0.1)';
            div.style.paddingBottom = '4px';
            
            if(log.includes('❌') || log.includes('SKIPPED') || log.includes('Error') || log.includes('Failed') || log.includes('🛑') || log.includes('⚠️')) {
                div.style.color = 'var(--accent-red)';
            } else if(log.includes('✅') || log.includes('PASSED') || log.includes('BUY') || log.includes('SELL') || log.includes('🟢') || log.includes('🏆') || log.includes('📝')) {
                div.style.color = 'var(--accent-green)';
            } else if(log.includes('⚙️') || log.includes('🤖') || log.includes('✨') || log.includes('🚀')) {
                div.style.color = 'var(--accent-purple)';
            } else {
                div.style.color = 'var(--accent-blue)';
            }
            div.innerText = log;
            logContainer.appendChild(div);
        });
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    const posContainer = document.getElementById('positions-list');
    if (data.paper_positions && Object.keys(data.paper_positions).length > 0) {
        let posHTML = '';
        for (const [sym, p] of Object.entries(data.paper_positions)) {
            let m2mClass = p.m2m >= 0 ? "m2m-green" : "m2m-red";
            let m2mSign = p.m2m >= 0 ? "+" : "";
            let typeClass = p.type === "BUY" ? "type-buy" : "type-sell";
            
            posHTML += `
            <div class="trade-card">
                <div class="trade-header">
                    <span class="trade-title">${sym}</span>
                    <span class="trade-type ${typeClass}">${p.type}</span>
                </div>
                <div class="trade-details">
                    <div class="trade-col">
                        <span class="trade-label">Qty</span>
                        <span class="trade-val">${p.qty}</span>
                    </div>
                    <div class="trade-col">
                        <span class="trade-label">Avg Price</span>
                        <span class="trade-val">₹${p.avg_price.toFixed(2)}</span>
                    </div>
                    <div class="trade-col trade-col-right">
                        <span class="trade-label">M2M</span>
                        <span class="m2m-val ${m2mClass}">${m2mSign}₹${p.m2m.toFixed(2)}</span>
                    </div>
                </div>
            </div>`;
        }
        if(posContainer) posContainer.innerHTML = posHTML;
    } else {
        if(posContainer) posContainer.innerHTML = `<div class="empty-state">Yet to trade today?<br>No open positions found.</div>`;
    }

    const ordContainer = document.getElementById('orders-list');
    if (data.paper_orders && data.paper_orders.length > 0) {
        let filteredOrders = data.paper_orders.filter(o => 
            (currentOrderTab === 'EXECUTED' && o.status === 'Executed') || 
            (currentOrderTab === 'PENDING' && o.status !== 'Executed')
        );
        
        if(filteredOrders.length > 0) {
            let ordHTML = '';
            filteredOrders.reverse().forEach(o => {
                let typeClass = o.type === "BUY" ? "type-buy" : "type-sell";
                ordHTML += `
                <div class="trade-card">
                    <div class="trade-header">
                        <span class="trade-title">${o.symbol}</span>
                        <span style="font-size:10px; color:var(--text-muted);">${o.time}</span>
                    </div>
                    <div class="trade-details">
                        <div class="trade-col">
                            <span class="trade-type ${typeClass}">${o.type}</span>
                        </div>
                        <div class="trade-col">
                            <span class="trade-label">Qty</span>
                            <span class="trade-val">${o.qty}</span>
                        </div>
                        <div class="trade-col">
                            <span class="trade-label">Price</span>
                            <span class="trade-val">₹${o.price.toFixed(2)}</span>
                        </div>
                        <div class="trade-col trade-col-right">
                            <span class="trade-label">Status</span>
                            <span class="trade-val" style="color:var(--accent-blue);">${o.status}</span>
                        </div>
                    </div>
                </div>`;
            });
            if(ordContainer) ordContainer.innerHTML = ordHTML;
        } else {
            if(ordContainer) ordContainer.innerHTML = `<div class="empty-state">No ${currentOrderTab.toLowerCase()} orders found.</div>`;
        }
    } else {
        if(ordContainer) ordContainer.innerHTML = `<div class="empty-state">Yet to trade today?<br>No orders placed.</div>`;
    }
}

function toggleSystem() {
    const isChecked = document.getElementById('systemToggle').checked;
    socket.emit('toggle_engine', {state: isChecked});
}

function executeTrade(type) {
    let stock = document.getElementById('popup-name').innerText;
    let sl = document.getElementById('tb-stoploss').innerText;
    let tgt = document.getElementById('tb-target').innerText;
    
    if (sl === '-' || tgt === '-' || sl === '' || tgt === '') {
        alert("⚠️ AI सिग्नलची वाट पहा. Target आणि Stoploss अजून मिळालेले नाहीत.");
    } else {
        alert(`✅ ${type} Order Initiated for ${stock}!\\n🎯 Target: ${tgt}\\n🛑 Stoploss: ${sl}`);
    }
}
</script>
</head>
<body>

<div class="header">
    <div class="brand">AI TRADER PRO</div>
    <div class="header-controls">
        <label class="switch">
            <input type="checkbox" id="systemToggle" checked onchange="toggleSystem()">
            <span class="slider round"></span>
        </label>
        <div class="status-badge"><div class="status-dot"></div><span id="status-text">CONNECTING...</span></div>
    </div>
</div>

<div id="marathi-status" class="status-bar-marathi">कनेक्ट करत आहे...</div>

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
    <div id="btn-SMC" class="filter-btn" onclick="filterStocks('SMC')">SMC STRATEGY</div>
</div>

<div id="page-home" class="page-section active">
    
    <div id="normal-stock-list" style="display:flex; flex-direction:column; gap:12px; width:100%;">
        {% for s in stocks %}
        <div class="stock-card" id="card-{{ s.name }}" data-cat="{{ s.cat }}" onclick="openCardPopup('{{ s.name }}')">
            <div class="st-info">
                <span class="st-name">{{ s.name }}</span>
                <span class="st-cat-tag">{{ s.cat }} SECTOR</span>
                <span id="status-{{ s.name }}" style="font-size:10px; font-weight:700; color:var(--text-muted); margin-top:5px; transition:all 0.3s; display:block;">{{ s.status_msg }}</span>
            </div>
            <div class="st-price-box">
                <div class="st-wait" id="price-{{ s.name }}">{{ s.price }}</div>
                <div class="st-change" id="change-{{ s.name }}">{{ s.change }}</div>
            </div>
        </div>
        {% endfor %}
    </div>

    <div id="smc-container" class="smc-container">
        <div style="font-size:14px; font-weight:800; color:var(--accent-purple); margin-bottom:10px; text-transform:uppercase; text-align:center; text-shadow: 0 0 5px var(--accent-purple);">
            🟢 LIVE SMC SCANNER
        </div>
        <div id="smc-dynamic-list" style="display:flex; flex-direction:column; gap:10px;">
            <div class="empty-state">
                Waiting for High Probability setups...<br><br>
                <span class="loading-pulse" style="color:var(--accent-purple);">⚙️ Live AI Scanning Active...</span>
            </div>
        </div>
    </div>

</div>

<div id="page-positions" class="page-section">
    <div style="font-size:18px; font-weight:800; margin-bottom:10px; color:var(--accent-blue);">LIVE POSITIONS</div>
    <div id="positions-list" style="display:flex; flex-direction:column; gap:12px;">
        <div class="empty-state">Loading...</div>
    </div>
</div>

<div id="page-orders" class="page-section">
    <div class="orders-tab-bar">
        <div id="tab-exec" class="order-tab active" onclick="switchOrderTab('EXECUTED')">EXECUTED</div>
        <div id="tab-pend" class="order-tab" onclick="switchOrderTab('PENDING')">PENDING</div>
    </div>
    <div id="orders-list" style="display:flex; flex-direction:column; gap:12px;">
        <div class="empty-state">Loading...</div>
    </div>
</div>

<div id="page-process" class="page-section">
    <div style="font-size:18px; font-weight:800; margin-bottom:10px; color:var(--accent-purple); display:flex; align-items:center; gap:8px;">
        <span class="material-icons-outlined">terminal</span> SYSTEM TERMINAL
    </div>
    <div style="background: rgba(0, 0, 0, 0.85); border: 1px solid var(--accent-purple); border-radius: 12px; padding: 15px; height: 100%; min-height: 400px; flex: 1; overflow-y: auto; box-shadow: inset 0 0 15px rgba(0, 0, 0, 1), 0 0 10px rgba(150, 46, 255, 0.2); font-family: monospace; font-size: 12px; letter-spacing: 0.5px;" id="terminal-logs">
        <div style="color: var(--text-muted); margin-bottom:6px; border-bottom: 1px solid rgba(0, 255, 255, 0.1); padding-bottom: 4px;">
            [09:00:00] ⏳ System initialized. Waiting for background processes...
        </div>
    </div>
</div>

<div id="page-menu" class="page-section">
    <div class="menu-profile">
        <div class="menu-greeting">Hello Pradeep 🚀</div>
        <div class="menu-funds-label">Available to trade</div>
        <div class="menu-funds-val" id="disp-funds">₹10000.00</div>
    </div>
    
    <div class="menu-list">
        <div class="menu-item">
            <span class="material-icons-outlined">qr_code_scanner</span>
            Web Login
        </div>
        <div class="menu-item">
            <span class="material-icons-outlined">group_add</span>
            Refer & Earn
        </div>
        <div class="menu-item">
            <span class="material-icons-outlined">account_balance_wallet</span>
            Fund Summary
        </div>
        <div class="menu-item">
            <span class="material-icons-outlined">settings</span>
            Settings
        </div>
        <div class="menu-item" style="color:var(--accent-red); border-color:rgba(255,49,49,0.3);">
            <span class="material-icons-outlined" style="color:var(--accent-red);">logout</span>
            Logout
        </div>
    </div>
</div>

<div id="modal-overlay" class="modal-overlay hidden">
    <div class="modal-box">
        <span class="material-icons-outlined modal-close-icon" onclick="closePopup()">close</span>

        <div class="modal-title">
            <span id="popup-name">STOCK NAME</span>
            <span id="ai-signal-badge" class="signal-badge"></span>
        </div>
        
        <div style="background: rgba(0,0,0,0.5); border: 1px solid var(--accent-blue); border-radius: 10px; padding: 15px; margin-top: 10px; min-height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div id="status-container" style="display: none; text-align: center;">
                <div class="loading-pulse" style="color: var(--accent-blue); font-size: 24px; margin-bottom: 10px;">⚙️</div>
                <div id="process-msg" style="color: var(--text-muted); font-size: 12px; font-weight: 700;">Initializing...</div>
            </div>
            
            <div class="chart-label">15 MINUTE CHART</div>
            <img id="img-15m" style="width: 100%; border-radius: 10px; display: none; border: 1px solid var(--border);" onerror="this.style.display='none'">
            
            <div class="chart-label">1 HOUR CHART</div>
            <img id="img-1h" style="width: 100%; border-radius: 10px; display: none; border: 1px solid var(--border);" onerror="this.style.display='none'">
        </div>

        <button id="btn-chart-read" style="width: 100%; background: var(--bg-card); border: 1px solid var(--accent-blue); color: var(--accent-blue); padding: 10px; border-radius: 10px; font-weight: 700; margin-top: 15px; cursor: pointer; transition: all 0.3s;">
            🔄 RE-ANALYZE
        </button>

        <select id="ai-selector" class="ai-selector" onchange="changeAITab()">
            <option value="tab1">1. AI INSIGHTS</option>
            <option value="tab2">2. LIQUIDITY & TRAP</option>
            <option value="tab3">3. FINAL ACTION</option>
        </select>

        <div id="gemini-card" class="gemini-card">
            <div class="gemini-title">
                <span class="material-icons-outlined" style="font-size: 18px;">auto_awesome</span>
                GEMINI AI INSIGHTS
            </div>
            <div id="gemini-text" class="gemini-text">Waiting for analysis...</div>
        </div>

        <div id="gemini-card-2" class="gemini-card">
            <div class="gemini-title">
                <span class="material-icons-outlined" style="font-size: 18px;">radar</span>
                LIQUIDITY & TRAP ANALYSIS
            </div>
            <div id="gemini-text-2" class="gemini-text">Waiting for analysis...</div>
        </div>
        
        <div id="gemini-card-3" class="gemini-card">
            <div class="gemini-title">
                <span class="material-icons-outlined" style="font-size: 18px;">track_changes</span>
                FINAL ACTION
            </div>
            <div id="gemini-text-3" class="gemini-text">Waiting for analysis...</div>
        </div>

        <div id="action-table-container" class="action-table-container">
            <table class="action-table">
                <tr><th>Entry</th><th>Stoploss</th><th>Target</th><th>R:R</th></tr>
                <tr>
                    <td id="tb-entry">-</td><td id="tb-stoploss">-</td>
                    <td id="tb-target">-</td><td id="tb-rr">-</td>
                </tr>
            </table>
        </div>

        <div style="display: flex; gap: 10px; margin-top: 15px;">
            <button onclick="executeTrade('BUY')" style="flex: 1; background: rgba(57, 255, 20, 0.1); border: 1px solid var(--accent-green); color: var(--accent-green); padding: 12px; border-radius: 10px; font-weight: 800; cursor: pointer; box-shadow: 0 0 10px rgba(57, 255, 20, 0.2); transition: all 0.3s; font-family: 'Inter', sans-serif; letter-spacing: 1px;">BUY</button>
            <button onclick="executeTrade('SELL')" style="flex: 1; background: rgba(255, 49, 49, 0.1); border: 1px solid var(--accent-red); color: var(--accent-red); padding: 12px; border-radius: 10px; font-weight: 800; cursor: pointer; box-shadow: 0 0 10px rgba(255, 49, 49, 0.2); transition: all 0.3s; font-family: 'Inter', sans-serif; letter-spacing: 1px;">SELL</button>
        </div>
    </div>
</div>

<div class="bottom-nav">
    <div class="nav-item active" onclick="switchPage('home', this)">
        <span class="material-icons-outlined nav-icon">home</span>
        <span class="nav-label">Home</span>
    </div>
    <div class="nav-item" onclick="switchPage('positions', this)">
        <span class="material-icons-outlined nav-icon">pie_chart</span>
        <span class="nav-label">Positions</span>
    </div>
    <div class="nav-item" onclick="switchPage('orders', this)">
        <span class="material-icons-outlined nav-icon">receipt_long</span>
        <span class="nav-label">Orders</span>
    </div>
    
    <div class="nav-item" onclick="switchPage('process', this)">
        <span class="material-icons-outlined nav-icon">terminal</span>
        <span class="nav-label">Process</span>
    </div>
    
    <div class="nav-item" onclick="switchPage('menu', this)">
        <span class="material-icons-outlined nav-icon">menu</span>
        <span class="nav-label">Menu</span>
    </div>
</div>

</body>
</html>
'''

# 👇 THREADS & SERVER START 👇
t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

c = threading.Thread(target=background_ai_cron_job)
c.daemon = True
c.start()

if __name__ == '__main__':
    # ✅ FIX: Default port set to 7860 for Hugging Face Spaces
    port = int(os.environ.get("PORT", 7860))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
