import os
import pyotp
import time
import threading
import requests
import json
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
# --- CHANGED FOR SHOONYA API ---
from NorenRestApiPy.NorenApi import NorenApi
from flask_socketio import SocketIO, emit

# --- NEW IMPORTS FOR REAL GEMINI AI ---
import google.generativeai as genai
from PIL import Image
import io

# --- NEW IMPORTS FOR TECHNICAL INDICATORS & FAST CHARTS ---
import pandas as pd
import matplotlib
matplotlib.use('Agg') # Headless optimization for server
import mplfinance as mpf

app = Flask(__name__, static_folder='static') # Configured static folder
app.config['SECRET_KEY'] = 'secret_key_for_websocket'

# --- WEBSOCKET CONFIGURATION FOR HUGGING HF ---
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- ENSURE STATIC FOLDER EXISTS ---
if not os.path.exists('static'):
    os.makedirs('static')

# --- 1. KEYS (SHOONYA KEYS + GEMINI KEY) ---
USER_ID = os.environ.get("USER_ID")
PASSWORD = os.environ.get("PASSWORD")
API_KEY = os.environ.get("API_KEY") 
VENDOR_CODE = os.environ.get("VENDOR_CODE")
TOTP_KEY = os.environ.get("TOTP_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- GLOBAL DATA STORE ---
live_feed = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
confirmed_winner = None 
data_fetched_once = False
tokens_loaded = False 
system_active = True 
api_global = None # NEW GLOBAL FOR SHOONYA API

# --- NEW: GLOBAL CACHE FOR ZERO WAIT TIME & VIX ---
# This holds the background calculated AI insights and Charts
PRECALCULATED_DATA = {}
LIVE_VIX = 15.0

# ==========================================
# --- NEW: PAPER TRADING ENGINE (₹10,000) ---
# ==========================================
# हा तो paper_engine.py चा भाग आहे जो बॅकग्राऊंडला काम करेल
paper_margin = 10000.0
paper_positions = {} # चालू ट्रेड इथे दिसतील
paper_orders = []    # पेंडिंग आणि एक्झिक्युटेड ऑर्डर्स इथे दिसतील

def execute_paper_order(symbol, signal, price):
    global paper_margin, paper_positions, paper_orders
    if signal not in ["BUY", "SELL"]: return
    
    qty = 10 # डिफॉल्ट क्वांटिटी (AI च्या Risk/Reward नुसार नंतर बदलता येईल)
    cost = qty * price
    
    order_time = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    time_str = order_time.strftime("%I:%M %p")

    if signal == "BUY" and paper_margin >= cost:
        paper_margin -= cost
        paper_orders.append({'symbol': symbol, 'type': 'BUY', 'price': price, 'qty': qty, 'status': 'Executed', 'time': time_str})
        if symbol in paper_positions:
            paper_positions[symbol]['qty'] += qty
        else:
            paper_positions[symbol] = {'qty': qty, 'avg_price': price, 'type': 'BUY', 'm2m': 0.0}
            
    elif signal == "SELL":
        paper_orders.append({'symbol': symbol, 'type': 'SELL', 'price': price, 'qty': qty, 'status': 'Executed', 'time': time_str})
        if symbol in paper_positions:
            paper_positions[symbol]['qty'] -= qty
            if paper_positions[symbol]['qty'] <= 0:
                del paper_positions[symbol]
        else:
            paper_positions[symbol] = {'qty': qty, 'avg_price': price, 'type': 'SELL', 'm2m': 0.0}

# --- 1.1 MARKET HOLIDAYS LIST (2026) ---
NSE_HOLIDAYS = [
    "2026-01-26", "2026-02-17", "2026-03-04", "2026-03-20", 
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15", 
    "2026-09-15", "2026-10-02", "2026-10-20", "2026-11-08", "2026-12-25"
]

# --- MEMORY SYSTEM (JSON FILE HANDLING) ---
STATE_FILE = "trade_state.json"

def save_state(winner, sector_code):
    try:
        data = {
            "date": str(datetime.now(timezone.utc).date()),
            "winner": winner,
            "code": sector_code
        }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
        print(f"💾 MEMORY SAVED: {winner}")
    except Exception as e:
        print(f"Save Error: {e}")

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
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

# --- SHOONYA SPECIFIC INDICES TOKENS ---
INDICES_LIST = [
    {"name": "NIFTY",      "token": "26000", "symbol": "Nifty 50"},
    {"name": "BANKNIFTY",  "token": "26009", "symbol": "Nifty Bank"},
    {"name": "NIFTY_IT",   "token": "26004", "symbol": "Nifty IT"},
    {"name": "NIFTY_AUTO", "token": "26002", "symbol": "Nifty Auto"}
]

STOCKS = [{"name": s["name"], "cat": s["cat"], "token": None, "price": "WAIT...", "change": "WAIT...", "symbol": s["name"]} for s in TARGET_STOCKS]
# --- 3. AUTO-SCANNER (GET TOKENS FOR SHOONYA) ---
def fetch_correct_tokens(api):
    global STOCKS
    print("\n>>> STARTING NSE TOKEN SCANNER (SHOONYA SAFE MODE) <<<")
    temp_stocks = []
    
    for item in TARGET_STOCKS:
        name = item["name"]
        cat = item["cat"]
        time.sleep(0.5) 
        try:
            # ✅ Shoonya API Search
            search_response = api.searchscrip(exchange="NSE", searchtext=name)
            
            found_token = None
            found_symbol = None
            
            if search_response and search_response.get('stat') == 'Ok' and 'values' in search_response:
                scrip_list = search_response['values']
                for s in scrip_list:
                    if s['tsym'] == name + "-EQ":
                        found_token = s['token']; found_symbol = s['tsym']
                        break
                if not found_token:
                    for s in scrip_list:
                        if s['tsym'] == name + "-BE":
                            found_token = s['token']; found_symbol = s['tsym']
                            break
                            
            if found_token:
                print(f"✅ FOUND: {name} -> {found_token}")
                temp_stocks.append({"name": name, "token": found_token, "symbol": found_symbol, "cat": cat, "price": "0.00", "change": "0.00"})
            else:
                print(f"❌ FAILED: {name}")
                temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR", "change": "ERR"})
        except Exception as e:
            print(f"Fetch Token Error for {name}: {e}")
            temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR", "change": "ERR"})
    
    STOCKS = temp_stocks
    print(">>> SCAN COMPLETE <<<\n")

# --- 4. SHOONYA WEBSOCKET HANDLERS ---
def event_handler_feed(message):
    global live_feed
    try:
        if type(message) == dict and 'tk' in message:
            token = message['tk']
            
            ltp = None
            if 'lp' in message: ltp = float(message['lp'])
            
            open_price = None
            if 'o' in message: open_price = float(message['o'])
            
            low_price = None
            if 'l' in message: low_price = float(message['l'])
                
            volume = None
            if 'v' in message: volume = int(message['v'])
                
            best_bid = None
            if 'bp1' in message: best_bid = float(message['bp1'])
                
            best_ask = None
            if 'sp1' in message: best_ask = float(message['sp1'])
            
            if token not in live_feed: live_feed[token] = {}
            if ltp is not None: live_feed[token]['ltp'] = ltp
            if open_price is not None: live_feed[token]['open'] = open_price
            if low_price is not None: live_feed[token]['low'] = low_price
            if volume is not None: live_feed[token]['volume'] = volume
            if best_bid is not None: live_feed[token]['best_bid'] = best_bid
            if best_ask is not None: live_feed[token]['best_ask'] = best_ask
            
    except Exception as e:
        pass # Silent error handle to avoid spamming console

def open_callback():
    global api_global
    print("✅ Shoonya WebSocket Connected! Subscribing to tokens...")
    
    try:
        for ind in INDICES_LIST:
            if ind.get("token"):
                api_global.subscribe(f"NSE|{ind['token']}")
        
        for s in STOCKS:
            if s.get("token"):
                api_global.subscribe(f"NSE|{s['token']}")
                
        print("✅ Subscribed to Markets (Shoonya)")
        socketio.emit('process_log', {'msg': "Markets Connected"})
    except Exception as e:
        print(f"❌ Subscription Error: {e}")
        socketio.emit('process_log', {'msg': f"Subscription Error: {str(e)}"})

def socket_error(err):
    print(f"❌ WebSocket ERROR: {err}")
    socketio.emit('process_log', {'msg': f"Error: {str(err)}"})

def socket_close():
    print(f"⚠️ WebSocket CLOSED.")
    socketio.emit('process_log', {'msg': "WebSocket Closed. Reconnecting..."})
# --- 5. MAIN LOGIC ENGINE (SHOONYA V2) ---
def fetch_offline_prices():
    """✅ NEW: Fallback function using Shoonya Historical API"""
    global STOCKS, live_feed, api_global
    print("🌐 Fetching Offline Prices via Shoonya (Weekend Mode)...")
    for s in STOCKS:
        try:
            if api_global and s.get("token"):
                # Shoonya uses epoch timestamps for historical data
                end_time = datetime.now().timestamp()
                start_time = (datetime.now() - timedelta(days=5)).timestamp()
                
                res = api_global.get_time_price_series(exchange='NSE', token=s["token"], starttime=start_time, endtime=end_time, interval='15')
                
                if res and isinstance(res, list) and len(res) > 0:
                    last_candle = res[0] # Shoonya returns newest first usually
                    
                    open_price = float(last_candle['into'])
                    low_price = float(last_candle['intl']) 
                    last_price = float(last_candle['intc'])
                    
                    s["price"] = f"{last_price:.2f}"
                    s["change"] = f"{(last_price - open_price):+.2f}"
                    
                    live_feed[s["token"]] = {
                        'ltp': last_price, 
                        'open': open_price,
                        'low': low_price, 
                        'volume': int(last_candle.get('v', 0)),
                        'best_bid': 0.0,
                        'best_ask': 0.0
                    }
        except Exception as e:
            print(f"⚠️ Offline fetch error for {s['name']}: {e}")

def start_engine():
    global live_feed, market_status, ans1_nifty, ans2_sector, winning_sector_code, tokens_loaded, system_active, confirmed_winner, api_global
    
    confirmed_winner, loaded_code = load_state()
    winning_sector_code = loaded_code
    if confirmed_winner:
        ans2_sector = "BANKING" if confirmed_winner == "BANK" else "IT / TECH" if confirmed_winner == "IT" else "AUTO"
    
    while True:
        try:
            if not system_active:
                market_status = "PAUSED"
                ans1_nifty = "OFF"
                ans2_sector = "OFF"
                
                if confirmed_winner is not None:
                    print("🛑 SYSTEM PAUSED: Resetting Memory...")
                    confirmed_winner = None
                    winning_sector_code = "ALL"
                    save_state(None, "ALL")

                socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS})
                time.sleep(2) 
                continue
            
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            
            t915 = datetime.strptime("09:15", "%H:%M").time()
            t_lock = datetime.strptime("09:20:00", "%H:%M:%S").time() 
            t0900 = datetime.strptime("09:00", "%H:%M").time()

            if t0900 <= current_time < t915:
                if confirmed_winner is not None:
                    save_state(None, "ALL") 
                confirmed_winner = None
                ans2_sector = "WAIT FOR 9:15"
                winning_sector_code = "ALL"

            market_status = "LIVE"

            if api_global is None:
                try:
                    totp = pyotp.TOTP(TOTP_KEY).now()
                    api = NorenApi()
                    
                    # ✅ SHOONYA LOGIN
                    ret = api.login(userid=USER_ID, password=PASSWORD, twoFA=totp, vendor_code=VENDOR_CODE, api_secret=API_KEY, imei="abc12345")
                    
                    if ret and ret.get('stat') == 'Ok':
                        api_global = api # ✅ SAVE GLOBALLY FOR HISTORICAL API
                        if not tokens_loaded:
                            fetch_correct_tokens(api_global)
                            valid_token_count = sum(1 for s in STOCKS if s.get("token"))
                            if valid_token_count > 0:
                                tokens_loaded = True
                                
                                print("📥 Fetching Initial Prices (Shoonya)...")
                                try:
                                    for ind in INDICES_LIST:
                                        if ind.get("token"):
                                            try:
                                                res = api_global.get_quotes(exchange="NSE", token=ind["token"])
                                                if res and res.get('stat') == 'Ok':
                                                    price = float(res.get('lp', 0))
                                                    opn_p = float(res.get('o', price))
                                                    low_p = float(res.get('l', price))
                                                    live_feed[ind["token"]] = {
                                                        'ltp': price, 
                                                        'open': opn_p,
                                                        'low': low_p,
                                                        'volume': 0,
                                                        'best_bid': 0.0,
                                                        'best_ask': 0.0
                                                    }
                                            except Exception as e:
                                                pass

                                    for s in STOCKS:
                                        if s.get("token"):
                                            res = api_global.get_quotes(exchange="NSE", token=s["token"])
                                            if res and res.get('stat') == 'Ok':
                                                price = float(res.get('lp', 0))
                                                s["price"] = f"{price:.2f}"
                                                
                                                opn_p = float(res.get('o', price))
                                                low_p = float(res.get('l', price)) 
                                                s["change"] = f"{(price - opn_p):+.2f}"

                                                live_feed[s["token"]] = {
                                                    'ltp': price, 
                                                    'open': opn_p,
                                                    'low': low_p, 
                                                    'volume': int(res.get('v', 0)),
                                                    'best_bid': float(res.get('bp1', 0)),
                                                    'best_ask': float(res.get('sp1', 0))
                                                }
                                    print("✅ Initial Prices Loaded!")
                                except Exception as fetch_err:
                                    print(f"⚠️ Initial Fetch Error: {fetch_err}")

                            # ✅ START SHOONYA WEBSOCKET
                            api_global.start_websocket(subscribe_callback=event_handler_feed, socket_open_callback=open_callback, socket_error_callback=socket_error, socket_close_callback=socket_close)
                            print("🚀 Engine & WebSocket Started (Shoonya)...")

                        else:
                            socketio.emit('process_log', {'msg': f"Shoonya Offline - Loading Backup Data"})
                            if not tokens_loaded:
                                fetch_offline_prices()
                                tokens_loaded = True
                            time.sleep(30) 
                            continue
                except Exception as e:
                    print(f"Login Error (Shoonya down?): {e}")
                    socketio.emit('process_log', {'msg': f"Shoonya Offline - Loading Backup Data"})
                    if not tokens_loaded:
                        fetch_offline_prices()
                        tokens_loaded = True
                    time.sleep(30) 
                    continue

            bank_pct = -100.0; it_pct = -100.0; auto_pct = -100.0
            
            for ind in INDICES_LIST:
                tk = ind["token"]
                if tk in live_feed:
                    ltp = live_feed[tk].get('ltp', 0)
                    opn = live_feed[tk].get('open', 0)
                    
                    if opn > 0 and ltp > 0:
                        pct = ((ltp - opn) / opn) * 100
                        diff = ltp - opn
                        
                        if ind["name"] == "NIFTY":
                            ans1_nifty = f"{diff:+.2f}"
                        elif ind["name"] == "BANKNIFTY": bank_pct = pct
                        elif ind["name"] == "NIFTY_IT": it_pct = pct
                        elif ind["name"] == "NIFTY_AUTO": auto_pct = pct

            if current_time >= t_lock:
                sector_pcts = {"BANK": bank_pct, "IT": it_pct, "AUTO": auto_pct}
                max_sector = max(sector_pcts, key=sector_pcts.get)
                max_val = sector_pcts[max_sector]

                if confirmed_winner is None:
                    if max_val > 0: 
                        confirmed_winner = max_sector
                        winning_sector_code = confirmed_winner
                        ans2_sector = "BANKING" if confirmed_winner == "BANK" else "IT / TECH" if confirmed_winner == "IT" else "AUTO"
                        save_state(confirmed_winner, winning_sector_code)
                else:
                    current_winner_val = sector_pcts.get(confirmed_winner, -100.0)
                    
                    if max_sector != confirmed_winner and (max_val - current_winner_val) > 0.1:
                        confirmed_winner = max_sector
                        winning_sector_code = confirmed_winner
                        ans2_sector = "BANKING" if confirmed_winner == "BANK" else "IT / TECH" if confirmed_winner == "IT" else "AUTO"
                        print(f"🔄 MARKET SHIFT: Sector Switched to {confirmed_winner}")
                        save_state(confirmed_winner, winning_sector_code)
                        
                    if current_winner_val < 0.0:
                        confirmed_winner = None
                        winning_sector_code = "ALL"
                        ans2_sector = "RE-SCANNING..."
                        save_state(None, "ALL")
            else:
                ans2_sector = "CALCULATING..."

            for s in STOCKS:
                tk = s.get("token")
                if tk and tk in live_feed:
                    ltp = live_feed[tk].get("ltp", 0)
                    opn = live_feed[tk].get("open", 0)
                    if ltp > 0:
                        s["price"] = f"{ltp:.2f}"
                        if opn > 0:
                            change_val = ltp - opn
                            s["change"] = f"{change_val:+.2f}"
                        else:
                            s["change"] = "WAIT..."
                    else:
                        s["price"] = "WAIT..."

            # We also update paper_positions M2M here
            for p_sym, p_data in paper_positions.items():
                p_tk = next((x["token"] for x in STOCKS if x["name"] == p_sym), None)
                if p_tk and p_tk in live_feed:
                    p_ltp = live_feed[p_tk].get("ltp", 0)
                    if p_ltp > 0:
                        if p_data['type'] == 'BUY':
                            p_data['m2m'] = (p_ltp - p_data['avg_price']) * p_data['qty']
                        else:
                            p_data['m2m'] = (p_data['avg_price'] - p_ltp) * p_data['qty']

            socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS, "paper_margin": paper_margin, "paper_positions": paper_positions})
            time.sleep(1) 

        except Exception as e:
            print(f"Engine Exception Error: {e}")
            time.sleep(5) 
# --- NEW: FAST PYTHON CHARTS (REPLACES ANGEL ONE WITH SHOONYA API) ---
def generate_mpl_chart(symbol, interval, filename):
    """
    Generates ultra-fast candlestick charts using Shoonya Historical API.
    Zero dependency on yfinance, 100% accurate NSE data.
    """
    global api_global, STOCKS
    
    if not api_global:
        return False
        
    # स्टॉकचे टोकन शोधा
    token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token:
        return False

    try:
        inter_map = {"15m": "15", "1h": "60"}
        days_back = 5 if interval == "15m" else 30
        
        # Shoonya Historical Data Request (Uses Timestamp)
        end_time = datetime.now().timestamp()
        start_time = (datetime.now() - timedelta(days=days_back)).timestamp()
        
        res = api_global.get_time_price_series(exchange='NSE', token=token, starttime=start_time, endtime=end_time, interval=inter_map.get(interval, "15"))
        
        if not res or not isinstance(res, list) or len(res) == 0:
            return False
            
        # डेटा फॉरमॅट करणे (Shoonya returns newest first, so we reverse it)
        df = pd.DataFrame(res)
        df = df.iloc[::-1].reset_index(drop=True)
        
        df['Date'] = pd.to_datetime(df['time'], format='%d-%m-%Y %H:%M:%S')
        df.set_index('Date', inplace=True)
        
        # Rename columns to match mplfinance format
        df.rename(columns={'into': 'Open', 'inth': 'High', 'intl': 'Low', 'intc': 'Close', 'v': 'Volume'}, inplace=True)
        
        # डेटा क्लीनिंग
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna()
        
        if df.empty or len(df) == 0: 
            return False
        
        filepath = os.path.join('static', filename)
        
        # --- DARK CYBERPUNK THEME ---
        mc = mpf.make_marketcolors(up='#39FF14', down='#FF3131', edge='inherit', wick='inherit', volume='in', ohlc='i')
        s  = mpf.make_mpf_style(marketcolors=mc, facecolor='#000508', edgecolor='#00ffff', figcolor='#000508', gridcolor='#003333', gridstyle='--')
        
        mpf.plot(df, type='candle', style=s, volume=False, savefig=dict(fname=filepath, dpi=100, bbox_inches='tight', pad_inches=0.1), figsize=(6, 3))
        return True
    except Exception as e:
        print(f"⚠️ MPL Chart Error {symbol}: {e}")
        return False

# --- FUNCTION: CALCULATE TECHNICAL INDICATORS (WITH SHOONYA API) ---
def get_technical_indicators(symbol):
    global api_global, STOCKS
    
    if not api_global:
        return "N/A", "N/A", "N/A", 0
        
    token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token:
        return "N/A", "N/A", "N/A", 0

    try:
        # 1. Fetch 15m data for RSI, SMA, MACD
        end_time = datetime.now().timestamp()
        start_time_15m = (datetime.now() - timedelta(days=3)).timestamp()
        res = api_global.get_time_price_series(exchange='NSE', token=token, starttime=start_time_15m, endtime=end_time, interval='15')
        
        # 2. Fetch Daily data for 10-Day Volume Average (Rule 3)
        start_time_daily = (datetime.now() - timedelta(days=15)).timestamp()
        res_daily = api_global.get_time_price_series(exchange='NSE', token=token, starttime=start_time_daily, endtime=end_time, interval='1440')
        
        avg_vol_10d = 0
        if res_daily and isinstance(res_daily, list):
            df_daily = pd.DataFrame(res_daily)
            df_daily['v'] = pd.to_numeric(df_daily.get('v', 0), errors='coerce')
            avg_vol_10d = df_daily['v'].head(10).mean() # head() because Shoonya is newest first
            if pd.isna(avg_vol_10d): avg_vol_10d = 0
        
        if not res or not isinstance(res, list):
            return "N/A", "N/A", "N/A", avg_vol_10d
            
        df = pd.DataFrame(res)
        df = df.iloc[::-1].reset_index(drop=True)
        df['Close'] = pd.to_numeric(df.get('intc', 0), errors='coerce')
        df = df.dropna(subset=['Close'])
        
        if df.empty: 
            return "N/A", "N/A", "N/A", avg_vol_10d
            
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
        
        rsi_val = f"{float(latest['RSI_14']):.2f}" if not pd.isna(latest['RSI_14']) else "N/A"
        sma_val = f"{float(latest['SMA_20']):.2f}" if not pd.isna(latest['SMA_20']) else "N/A"
        macd_val = f"{float(latest['MACD']):.2f}" if not pd.isna(latest['MACD']) else "N/A"
        
        return rsi_val, sma_val, macd_val, avg_vol_10d
    except Exception as e:
        return "Error", "Error", "Error", 0

# --- 6. BACKGROUND CRON JOB (ZERO WAIT TIME - BULLETPROOF) ---
def background_ai_cron_job():
    """
    Runs continuously in the background.
    Calculates charts and AI insights for all stocks so they are ready BEFORE the user clicks.
    NOW UPGRADED WITH SMART MONEY (INSTITUTIONAL) HARD FILTERS & PAPER TRADING EXECUTION!
    """
    global PRECALCULATED_DATA, system_active, live_feed, STOCKS, LIVE_VIX, INDICES_LIST, paper_positions
    
    print("⏳ [CRON] Background AI Task Started with Institutional Filters...")
    while True:
        if not system_active:
            time.sleep(10)
            continue
            
        # 1. Fetch NIFTY % Change for Relative Strength (RS) comparison
        nifty_pct = 0.0
        nifty_token = next((ind["token"] for ind in INDICES_LIST if ind["name"] == "NIFTY"), None)
        if nifty_token and nifty_token in live_feed:
            n_ltp = live_feed[nifty_token].get('ltp', 0)
            n_opn = live_feed[nifty_token].get('open', 0)
            if n_opn > 0:
                nifty_pct = ((n_ltp - n_opn) / n_opn) * 100

        for s in STOCKS:
            symbol = s["name"]
            stock_token = s.get("token")
            
            # --- FETCH LIVE MARKET DATA ---
            live_ltp = 0.0; live_open = 0.0; live_low = 0.0
            live_vol = 0; live_bid = "N/A"; live_ask = "N/A"
            stock_pct = 0.0
            
            if stock_token and stock_token in live_feed:
                feed = live_feed[stock_token]
                live_ltp = feed.get("ltp", 0.0)
                live_open = feed.get("open", 0.0)
                live_low = feed.get("low", 0.0)
                live_vol = feed.get("volume", 0)
                live_bid = feed.get("best_bid", "N/A")
                live_ask = feed.get("best_ask", "N/A")
                
                if live_open > 0:
                    stock_pct = ((live_ltp - live_open) / live_open) * 100

            # --- FETCH TECHNICAL INDICATORS & 10-DAY AVG VOLUME ---
            rsi_val, sma_val, macd_val, avg_vol_10d = get_technical_indicators(symbol)
            
            # =================================================================
            # 🚀 INSTITUTIONAL HARD FILTERS (SMART MONEY LOGIC) 🚀
            # =================================================================
            
            filter_passed = True
            rejection_reason = ""

            # Filter 1: RELATIVE STRENGTH (RS) - Stock must outperform Nifty
            if stock_pct <= nifty_pct:
                filter_passed = False
                rejection_reason += f"❌ Relative Strength Fail (Stock {stock_pct:.2f}% vs Nifty {nifty_pct:.2f}%)<br>"

            # Filter 2: OPEN = LOW STRATEGY (Bullish Operator Entry)
            if live_open == 0 or live_low == 0 or abs(live_open - live_low) > (live_open * 0.0005):
                filter_passed = False
                rejection_reason += f"❌ Open-Low Fail (Open: {live_open}, Low: {live_low})<br>"

            # Filter 3: VOLUME ANOMALY (Live Vol must be > 1.5x of 10-Day Avg)
            if live_vol == 0 or avg_vol_10d == 0 or live_vol < (1.5 * avg_vol_10d):
                filter_passed = False
                rejection_reason += f"❌ Volume Anomaly Fail (Live: {live_vol}, Need > {int(1.5 * avg_vol_10d)})<br>"

            # -----------------------------------------------------------------

            # 1. Generate fast python charts (15m and 1h)
            img15_name = f"chart_{symbol}_15m.png"
            img1h_name = f"chart_{symbol}_1h.png"
            c15_success = generate_mpl_chart(symbol, "15m", img15_name)
            c1h_success = generate_mpl_chart(symbol, "1h", img1h_name)
            local_paths = [os.path.join('static', img15_name), os.path.join('static', img1h_name)]

            # ✅ CHANGED: DO NOT BYPASS AI IF FILTERS FAIL. JUST PREPARE A WARNING MESSAGE.
            warning_html = ""
            if not filter_passed:
                warning_html = f"<span style='color:var(--accent-red); font-weight:800;'>🚨 ALGO WARNING: ऑपरेटर ट्रॅप असू शकतो!</span><br><span style='font-size:12px; font-weight:normal; color:#E0E0E0;'>{rejection_reason}</span><br><br>"
            else:
                warning_html = "<span style='color:var(--accent-green); font-weight:800;'>✅ ALGO PASSED: Smart Money Detected!</span><br><br>"

            # ✅ SEND TO GEMINI AI (ALWAYS EXECUTE, EVEN IF FILTER FAILS)
            if GEMINI_API_KEY:
                try:
                    prompt_text = f"""
                    You are an expert stock market technical analyst. 
                    Analyze these two charts (15-minute and 1-hour) for the stock '{symbol}'.
                    
                    [IMPORTANT LIVE MARKET DATA]
                    - Current Price (LTP): {live_ltp}
                    - Today's Open: {live_open} (Open=Low strategy matched!)
                    - Total Volume Today: {live_vol} (150% Volume Anomaly detected!)
                    - Best Buyer Price (Best Bid): {live_bid}
                    - Best Seller Price (Best Ask): {live_ask}
                    - INDIA VIX (Simulated Panic Level): {LIVE_VIX}
                    
                    [LIVE TECHNICAL INDICATORS DATA (15m Timeframe)]
                    - RSI (14): {rsi_val}
                    - SMA (20): {sma_val}
                    - MACD: {macd_val}
                    
                    Please answer the following questions strictly in MARATHI language.
                    You must split your response into FIVE sections using EXACTLY the word '---SPLIT---' between them.
                    
                    SECTION 1 Questions:
                    1) दोन्ही चार्ट आणि व्हॉल्यूम डेटा काय सांगत आहेत, अपट्रेड आहे की डाउनट्रेड?
                    2) smc lines, fvg, ob आणि दिलेले RSI, MACD व SMA इंडिकेटर्स काय सांगत आहे? (Give exact pinpoint numerical values for FVG like '148.50 ते 149.20').
                    3) smc नुसार आणि इंडिकेटर्स नुसार किती पर्सेंटेज आहे buy/sell साठी?
                    4) ट्रेंड केव्हा बदलेल?

                    ---SPLIT---

                    SECTION 2 Questions (Smart Money Concepts, Liquidity & Volume Trap):
                    1) या झोन मध्ये येणे आधी किंमतीने मागचा हाय किंवा लो स्वीप करून स्टॉपलॉस उडवले आहेत का?
                    2) या झोनच्यावर किंवा खाली रिटेलर्स ना अडकवण्यासाठी काही आमिष (Liquidity) आहे का?
                    3) जरी येथे इंड्युसमेंट नसेल तर त्याला फेक म्हणून घोषित कर?
                    4) ऑपरेटर चा ट्रॅप फेल झाला असेल आणि रस्ता मोकळा असेल तेव्हा सांग? (Live Volume आणि Bid/Ask data वापरून सांग. जर Fake Breakout असेल आणि ऑपरेटर गेम करत असेल तर लाल रंगात "🚨 सावधान: ऑपरेटर अलर्ट! हा Fake Breakout असू शकतो." असे लिहा).

                    ---SPLIT---

                    SECTION 3 Questions (Final Action):
                    entry kuthe gyaychi stoploss kuthe taragate kuthe risk rewrd kiti kiti theu 10000 capital var kiti shares geun 10000 capital var risk kiti? (Give precise numbers only, e.g. Entry: 150.25).

                    ---SPLIT---

                    SECTION 4 (Signal & VIX Kill Switch):
                    If INDIA VIX > 20, strictly output: '🚫 PANIC - NO TRADE' or 'HOLD (VIX HIGH)'.
                    Otherwise, strictly output ONLY ONE WORD based on your analysis: 'BUY', 'SELL', or 'HOLD'. 
                    Do not output anything else here.

                    ---SPLIT---

                    SECTION 5 (Table Data):
                    Strictly output ONLY precise numerical values separated by a pipe character '|' in this exact order:
                    Entry|Stoploss|Target|RiskReward
                    Example: 150.25|148.10|155.00|1:2
                    Do not add any text or spaces.
                    """
                    
                    model = genai.GenerativeModel('gemini-2.0-flash-thinking-exp-01-21')
                    contents = [prompt_text]
                    
                    if c15_success and c1h_success:
                        contents.append(Image.open(local_paths[0]))
                        contents.append(Image.open(local_paths[1]))
                        
                    response = model.generate_content(contents)
                    
                    full_text = response.text
                    parts = full_text.split("---SPLIT---")
                    
                    if len(parts) >= 5:
                        ai_signal_text = parts[3].strip().upper()
                        
                        PRECALCULATED_DATA[symbol] = {
                            "status": "success",
                            "charts": {
                                "15m": f"/static/{img15_name}" if c15_success else "", 
                                "1h": f"/static/{img1h_name}" if c1h_success else ""
                            },
                            "ai_analysis": warning_html + parts[0].strip().replace("\n", "<br>"),
                            "ai_analysis_2": parts[1].strip().replace("\n", "<br>"),
                            "ai_analysis_3": parts[2].strip().replace("\n", "<br>"),
                            "ai_signal": ai_signal_text,
                            "ai_table_data": parts[4].strip()
                        }
                        
                        # ========================================================
                        # 🤖 AUTOMATIC PAPER TRADING EXECUTION
                        # ========================================================
                        if ("BUY" in ai_signal_text or "SELL" in ai_signal_text) and symbol not in paper_positions:
                            action = "BUY" if "BUY" in ai_signal_text else "SELL"
                            if live_ltp > 0:
                                print(f"🚀 AI SIGNAL TRIGGERED: {action} {symbol} at {live_ltp}")
                                execute_paper_order(symbol, action, live_ltp)
                                
                    else:
                        raise ValueError("Gemini format invalid")
                        
                except Exception as e:
                    print(f"⚠️ [CRON] AI Error for {symbol}: {e}")
                    PRECALCULATED_DATA[symbol] = {
                        "status": "success",
                        "charts": {"15m": "", "1h": ""},
                        "ai_analysis": warning_html + "⚠️ सध्या चार्ट उपलब्ध नाही किंवा AI ने उत्तर दिले नाही. पुन्हा प्रयत्न करा.",
                        "ai_analysis_2": "डेटा उपलब्ध नाही",
                        "ai_analysis_3": "डेटा उपलब्ध नाही",
                        "ai_signal": "HOLD",
                        "ai_table_data": "0|0|0|0"
                    }
            
            # API Rate limit sleep
            time.sleep(3) 
        
        time.sleep(60)
# --- 7. FLASK ROUTES ---
@app.route('/')
def index():
    # आपण सुरवातीला जे variables रेंडर करतो त्यात बदल नाही
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    # ✅ NEW: UI ला Paper Trading चा लाईव्ह डेटा पाठवण्यासाठी अपडेट केले
    return jsonify({
        "status": market_status, 
        "ans1": ans1_nifty, 
        "ans2": ans2_sector, 
        "winner": winning_sector_code, 
        "stocks": STOCKS,
        "paper_margin": paper_margin,
        "paper_positions": paper_positions,
        "paper_orders": paper_orders
    })

@socketio.on('toggle_engine')
def handle_toggle(data):
    global system_active
    system_active = data.get('state', True)
    status_msg = "SYSTEM STARTED" if system_active else "SYSTEM STOPPED"
    socketio.emit('process_log', {'msg': status_msg})

# --- OPTIMIZED FAST ROUTE (ZERO WAIT TIME) ---
@app.route('/get_chart_screenshot/<symbol>')
def get_chart_screenshot(symbol):
    global system_active, PRECALCULATED_DATA
    
    if not system_active:
         socketio.emit('process_log', {'msg': '❌ System is PAUSED. Enable Switch to Read Charts.'})
         return jsonify({"status": "error", "message": "System Paused"})

    if symbol in PRECALCULATED_DATA:
        socketio.emit('process_log', {'msg': '⚡ ZERO WAIT TIME: Data loaded instantly!'})
        return jsonify(PRECALCULATED_DATA[symbol])
    else:
        socketio.emit('process_log', {'msg': '⏳ Calculating... Try clicking again in 10 seconds.'})
        return jsonify({"status": "error", "message": "Data Not Ready Yet. AI is calculating in background."})

# --- 8. HTML TEMPLATE (NEON CYBERPUNK THEME + GPU ACCELERATION) ---
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
.switch {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 20px;
}
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: rgba(255, 255, 255, 0.1);
  -webkit-transition: .4s;
  transition: .4s;
  border: 1px solid var(--accent-red);
}
.slider:before {
  position: absolute;
  content: "";
  height: 14px;
  width: 14px;
  left: 3px;
  bottom: 2px;
  background-color: var(--accent-red);
  -webkit-transition: .4s;
  transition: .4s;
  box-shadow: 0 0 5px var(--accent-red);
}
input:checked + .slider {
  background-color: rgba(0, 255, 255, 0.2);
  border-color: var(--accent-green);
}
input:checked + .slider:before {
  -webkit-transform: translateX(18px);
  -ms-transform: translateX(18px);
  transform: translateX(18px);
  background-color: var(--accent-green);
  box-shadow: 0 0 5px var(--accent-green);
}
.slider.round {
  border-radius: 20px;
}
.slider.round:before {
  border-radius: 50%;
}


/* --- NEW MARATHI STATUS BAR (LIVR PATII) --- */
.status-bar-marathi {
    background: rgba(0, 5, 8, 0.8);
    color: var(--accent-blue);
    font-size: 11px;
    text-align: center;
    padding: 5px;
    border-bottom: 1px solid var(--border);
    display: none; /* Hidden by default */
    font-weight: 800;
}

/* --- TOP DASHBOARD AREA --- */
.dashboard-summary {
    display: flex; gap: 15px; padding: 20px;
    border-bottom: 1px solid rgba(0, 255, 255, 0.1);
    background: linear-gradient(180deg, rgba(0, 255, 255, 0.05) 0%, rgba(0,0,0,0) 100%);
}
.summary-card {
    flex: 1; 
    background: var(--bg-card); 
    border: 1px solid rgba(0, 255, 255, 0.3);
    border-radius: 16px; padding: 15px; display: flex; flex-direction: column; gap: 5px;
    box-shadow: 0 0 10px rgba(0, 255, 255, 0.1); /* Subtle Glow */
    backdrop-filter: blur(5px); /* Glassmorphism */
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
    box-shadow: 0 0 15px rgba(0, 255, 255, 0.4); /* Active Glow */
}

/* --- MAIN STOCK LIST (Neon Glass Cards + GPU ACCELERATION) --- */
.stock-list { 
    flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; 
    touch-action: pan-y; 
}

.stock-card { 
    background: var(--bg-card); /* Glass Background */
    border: 1px solid rgba(0, 255, 255, 0.4); /* Cyan Border */
    padding: 18px 20px; 
    border-radius: 16px; 
    display: flex; justify-content: space-between; align-items: center;
    box-shadow: 0 0 8px rgba(0, 255, 255, 0.2); /* Neon Glow Effect */
    backdrop-filter: blur(8px); /* Glassmorphism */
    transition: all 0.3s ease; 
    will-change: transform; 
    /* ✅ GPU TRICK: Forces Hardware Acceleration */
    transform: translate3d(0, 0, 0);
    backface-visibility: hidden;
    touch-action: pan-y;
    cursor: pointer;
}
.stock-card:hover { 
    transform: translateY(-2px) translate3d(0,0,0); 
    box-shadow: 0 0 20px rgba(0, 255, 255, 0.5); /* Stronger Glow on Hover */
    border-color: var(--accent-blue);
}

.st-info { display: flex; flex-direction: column; gap: 4px; }
.st-name { font-size: 16px; font-weight: 800; color: var(--text-main); letter-spacing: 0.5px; text-transform: uppercase; }
.st-cat-tag { 
    font-size: 10px; font-weight: 700; color: var(--text-muted); 
    background: rgba(0, 255, 255, 0.1); padding: 3px 8px; 
    border-radius: 6px; width: fit-content; text-transform: uppercase;
    border: 1px solid rgba(0, 255, 255, 0.2);
}

.st-price-box { text-align: right; }
.st-price { 
    font-size: 20px; font-weight: 800; color: var(--accent-green); letter-spacing: 0.5px; 
    text-shadow: 0 0 10px rgba(57, 255, 20, 0.6); /* Price Glow */
}
.st-wait { font-size: 14px; font-weight: 600; color: var(--text-muted); }
.st-change { font-size: 12px; font-weight: 600; margin-top: 2px; } /* Added for points change */

/* --- POPUP WINDOW STYLES (Neon) --- */
.modal-overlay {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0, 5, 8, 0.9); z-index: 2000;
    display: flex; justify-content: center; align-items: flex-start; padding-top: 40px;
    backdrop-filter: blur(10px);
    overflow-y: auto;
}
.modal-box {
    background: rgba(1, 12, 20, 0.9); width: 85%; max-width: 350px;
    padding: 25px; border-radius: 20px; border: 1px solid var(--accent-blue);
    text-align: center; box-shadow: 0 0 30px rgba(0, 255, 255, 0.3); /* Big Glow */
    animation: popin 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    display: flex; flex-direction: column; gap: 15px;
    margin-bottom: 40px;
    position: relative; /* For Absolute Close Button */
}
.modal-title { font-size: 18px; font-weight: 800; color: var(--text-main); margin-bottom: 5px; text-transform: uppercase; text-shadow: 0 0 5px var(--accent-blue); display: flex; align-items: center; justify-content: center; gap: 10px;}

.modal-close-icon {
    position: absolute;
    top: 15px;
    right: 15px;
    color: var(--accent-red);
    font-size: 24px;
    cursor: pointer;
    font-weight: 800;
    text-shadow: 0 0 5px var(--accent-red);
}

.chart-label {
    font-size: 12px; font-weight: 700; color: var(--accent-blue);
    text-align: left; margin-bottom: 5px; margin-top: 10px; text-transform: uppercase;
}

.gemini-card {
    background: rgba(150, 46, 255, 0.1); 
    border: 1px solid var(--accent-purple);
    border-radius: 12px;
    padding: 15px;
    margin-top: 15px;
    display: none; 
    text-align: left;
    box-shadow: 0 0 10px rgba(150, 46, 255, 0.2);
}
.gemini-title {
    font-size: 14px; font-weight: 800; color: var(--accent-purple);
    display: flex; align-items: center; gap: 5px; margin-bottom: 8px;
    text-transform: uppercase;
}
.gemini-text {
    font-size: 11px; color: #E0E0E0; line-height: 1.4; font-weight: 500;
}

.ai-selector {
    width: 100%;
    background: var(--bg-card);
    border: 1px solid var(--accent-purple);
    color: var(--accent-purple);
    padding: 10px;
    border-radius: 10px;
    font-weight: 800;
    margin-top: 15px;
    cursor: pointer;
    outline: none;
    text-transform: uppercase;
    text-align: center;
    font-family: 'Inter', sans-serif;
    box-shadow: 0 0 8px rgba(150, 46, 255, 0.2);
    display: none; 
}
.ai-selector option {
    background: var(--bg-main);
    color: var(--text-main);
}

.signal-badge {
    font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 6px;
    border: 1px solid var(--border); display: none;
}
.signal-buy { background: rgba(57, 255, 20, 0.2); color: var(--accent-green); border-color: var(--accent-green); box-shadow: 0 0 8px rgba(57, 255, 20, 0.5); }
.signal-sell { background: rgba(255, 49, 49, 0.2); color: var(--accent-red); border-color: var(--accent-red); box-shadow: 0 0 8px rgba(255, 49, 49, 0.5); }
.signal-hold { background: rgba(0, 255, 255, 0.2); color: var(--accent-blue); border-color: var(--accent-blue); }

.action-table-container {
    margin-top: 15px;
    display: none; 
    background: rgba(1, 12, 20, 0.8);
    border-radius: 10px;
    border: 1px solid var(--accent-purple);
    overflow: hidden;
    box-shadow: 0 0 10px rgba(150, 46, 255, 0.2);
}
.action-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
}
.action-table th {
    background: rgba(150, 46, 255, 0.2);
    color: var(--accent-purple);
    padding: 8px;
    text-transform: uppercase;
    font-weight: 800;
    border-bottom: 1px solid var(--accent-purple);
}
.action-table td {
    color: #fff;
    padding: 10px 5px;
    font-weight: 700;
    text-align: center;
    border-right: 1px solid rgba(150, 46, 255, 0.2);
}
.action-table td:last-child { border-right: none; }

@keyframes popin { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }

/* --- BOTTOM NAVIGATION (Modern) --- */
.bottom-nav {
    height: 70px; background: rgba(0, 5, 8, 0.95);
    border-top: 1px solid rgba(0, 255, 255, 0.2);
    display: flex; justify-content: space-around; align-items: center;
    padding-bottom: 10px; backdrop-filter: blur(10px);
}
.nav-item {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    color: rgba(255, 255, 255, 0.5); cursor: pointer; flex: 1; height: 100%;
    transition: all 0.2s;
}
.nav-item.active { color: var(--accent-blue); text-shadow: 0 0 10px rgba(0, 255, 255, 0.6); }
.nav-icon { font-size: 26px; margin-bottom: 2px; }
.nav-label { font-size: 10px; font-weight: 600; text-transform: uppercase; }

/* --- UTILS & ANIMATION --- */
.hidden { display: none !important; }
@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
.loading-pulse { animation: pulse 1.5s infinite; }

/* Custom Scrollbar */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: var(--bg-main); }
::-webkit-scrollbar-thumb { background: var(--accent-blue); border-radius: 10px; }


/* =========================================================
   ✅ NEW CSS FOR POSITIONS, ORDERS, & MENU (m.Stock Like) 
   ========================================================= */

/* Section Toggling */
.page-section { 
    display: none; flex: 1; overflow-y: auto; padding: 20px; 
    flex-direction: column; gap: 12px; touch-action: pan-y; 
}
.page-section.active { display: flex; }

/* Trade Cards (m.Stock matching table style) */
.trade-card {
    background: var(--bg-card); 
    border: 1px solid rgba(0, 255, 255, 0.4); 
    padding: 15px; border-radius: 12px; 
    box-shadow: 0 0 8px rgba(0, 255, 255, 0.2); 
    backdrop-filter: blur(8px);
}
.trade-header { 
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 12px; border-bottom: 1px dashed rgba(0,255,255,0.2); 
    padding-bottom: 8px; 
}
.trade-title { font-size: 16px; font-weight: 800; color: var(--text-main); }
.trade-type { 
    font-size: 10px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase;
}
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

/* Orders Tabs (Executed / Pending) */
.orders-tab-bar { display: flex; gap: 10px; margin-bottom: 15px; }
.order-tab { 
    flex: 1; text-align: center; padding: 10px; border: 1px solid var(--border); 
    border-radius: 8px; font-size: 12px; font-weight: 700; color: var(--text-muted); 
    cursor: pointer; transition: all 0.3s; text-transform: uppercase;
}
.order-tab.active { 
    background: rgba(0,255,255,0.2); color: white; 
    box-shadow: 0 0 10px rgba(0,255,255,0.3); border-color: var(--accent-blue);
}

/* Menu Layout */
.menu-profile { text-align: center; padding: 20px 0; border-bottom: 1px solid rgba(0,255,255,0.2); }
.menu-greeting { font-size: 16px; font-weight: 700; color: var(--text-muted); }
.menu-funds-label { font-size: 12px; font-weight: 700; margin-top: 15px; color: var(--text-muted); text-transform: uppercase;}
.menu-funds-val { 
    font-size: 32px; font-weight: 800; color: var(--accent-green); 
    text-shadow: 0 0 15px rgba(57, 255, 20, 0.6); margin-top: 5px; 
}
.menu-list { margin-top: 20px; display: flex; flex-direction: column; gap: 15px; }
.menu-item {
    display: flex; align-items: center; gap: 15px; padding: 15px;
    background: var(--bg-card); border: 1px solid rgba(0,255,255,0.2);
    border-radius: 12px; color: white; font-weight: 700; cursor: pointer;
}
.menu-item .material-icons-outlined { color: var(--accent-blue); }

.empty-state {
    text-align: center; color: var(--text-muted); padding: 40px 20px;
    font-size: 14px; font-weight: 600;
}
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

// ==========================================
// ✅ NEW: PAGE SWITCHING LOGIC (m.Stock Like)
// ==========================================
function switchPage(pageId, navElement) {
    // 1. Hide all pages
    document.querySelectorAll('.page-section').forEach(p => p.classList.remove('active'));
    
    // 2. Show target page
    document.getElementById('page-' + pageId).classList.add('active');
    
    // 3. Update bottom nav state
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    navElement.classList.add('active');
    
    // 4. Hide Top Dashboard/Filter on other pages
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
    // Force re-render of orders immediately
    if (latestData) updateDashboard(latestData);
}

// --- NEW FUNCTION: DROPDOWN LOGIC ---
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

    document.getElementById('popup-name').innerText = stockName;
    document.getElementById('modal-overlay').classList.remove('hidden');

    const img15 = document.getElementById('img-15m');
    const img1h = document.getElementById('img-1h');
    const statusContainer = document.getElementById('status-container');
    const processMsg = document.getElementById('process-msg');
    const aiSelector = document.getElementById('ai-selector');
    const signalBadge = document.getElementById('ai-signal-badge');
    const tableContainer = document.getElementById('action-table-container');
    const geminiCard = document.getElementById('gemini-card');
    const geminiText = document.getElementById('gemini-text');
    const geminiCard2 = document.getElementById('gemini-card-2');
    const geminiText2 = document.getElementById('gemini-text-2');
    const geminiCard3 = document.getElementById('gemini-card-3');
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
        geminiCard.style.display = 'none';
        geminiCard2.style.display = 'none';
        if(geminiCard3) geminiCard3.style.display = 'none';
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
                const img15Url = data.charts['15m'] + "?" + new Date().getTime();
                const img1hUrl = data.charts['1h'] + "?" + new Date().getTime();
                
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
                    table: tData
                };

            } else {
                processMsg.innerText = "Error: " + data.message;
                processMsg.style.color = "var(--accent-red)";
                geminiText.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
                geminiText2.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
                if(geminiText3) geminiText3.innerHTML = "<span style='color:var(--accent-red)'>Analysis Failed.</span>";
            }
        })
        .catch(err => {
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
    
    // Update Margin in Menu
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
    
    let activePopupStock = document.getElementById('popup-name').innerText;
    let isPopupOpen = !document.getElementById('modal-overlay').classList.contains('hidden');

    // 1. Update Home Page Stocks
    data.stocks.forEach(s => {
        const priceEl = document.getElementById('price-' + s.name);
        const changeEl = document.getElementById('change-' + s.name);
        
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

    // ==========================================
    // 2. Render POSITIONS (Paper Trading)
    // ==========================================
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
        posContainer.innerHTML = posHTML;
    } else {
        posContainer.innerHTML = `<div class="empty-state">Yet to trade today?<br>No open positions found.</div>`;
    }

    // ==========================================
    // 3. Render ORDERS (Paper Trading)
    // ==========================================
    const ordContainer = document.getElementById('orders-list');
    if (data.paper_orders && data.paper_orders.length > 0) {
        let filteredOrders = data.paper_orders.filter(o => 
            (currentOrderTab === 'EXECUTED' && o.status === 'Executed') || 
            (currentOrderTab === 'PENDING' && o.status !== 'Executed')
        );
        
        if(filteredOrders.length > 0) {
            let ordHTML = '';
            // Reverse so newest is on top
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
            ordContainer.innerHTML = ordHTML;
        } else {
            ordContainer.innerHTML = `<div class="empty-state">No ${currentOrderTab.toLowerCase()} orders found.</div>`;
        }
    } else {
        ordContainer.innerHTML = `<div class="empty-state">Yet to trade today?<br>No orders placed.</div>`;
    }
}

function toggleSystem() {
    const isChecked = document.getElementById('systemToggle').checked;
    socket.emit('toggle_engine', {state: isChecked});
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
</div>

<div id="page-home" class="page-section active">
    {% for s in stocks %}
    <div class="stock-card" id="card-{{ s.name }}" data-cat="{{ s.cat }}" onclick="openCardPopup('{{ s.name }}')">
        <div class="st-info">
            <span class="st-name">{{ s.name }}</span>
            <span class="st-cat-tag">{{ s.cat }} SECTOR</span>
        </div>
        <div class="st-price-box">
            <div class="st-wait" id="price-{{ s.name }}">{{ s.price }}</div>
            <div class="st-change" id="change-{{ s.name }}">{{ s.change }}</div>
        </div>
    </div>
    {% endfor %}
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

<div id="page-menu" class="page-section">
    <div class="menu-profile">
        <div class="menu-greeting">Hello Pradip 🚀</div>
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
    <div class="nav-item" onclick="switchPage('menu', this)">
        <span class="material-icons-outlined nav-icon">menu</span>
        <span class="nav-label">Menu</span>
    </div>
</div>

</body>
</html>
'''

# 👇 हे थ्रेड्स आता सर्वात शेवटी आहेत, त्यामुळे NameError येणार नाही 👇
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
