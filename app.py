import sys
import uuid
import base64
import os
import time
import threading
import requests
import json
import csv 
import gzip
import io
import re
import urllib.parse
import websocket
import xml.etree.ElementTree as ET
from google.protobuf.json_format import MessageToDict
import MarketDataFeedV3_pb2 as MarketDataFeed_pb2
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from flask_socketio import SocketIO, emit
from concurrent.futures import ThreadPoolExecutor
import math

# ==========================================
# ✅ SMART LOG EMITTER (ALL PRINTS TO UI TERMINAL)
# ==========================================
PROCESS_LOGS = []
process_logs_lock = threading.Lock()
socketio = None
os.environ['PYTHONUNBUFFERED'] = '1'

def sys_print(msg):
    global PROCESS_LOGS, socketio
    print(msg, file=sys.stderr, flush=True)
    now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S")
    full_msg = f"[{now}] {msg}"
    with process_logs_lock:
        PROCESS_LOGS.append(full_msg)
        if len(PROCESS_LOGS) > 100:  
            PROCESS_LOGS.pop(0)
    try:
        if socketio is not None:
            socketio.emit('new_process_log', {'msg': full_msg}) 
    except:
        pass
sys_print("💓 [HEARTBEAT] Script started. Loading imports...")
sys_print("🚀 Sentry removed. Starting core modules...")

import google.genai as genai
from PIL import Image
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
from pymongo import MongoClient

mongo_db = None
try:
    client = MongoClient(os.environ.get("MONGODB_URI"))
    mongo_db = client["trading_db"]
    sys_print("✅ [MONGODB] Connected!")
except Exception as e:
    sys_print(f"⚠️ [MONGODB] Error: {e}")

sys_print("💓 [HEARTBEAT] AI and Charting libraries loaded. Setting up Flask...")

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'secret_key_for_websocket'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

if not os.path.exists('static'):
    os.makedirs('static')

sys_print("💓 [HEARTBEAT] Fetching Environment Variables...")

API_KEY = os.environ.get("API_KEY") 
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "YOUR_UPSTOX_ACCESS_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

upstox_headers = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {ACCESS_TOKEN}'
}

# ✅ DISCORD WEBHOOK (Replaced Telegram)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

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
key_lock = threading.Lock()

sys_print("💓 [HEARTBEAT] Configuring First Available Gemini Key...")
sys_print("💓 [HEARTBEAT] Initializing Global Data Store...")

live_feed = {} 
live_feed_lock = threading.Lock()

market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL"
confirmed_winner = None 
data_fetched_once = False
tokens_loaded = False 
sws = None 
system_active = True 
upstox_ws_url = None 

SMC_MATCHED_STOCKS = {}
smc_lock = threading.Lock()

PRECALCULATED_DATA = {}
LIVE_VIX = 15.0 
stocks_lock = threading.Lock()

# ✅ DISCORD ALERT FUNCTION
def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        sys_print("⚠️ Discord Webhook URL missing!")
        return
    try:
        clean_msg = message.replace("<b>", "**").replace("</b>", "**").replace("<i>", "*").replace("</i>", "*")
        payload = {"content": clean_msg}
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 204:
            sys_print("✅ [DISCORD] Alert Sent Successfully!")
        else:
            sys_print(f"⚠️ [DISCORD] Error: {response.text}")
    except Exception as e:
        sys_print(f"❌ [DISCORD] Crash: {e}")

# ==========================================
# 🧠 SMART CACHE (UPDATED WITH 1m & 5m FOR MTF SMC)
# ==========================================
SMART_CACHE = {
    "1m_chart": {},  # For LTF CHOCH Entry
    "5m_chart": {},  # For MTF Zone / OB
    "15m_chart": {}, # For HTF BOS Trend
    "1h_chart": {},  
    "15m_ind": {},   
    "1d_ind": {}     
}

CACHE_EXPIRY = {
    "1m_chart": 60,    # 1 minute expiry
    "5m_chart": 300,   # 5 minute expiry
    "15m_chart": 1800, # 30 minute expiry
    "1h_chart":  3600,
    "15m_ind":   1800,
    "1d_ind":    86400
}

CACHE_FILE = "trading_cache.json"
cache_lock = threading.Lock() 
last_cache_save = time.time()

def load_smart_cache():
    global SMART_CACHE
    sys_print("💓 [HEARTBEAT] Attempting to load Smart Cache...")
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                loaded_cache = json.load(f)
                # Merge safely to keep new keys like 1m_chart and 5m_chart
                for key in SMART_CACHE.keys():
                    if key in loaded_cache:
                        SMART_CACHE[key] = loaded_cache[key]
            sys_print("✅ Smart Cache Loaded from Disk!")
        except Exception as e:
            sys_print(f"⚠️ Cache Load Error: {e}")
    else:
        sys_print("💓 [HEARTBEAT] No existing cache file found.")

def save_smart_cache():
    global last_cache_save
    try:
        if time.time() - last_cache_save > 300:
            with cache_lock: 
                with open(CACHE_FILE, 'w') as f:
                    json.dump(SMART_CACHE, f)
            last_cache_save = time.time()
    except Exception as e:
        sys_print(f"⚠️ Cache Save Error: {e}")

def _get_cache_data(compartment_name, instrument_key):
    val = SMART_CACHE[compartment_name].get(instrument_key)
    if val is None: return []
    if isinstance(val, dict): return list(val.get("data", []))
    return list(val)

def _is_cache_fresh(compartment_name, instrument_key):
    val = SMART_CACHE[compartment_name].get(instrument_key)
    if val is None: return False
    if isinstance(val, dict):
        data = val.get("data", [])
        if not data: return False
        age = time.time() - val.get("ts", 0)
        return age < CACHE_EXPIRY.get(compartment_name, 1800)
    return len(val) > 0

def _set_cache_data(compartment_name, instrument_key, data_list):
    if compartment_name not in SMART_CACHE:
        SMART_CACHE[compartment_name] = {}
    SMART_CACHE[compartment_name][instrument_key] = {"data": data_list, "ts": time.time()}

load_smart_cache()

sys_print("💓 [HEARTBEAT] END OF PART 1")
# ==========================================
# 🚀 SAFE API CALLER 
# ==========================================
api_lock = threading.Lock()

def safe_api_call(url):
    try:
        response = requests.get(url, headers=upstox_headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') != 'success':
                sys_print(f"💓 [HEARTBEAT] API returned non-success: {data}")
            return data
        elif response.status_code == 429:
            sys_print("⚠️ Rate Limit 429 Hit in safe_api_call. Waiting 2s...")
            time.sleep(2)
            return requests.get(url, headers=upstox_headers, timeout=10).json()
        else:
            sys_print(f"💓 [HEARTBEAT] API Error HTTP {response.status_code} for URL: {url}")
            sys_print(f"💓 [HEARTBEAT] Response Text: {response.text}")
    except Exception as e:
        sys_print(f"❌ API Error: {e}")
    return None

# ==========================================
# 🧠 SMART DATA FETCHER (UPDATED FOR 1m & 5m SMC)
# ==========================================
def fetch_smart_data(instrument_key, interval_str, days_back, compartment_name):
    global SMART_CACHE, live_feed, live_feed_lock

    with cache_lock:
        has_cache = _is_cache_fresh(compartment_name, instrument_key)

    if has_cache:
        sys_print(f"🧠 [DEBUG] Cache HIT for {compartment_name}. Using cached data.")

        with cache_lock:
            data_list = _get_cache_data(compartment_name, instrument_key)

        try:
            last_time_str = data_list[-1][0] 
            last_dt = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M")
            last_dt = last_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            
            diff_mins = (now - last_dt).total_seconds() / 60
            
            # ✅ MTF INTERVAL LOGIC
            interval_limit = 1 if "1m" in compartment_name else (5 if "5m" in compartment_name else (15 if "15m" in compartment_name else (60 if "1h" in compartment_name else 1440)))
            
            with live_feed_lock:
                ltp_val = live_feed.get(instrument_key, {}).get('ltp', 0.0)

            if diff_mins >= interval_limit and ltp_val > 0:
                new_ts = now.strftime("%Y-%m-%d %H:%M")
                data_list.append([new_ts, ltp_val, ltp_val, ltp_val, ltp_val, 0])
        except Exception as e:
            sys_print(f"⚠️ [fetch_smart_data] Time Check Error: {e}")
            
        # ✅ MTF LIMITS ADJUSTED
        limits = {"1m_chart": 60, "5m_chart": 100, "15m_chart": 150, "1h_chart": 200, "15m_ind": 50, "1d_ind": 30}
        max_limit = limits.get(compartment_name, 100)

        if len(data_list) > max_limit:
            while len(data_list) > max_limit:
                data_list.pop(0) 
            
        with live_feed_lock:
            feed_data = live_feed.get(instrument_key, {})
            ltp = feed_data.get('ltp', 0.0)
            vol = feed_data.get('volume', 0)

        if ltp > 0:
            data_list[-1][4] = ltp 
            if ltp > float(data_list[-1][2]): data_list[-1][2] = ltp
            if ltp < float(data_list[-1][3]): data_list[-1][3] = ltp
            data_list[-1][5] = vol

        with cache_lock:
            _set_cache_data(compartment_name, instrument_key, data_list)

        save_smart_cache()
        return data_list
        
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    cutoff_dt = ist_now - timedelta(days=days_back)
    todate_str = ist_now.strftime("%Y-%m-%d")
    fromdate_str = cutoff_dt.strftime("%Y-%m-%d")
    
    # ✅ UPSTOX INTERVAL MAPPING ADDED FOR 1m & 5m
    upstox_interval = 'day'
    if interval_str == "ONE_MINUTE": upstox_interval = '1minute'
    elif interval_str == "FIVE_MINUTE": upstox_interval = '1minute'
    elif interval_str == "FIFTEEN_MINUTE": upstox_interval = '30minute'
    elif interval_str == "ONE_HOUR": upstox_interval = 'day'
    elif interval_str == "ONE_DAY": upstox_interval = 'day'

    encoded_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{upstox_interval}/{todate_str}/{fromdate_str}"

    with cache_lock:
        cached_data = _get_cache_data(compartment_name, instrument_key)
    
    res = safe_api_call(url)
    if res and res.get('status') == 'success' and 'data' in res and 'candles' in res['data']:
        formatted_data = []
        for candle in reversed(res['data']['candles']):
            ts = candle[0][:16].replace("T", " ") 
            formatted_data.append([ts, candle[1], candle[2], candle[3], candle[4], candle[5]])
            
        with cache_lock:
            _set_cache_data(compartment_name, instrument_key, formatted_data)
            
        save_smart_cache()
        return formatted_data
    else:
        sys_print(f"💓 [HEARTBEAT] fetch_smart_data failed or empty for {instrument_key}. Res: {res}")
        
    return cached_data

# --- नवीन मार्केट टायमिंग आणि सुट्ट्यांचा कोड ---
NSE_HOLIDAYS = [
    "2026-01-26", "2026-03-03", "2026-03-20", "2026-04-02", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-09-15", "2026-10-02",
    "2026-11-08", "2026-12-25"
]

def is_market_open():
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    
    # १. शनिवार आणि रविवार चेक करणे (5=Sat, 6=Sun)
    if now.weekday() >= 5: return False, "MARKET CLOSED (WEEKEND)"
    
    # २. सण आणि सुट्ट्या चेक करणे
    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS: return False, "MARKET CLOSED (HOLIDAY)"
    
    # ३. वेळ चेक करणे (सकाळी ०९:१५ ते दुपारी ०३:३०)
    curr_time = now.time()
    start_time = datetime.strptime("09:15", "%H:%M").time()
    end_time = datetime.strptime("15:30", "%H:%M").time()
    
    if curr_time < start_time or curr_time > end_time:
        return False, "MARKET CLOSED (OFF HOURS)"
        
    return True, "MARKET OPEN"
# --------------------------------------------------

STATE_FILE = "trade_state.json"

def save_state(winner, sector_code):
    try:
        data = {"date": str(datetime.now(timezone.utc).date()), "winner": winner, "code": sector_code}
        with open(STATE_FILE, "w") as f: json.dump(data, f)
    except Exception as e:
        sys_print(f"⚠️ [save_state] Error: {e}")

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == str(datetime.now(timezone.utc).date()):
                    return data["winner"], data["code"]
    except Exception as e:
        sys_print(f"⚠️ [load_state] Error: {e}")
    return None, "ALL"

sys_print("💓 [HEARTBEAT] Defining TARGET_STOCKS and INDICES_LIST...")

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
    {"name": "EQUITASBNK", "cat": "BANK"}, {"name": "NFL", "cat": "CHEMICAL"},
    # --- नवीन 20 स्टॉक्स (100 च्या आसपासचे) खाली जोडले आहेत ---
    {"name": "SAIL", "cat": "METAL"}, {"name": "UNIONBANK", "cat": "BANK"},
    {"name": "MAHABANK", "cat": "BANK"}, {"name": "IOB", "cat": "BANK"},
    {"name": "SUZLON", "cat": "POWER"}, {"name": "UJJIVANSFB", "cat": "BANK"},
    {"name": "IEX", "cat": "POWER"}, {"name": "RENUKA", "cat": "SUGAR"},
    {"name": "LEMONTREE", "cat": "HOTEL"}, {"name": "MOTHERSON", "cat": "AUTO"},
    {"name": "EASEMYTRIP", "cat": "TRAVEL"}, {"name": "NATIONALUM", "cat": "METAL"},
    {"name": "HCC", "cat": "INFRA"}, {"name": "YESBANK", "cat": "BANK"},
    {"name": "TV18BRDCST", "cat": "MEDIA"}, {"name": "ALOKINDS", "cat": "TEXTILE"},
    {"name": "RCF", "cat": "CHEMICAL"}, {"name": "PSB", "cat": "BANK"},
    {"name": "TATASTEEL", "cat": "METAL"}, {"name": "IOC", "cat": "ENERGY"}
]

# ✅ NEW: Adding Sectoral Indices for Top-Down Approach Scanning
INDICES_LIST = [
    {"name": "NIFTY", "token": "NSE_INDEX|Nifty 50", "symbol": "Nifty 50"},
    {"name": "BANKNIFTY", "token": "NSE_INDEX|Nifty Bank", "symbol": "Nifty Bank"},
    {"name": "NIFTY_IT", "token": "NSE_INDEX|Nifty IT", "symbol": "Nifty IT"},
    {"name": "NIFTY_AUTO", "token": "NSE_INDEX|Nifty Auto", "symbol": "Nifty Auto"},
    {"name": "INDIA_VIX", "token": "NSE_INDEX|India VIX", "symbol": "India VIX"}
]

STOCKS = [{"name": s["name"], "cat": s["cat"], "token": None, "price": "WAIT...", "change": "WAIT...", "symbol": s["name"], "status_msg": "Initializing..."} for s in TARGET_STOCKS]

def fetch_correct_tokens():
    global STOCKS
    sys_print("💓 [HEARTBEAT] fetch_correct_tokens() triggered. Downloading Upstox CSV...")
    sys_print("🔎 STARTING UPSTOX DYNAMIC TOKEN SCANNER (30 STOCKS)...")
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
                        sys_print(f"✅ FOUND: {name} -> Token {found_token}")
                        temp_stocks.append({"name": name, "token": found_token, "symbol": name, "cat": cat, "price": "0.00", "change": "0.00", "status_msg": "Token Found."})
                    else:
                        sys_print(f"❌ FAILED: {name} Token not found.")
                        temp_stocks.append({"name": name, "token": None, "cat": cat, "price": "ERR", "change": "ERR", "status_msg": "❌ Error: Token Not Found"})
    except Exception as e:
        sys_print(f"⚠️ [fetch_correct_tokens] Fetch Error: {str(e)[:30]}")
    
    if temp_stocks:
        with stocks_lock:
            STOCKS = temp_stocks
    sys_print("✅ SCAN COMPLETE: Ready for Market Data.")

# --- 4. REAL UPSTOX PROTOBUF WEBSOCKET ENGINE ---
def on_ws_message(ws, message):
    global live_feed
    try:
        if isinstance(message, str):
            return
        feed_response = MarketDataFeed_pb2.FeedResponse()
        feed_response.ParseFromString(message)
        data_dict = MessageToDict(feed_response)
        
        # हा प्रिंट इथे बरोबर आहे कारण त्याला फक्त data_dict लागतो
        sys_print(f"🔍 TYPE: {data_dict.get('type')} | FEEDS: {len(data_dict.get('feeds', {}))}")

        feeds = data_dict.get('feeds', {})
        for key, feed_data in feeds.items():
            try:
                with live_feed_lock:
                    existing = live_feed.get(key, {})
                    ltp = existing.get('ltp', 0)
                    vol = existing.get('volume', 0)
                    best_bid = existing.get('best_bid', "WAIT...")
                    best_ask = existing.get('best_ask', "WAIT...")
                    day_high = existing.get('high', 0)
                    day_low = existing.get('low', 0)
                    atp = existing.get('atp', 0)
                    tbq = existing.get('tbq', 0)
                    tsq = existing.get('tsq', 0)
                    prev_close = existing.get('prev_close', 0)
                    
                mff = {}
                ff = feed_data.get('fullFeed', feed_data.get('ff', {}))
                if ff:
                    mff = ff.get('marketFF', ff.get('indexFF', {}))
                    sys_print("---")
                    sys_print(f"🔍 [WS RAW] Key: {key}")
                    sys_print(f"🔍 [WS FULL MFF KEYS] {list(mff.keys())}")
                    sys_print(f"🔍 [WS OHLC v2] {mff.get('ohlc') or mff.get('marketOHLC') or ff.get('ohlc', 'NO OHLC')}")
                    sys_print(f"🔍 [WS LTP] {mff.get('ltpc', 'NO LTP')}")
                    sys_print(f"🔍 [WS OHLC] {mff.get('marketOHLC', 'NO OHLC')}")
                    sys_print(f"🔍 [WS VOL] {mff.get('vtt', 'NO VOL')}")
                    sys_print(f"🔍 [WS BID/ASK] {mff.get('marketLevel', 'NO MARKET LEVEL')}")
                    sys_print(f"🔍 [WS ATP] {mff.get('atp', 'NO ATP')}")
                    sys_print(f"🔍 [WS TBQ/TSQ] BuyQ:{mff.get('tbq','N/A')} SellQ:{mff.get('tsq','N/A')}")
                    
                    if mff.get('ltpc'): ltp = mff['ltpc'].get('ltp', 0)
                    if mff.get('vtt'): vol = mff.get('vtt', 0)
                    if mff.get('atp'): atp = mff.get('atp', 0)
                    if mff.get('tbq'): tbq = mff.get('tbq', 0)
                    if mff.get('tsq'): tsq = mff.get('tsq', 0)
                    
                    market_level = mff.get('marketLevel', {})
                    if market_level:
                        quotes = market_level.get('bidAskQuote', [])
                        if quotes and len(quotes) > 0:
                            top_quote = quotes[0]
                            bb = top_quote.get('bidP', 0)
                            ba = top_quote.get('askP', 0)
                            if bb > 0: best_bid = bb
                            if ba > 0: best_ask = ba
                    
                    market_ohlc_list = mff.get('marketOHLC', {}).get('ohlc', [])
                    for ohlc_item in market_ohlc_list:
                        if ohlc_item.get('interval') == '1d':
                            day_high = ohlc_item.get('high', day_high)
                            day_low = ohlc_item.get('low', day_low)
                            break

                if not ltp:
                    ltp = feed_data.get('ltpc', {}).get('ltp', 0)
                    sys_print(f"🔍 [WS HIGH] {day_high} | LOW: {day_low}")

                if ltp and ltp > 0:
                    sys_print(f"✅ TICK: {key} | LTP: {ltp} | PrevClose: {prev_close} | ATP: {atp} | BuyQty: {tbq}")
                    with live_feed_lock:
                        if key not in live_feed:
                            live_feed[key] = {}
                        live_feed[key].update({
                            'ltp': ltp, 'volume': vol, 'best_bid': best_bid, 'best_ask': best_ask,
                            'high': day_high, 'low': day_low, 'atp': atp, 'tbq': tbq, 'tsq': tsq,
                            'prev_close': prev_close
                        })
        
            except Exception as e:
                sys_print(f"⚠️ [on_ws_message] Feed Key {key} Error: {e}")
    except Exception as e:
        sys_print(f"❌ [on_ws_message] WS ERROR: {e}")
        
def on_ws_error(ws, error):
    sys_print(f"❌ [HEARTBEAT] WS Error: {error}")

def on_ws_close(ws, close_status_code, close_msg):
    sys_print("⚠️ Real WebSocket Connection Closed. Reconnecting...")

def on_ws_open(ws):
    global STOCKS, INDICES_LIST
    sys_print("✅ REAL WebSocket Connected! Sending Final Sub...")
    with stocks_lock:
        stock_tokens = [s["token"] for s in STOCKS if s.get("token")]
    all_tokens = [ind["token"] for ind in INDICES_LIST] + stock_tokens
    sys_print(f"🔍 TOKENS: {all_tokens[:3]}")
    
    sub_req = {
        "guid": str(uuid.uuid4()),
        "method": "sub",
        "data": {
            "mode": "full",
            "instrumentKeys": all_tokens
        }
    }
    ws.send(json.dumps(sub_req).encode('utf-8'), opcode=websocket.ABNF.OPCODE_BINARY)
    sys_print(f"🚀 Subscribed to {len(all_tokens)} Stocks & Indices")

def start_websocket_thread():
    global system_active, sws
    
    while system_active:
        try:
            sys_print("🌐 Requesting Authorized Upstox WSS URL (V3 Update)...")
            
            auth_url = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
            res = requests.get(auth_url, headers=upstox_headers, timeout=10)
            
            if res.status_code == 200:
                ws_url = res.json()['data']['authorizedRedirectUri']
                sws = websocket.WebSocketApp(
                    ws_url, on_open=on_ws_open, on_message=on_ws_message,
                    on_error=on_ws_error, on_close=on_ws_close
                )
                sws.run_forever()
            else:
                sys_print(f"❌ WS Auth API Failed: {res.text[:100]}")
                
        except Exception as e:
            sys_print(f"❌ [start_websocket_thread] WS THREAD CRASH: {e}")
            
        time.sleep(5) 

sys_print("💓 [HEARTBEAT] END OF PART 2")
# --- 5. MAIN LOGIC ENGINE ---
def fetch_offline_prices():
    global STOCKS, INDICES_LIST, live_feed

    with stocks_lock:
        stocks_snap = list(STOCKS)

    valid_tokens = [s['token'] for s in stocks_snap if s.get('token') and "NSE_EQ|" in s['token']]
    index_tokens = [ind['token'] for ind in INDICES_LIST if ind.get('token')]
    
    all_tokens_to_fetch = valid_tokens + index_tokens
    if not all_tokens_to_fetch: 
        sys_print("⚠️ [DEBUG] No valid tokens found for offline fetch.")
        return
    
    try:
        encoded_tokens = ",".join([urllib.parse.quote(t) for t in all_tokens_to_fetch])
        url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded_tokens}"
        
        sys_print(f"🔍 [DEBUG] Fetching offline data (Including Indices)...")
        res = safe_api_call(url)
        
        if res and res.get('status') == 'success' and 'data' in res:
            updated_count = 0

            with stocks_lock:
                for s in STOCKS:
                    expected_key_token = s['token']
                    expected_key_symbol = f"NSE_EQ:{s['name']}"
                    
                    quote = res['data'].get(expected_key_token) or res['data'].get(expected_key_symbol)
                    
                    if quote:
                        last_p = quote.get('last_price', 0.0)
                        open_p = quote.get('ohlc', {}).get('open', 0.0)
                        ohlc = quote.get('ohlc', {})
                        prev_close = ohlc.get('prev_close', ohlc.get('close', 0.0))
                        
                        s["price"] = f"{last_p:.2f}"
                        s["change"] = f"{(last_p - prev_close):+.2f}" if prev_close > 0 else "0.00" 
                        
                        tk = s.get('token')
                        if tk:
                            with live_feed_lock:
                                if tk not in live_feed: live_feed[tk] = {}
                                live_feed[tk].update({'ltp': last_p, 'open': open_p, 'prev_close': prev_close})
                        updated_count += 1

            if mongo_db is not None:
                try:
                    prev_close_data = {}
                    with stocks_lock:
                        for s in STOCKS:
                            tk = s.get('token')
                            if tk:
                                with live_feed_lock:
                                    pc = live_feed.get(tk, {}).get('prev_close', 0)
                                if pc > 0:
                                    prev_close_data[tk] = pc
                    mongo_db["prev_closes"].replace_one(
                        {"_id": "all_prev_closes"},
                        {"_id": "all_prev_closes", "data": prev_close_data, "date": str(datetime.now(timezone.utc).date())},
                        upsert=True
                    )
                    sys_print("✅ [MONGODB] All Prev Closes Saved!")
                except Exception as e:
                    sys_print(f"⚠️ [MONGODB] Prev Close Save Error: {e}")

            for ind in INDICES_LIST:
                tk = ind['token']
                expected_key_token = tk
                expected_key_symbol = tk.replace('|', ':')
                
                quote = res['data'].get(expected_key_token) or res['data'].get(expected_key_symbol)
                prev_close = 0
                if quote:
                    last_p = quote.get('last_price', 0.0)
                    open_p = quote.get('ohlc', {}).get('open', 0.0)
                    prev_close = quote.get('ohlc', {}).get('close', 0.0)
                    with live_feed_lock:
                        if tk not in live_feed: live_feed[tk] = {}
                        live_feed[tk].update({'ltp': last_p, 'open': open_p, 'prev_close': prev_close})
                    sys_print(f"✅ [DEBUG] Index {ind['name']} Loaded: LTP={last_p}, PREV_CLOSE={prev_close}")
                if ind["name"] == "NIFTY" and prev_close > 0:
                    if mongo_db is not None:
                        mongo_db["market_data"].replace_one(
                            {"_id": "nifty"},
                            {"_id": "nifty", "prev_close": prev_close},
                            upsert=True
                        )
            
            sys_print(f"✅ AI TRADER PRO: {updated_count} Stocks & Indices updated successfully!")
        else:
            sys_print(f"⚠️ [DEBUG] Fetch failed or API returned empty data.")
            
    except Exception as e:
        sys_print(f"❌ [fetch_offline_prices] Fetch Error: {e}")

def start_engine():
    global live_feed, market_status, ans1_nifty, ans2_sector, winning_sector_code, tokens_loaded, system_active, confirmed_winner, STOCKS, LIVE_VIX, SMC_MATCHED_STOCKS, PROCESS_LOGS

    sector_last_changed_time = {}

    if mongo_db is not None:
        try:
            nifty_data = mongo_db["market_data"].find_one({"_id": "nifty"})

            prev_closes_data = mongo_db["prev_closes"].find_one({"_id": "all_prev_closes"})
            if prev_closes_data and prev_closes_data.get("date") == str(datetime.now(timezone.utc).date()):
                saved_closes = prev_closes_data.get("data", {})
                with live_feed_lock:
                    for token, pc in saved_closes.items():
                        if token not in live_feed:
                            live_feed[token] = {}
                        live_feed[token]['prev_close'] = pc
                sys_print(f"✅ All Stock Prev Closes Loaded from MongoDB: {len(saved_closes)} tokens")
            else:
                sys_print("⚠️ No prev_close data for today in MongoDB - will fetch fresh.")

            if nifty_data:
                with live_feed_lock:
                    if "NSE_INDEX|Nifty 50" not in live_feed:
                        live_feed["NSE_INDEX|Nifty 50"] = {}
                    live_feed["NSE_INDEX|Nifty 50"]["prev_close"] = nifty_data["prev_close"]
                sys_print(f"✅ Nifty Prev Close Loaded: {nifty_data['prev_close']}")

        except Exception as e:
            sys_print(f"⚠️ [MONGODB] Load Error: {e}")
    
    sys_print("💓 [HEARTBEAT] start_engine() triggered. Loading state...")
    confirmed_winner, loaded_code = load_state()
    winning_sector_code = loaded_code
    if confirmed_winner:
        ans2_sector = confirmed_winner + " SECTOR"
    
    while True:
        try:
            if not system_active:
                market_status = "PAUSED"
                ans1_nifty = "OFF"; ans2_sector = "OFF"
                with process_logs_lock: logs_snap = list(PROCESS_LOGS)
                socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS, "smc_matches": SMC_MATCHED_STOCKS, "logs": logs_snap})
                time.sleep(4) 
                continue

            # --- नवीन: मार्केट चालू आहे की नाही हे तपासणे ---
            mkt_open, mkt_msg = is_market_open()
            if not mkt_open:
                market_status = mkt_msg
                ans1_nifty = "CLOSED"; ans2_sector = "CLOSED"
                with process_logs_lock: logs_snap = list(PROCESS_LOGS)
                socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": STOCKS, "smc_matches": SMC_MATCHED_STOCKS, "logs": logs_snap})
                time.sleep(60) # मार्केट बंद असल्यास दर 1 मिनिटाने चेक करेल
                continue
            # --------------------------------------------------
            
            market_status = "LIVE"

            if not tokens_loaded:
                sys_print("💓 [HEARTBEAT] Tokens not loaded yet. Fetching...")
                fetch_correct_tokens()
                with stocks_lock:
                    valid_cnt = sum(1 for s in STOCKS if s.get("token"))
                if valid_cnt > 0:
                    tokens_loaded = True
                    fetch_offline_prices() 
                    sys_print("💓 [HEARTBEAT] Tokens loaded successfully. Starting WS thread...")
                    sys_print("🚀 Engine Started. Fetching Upstox Live Data...")
                    
                    ws_thread = threading.Thread(target=start_websocket_thread)
                    ws_thread.daemon = True
                    ws_thread.start()
                else:
                    sys_print("💓 [HEARTBEAT] No valid tokens found. Retrying in 5s...")
                    time.sleep(5)
                    continue

            sector_totals = {}
            sector_counts = {}

            with stocks_lock:
                stocks_snap = list(STOCKS)

            for s in stocks_snap:
                tk = s.get("token")
                cat = s.get("cat")
                if tk and cat:
                    with live_feed_lock:
                        feed_snap = live_feed.get(tk, {})
                    ltp = feed_snap.get('ltp', 0)
                    prev_c = feed_snap.get('prev_close', 0)
                    
                    if prev_c > 0 and ltp > 0:
                        pct = ((ltp - prev_c) / prev_c) * 100
                        sector_totals[cat] = sector_totals.get(cat, 0.0) + pct
                        sector_counts[cat] = sector_counts.get(cat, 0) + 1
            
            sector_avgs = {}
            for cat in sector_totals:
                sector_avgs[cat] = sector_totals[cat] / sector_counts[cat]
            
            for ind in INDICES_LIST:
                tk = ind["token"]
                with live_feed_lock:
                    feed_snap = live_feed.get(tk, {})
                l = feed_snap.get('ltp', 0)
                prev_c = feed_snap.get('prev_close', 0)
                    
                if ind["name"] == "INDIA_VIX" and l > 0:
                    if l < 100: 
                        LIVE_VIX = l
                
                if ind["name"] == "NIFTY" and prev_c > 0 and l > 0:
                    points_change = l - prev_c
                    pct_change = (points_change / prev_c) * 100
                    ans1_nifty = f"{points_change:+.2f} ({pct_change:+.2f}%)"
            
            if sector_avgs:
                max_sector = max(sector_avgs, key=sector_avgs.get)
                max_val = sector_avgs[max_sector]

                if confirmed_winner is None:
                    confirmed_winner = max_sector
                    winning_sector_code = confirmed_winner
                    ans2_sector = confirmed_winner + " SECTOR"
                    sector_last_changed_time[confirmed_winner] = time.time()
                    sys_print(f"🏆 Strongest Sector Identified: {confirmed_winner}")
                    save_state(confirmed_winner, winning_sector_code)
                else:
                    curr_val = sector_avgs.get(confirmed_winner, -100.0)
                    last_change = sector_last_changed_time.get(confirmed_winner, 0)
                    if max_sector != confirmed_winner and (max_val - curr_val) > 1.0 and (time.time() - last_change) > 1800:
                        confirmed_winner = max_sector
                        winning_sector_code = confirmed_winner
                        ans2_sector = confirmed_winner + " SECTOR"
                        sector_last_changed_time[confirmed_winner] = time.time()
                        sys_print(f"🔄 Sector Shift! New Winner: {confirmed_winner}")
                        save_state(confirmed_winner, winning_sector_code)
            else:
                ans2_sector = "CALCULATING..."

            with stocks_lock:
                for s in STOCKS:
                    tk = s.get("token")
                    if tk:
                        with live_feed_lock:
                            feed_snap = live_feed.get(tk, {})
                        ltp = feed_snap.get("ltp", 0)
                        prev_c = feed_snap.get("prev_close", 0)
                        if ltp > 0:
                            s["price"] = f"{ltp:.2f}"
                            s["change"] = f"{(ltp - prev_c):+.2f}" if prev_c > 0 else "WAIT..."
                            if s["status_msg"] in ["Initializing...", "Token Found.", "Loaded Offline Prices"]:
                                s["status_msg"] = "Live Tracking Active 🟢"
                        else: 
                            s["price"] = "WAIT..."
                            s["status_msg"] = "Waiting for live feed..."

            with stocks_lock:
                stocks_emit = list(STOCKS)
            with process_logs_lock:
                logs_snap = list(PROCESS_LOGS)
            with smc_lock:
                smc_snap = dict(SMC_MATCHED_STOCKS)

            socketio.emit('update_data', {"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": stocks_emit, "smc_matches": smc_snap, "logs": logs_snap})
            
            time.sleep(1) 

        except Exception as e:
            sys_print(f"❌ [start_engine] Main Engine Error: {e}")
            time.sleep(5)

sys_print("💓 [HEARTBEAT] END OF PART 3")
# --- NEW: FAST PYTHON CHARTS (IN-MEMORY & SMART CACHE) ---
def generate_mpl_chart(symbol, interval):
    global STOCKS
    sys_print(f"💓 [HEARTBEAT] Generating {interval} chart for {symbol}...")
    with stocks_lock:
        token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token: 
        sys_print(f"💓 [HEARTBEAT] Chart fail: No token for {symbol}")
        return "", None

    try:
        if interval == "15m":
            cached_data = fetch_smart_data(token, "FIFTEEN_MINUTE", 10, "15m_chart")
        else:
            cached_data = fetch_smart_data(token, "ONE_HOUR", 30, "1h_chart")
            
        if not cached_data: 
            sys_print(f"💓 [HEARTBEAT] Chart fail: No cached data for {symbol} ({interval})")
            return "", None
            
        cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        df = pd.DataFrame(cached_data, columns=cols)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        
        df = df.dropna()
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna()
        df = df.tail(120)
        
        if df.empty: return "", None

        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()

        ema_plots = [
            mpf.make_addplot(df['EMA_20'], color='#00ffff', width=1.5),
            mpf.make_addplot(df['EMA_50'], color='#FF8C00', width=1.5),
        ]
        
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='#39FF14', down='#FF3131', edge='inherit', wick='inherit', volume='in', ohlc='i')
        s  = mpf.make_mpf_style(marketcolors=mc, facecolor='#000508', edgecolor='#00ffff', figcolor='#000508', gridcolor='#003333', gridstyle='--')
        
        mpf.plot(df, type='candle', style=s, volume=True, addplot=ema_plots,
                 savefig=dict(fname=buf, format='png', dpi=300, bbox_inches='tight', pad_inches=0.1),
                 figsize=(10, 5))
        
        buf.seek(0)
        img_bytes = buf.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        base64_string = f"data:image/png;base64,{img_base64}"
        pil_image = Image.open(io.BytesIO(img_bytes))
        
        return base64_string, pil_image
        
    except Exception as e:
        sys_print(f"⚠️ [generate_mpl_chart] MPL Chart Error {symbol}: {e}")
        return "", None

# ==========================================
# ✅ NEW: TOP-DOWN SECTOR & RS CALCULATOR
# ==========================================
def check_sector_and_rs(symbol, stock_ltp, stock_prev_close):
    global STOCKS, live_feed
    with stocks_lock:
        cat = next((s['cat'] for s in STOCKS if s['name'] == symbol), "NIFTY")
    
    cat_map = {"BANK": "Nifty Bank", "IT": "Nifty IT", "AUTO": "Nifty Auto", "FINANCE": "Nifty Fin Service", "METAL": "Nifty Metal", "PHARMA": "Nifty Pharma"}
    idx_name = cat_map.get(cat, "Nifty 50")
    idx_token = f"NSE_INDEX|{idx_name}"
    
    sector_trend = "SIDEWAYS"
    rs_status = "WEAK"
    
    try:
        sector_daily = fetch_smart_data(idx_token, "ONE_DAY", 35, "1d_ind")
        if sector_daily and len(sector_daily) > 20:
            df_sec = pd.DataFrame(sector_daily, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df_sec['Close'] = pd.to_numeric(df_sec['Close'], errors='coerce')
            sma_20 = df_sec['Close'].rolling(window=20).mean().iloc[-1]
            last_close = df_sec['Close'].iloc[-1]
            sector_trend = "BULLISH" if last_close > sma_20 else "BEARISH"
            
        with live_feed_lock:
            sec_feed = live_feed.get(idx_token, {})
            sec_ltp = sec_feed.get('ltp', 0.0)
            sec_prev = sec_feed.get('prev_close', 0.0)
            
        if sec_ltp > 0 and sec_prev > 0 and stock_ltp > 0 and stock_prev_close > 0:
            current_rs = stock_ltp / sec_ltp
            prev_rs = stock_prev_close / sec_prev
            if current_rs > prev_rs:
                rs_status = "STRONG (Rising)"
            else:
                rs_status = "WEAK (Falling)"
                
    except Exception as e:
        sys_print(f"⚠️ [check_sector_and_rs] Error: {e}")
        
    return sector_trend, rs_status, idx_name

def get_technical_indicators(symbol):
    global STOCKS
    with stocks_lock:
        token = next((s['token'] for s in STOCKS if s['name'] == symbol), None)
    if not token: return "N/A", "N/A", "N/A", 0, 0, "N/A", "N/A"

    try:
        data_15m = fetch_smart_data(token, "FIFTEEN_MINUTE", 5, "15m_ind")
        data_daily = fetch_smart_data(token, "ONE_DAY", 15, "1d_ind")
        
        avg_vol_10d = 0
        daily_trend = "SIDEWAYS"
        
        if data_daily:
            df_daily = pd.DataFrame(data_daily, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df_daily['Volume'] = pd.to_numeric(df_daily['Volume'], errors='coerce')
            df_daily['Close'] = pd.to_numeric(df_daily['Close'], errors='coerce')
            avg_vol_10d = df_daily['Volume'].tail(10).mean()
            
            df_daily['SMA_20'] = df_daily['Close'].rolling(window=20).mean()
            if not df_daily['SMA_20'].dropna().empty:
                last_close = df_daily['Close'].iloc[-1]
                last_sma = df_daily['SMA_20'].iloc[-1]
                daily_trend = "BULLISH" if last_close > last_sma else "BEARISH"
        
        if not data_15m: return "N/A", "N/A", "N/A", avg_vol_10d, 0, "N/A", daily_trend
            
        df = pd.DataFrame(data_15m, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        df['High'] = pd.to_numeric(df['High'], errors='coerce')
        df['Low'] = pd.to_numeric(df['Low'], errors='coerce')
        df = df.dropna()
        if df.empty: return "N/A", "N/A", "N/A", avg_vol_10d, 0, "N/A", daily_trend
            
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_12 - ema_26
        
        df['up_move'] = df['High'] - df['High'].shift(1)
        df['down_move'] = df['Low'].shift(1) - df['Low']
        df['+dm'] = 0.0
        df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), '+dm'] = df['up_move']
        df['-dm'] = 0.0
        df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), '-dm'] = df['down_move']
        
        df['tr0'] = abs(df['High'] - df['Low'])
        df['tr1'] = abs(df['High'] - df['Close'].shift(1))
        df['tr2'] = abs(df['Low'] - df['Close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        
        atr_14 = df['tr'].rolling(14).mean()
        plus_di = 100 * (df['+dm'].rolling(14).mean() / atr_14)
        minus_di = 100 * (df['-dm'].rolling(14).mean() / atr_14)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        df['ADX'] = dx.rolling(14).mean()
        
        atr_10 = df['tr'].rolling(10).mean()
        hl2 = (df['High'] + df['Low']) / 2
        df['Supertrend_LB'] = hl2 - (3 * atr_10)
        df['Supertrend_UB'] = hl2 + (3 * atr_10)
        df['Supertrend_Trend'] = "BEARISH"
        df.loc[df['Close'] > df['Supertrend_LB'], 'Supertrend_Trend'] = "BULLISH"
        
        latest = df.iloc[-1]
        adx_val = latest['ADX'] if not pd.isna(latest['ADX']) else 0
        st_trend = latest['Supertrend_Trend']
        
        return f"{latest['RSI_14']:.2f}", f"{latest['SMA_20']:.2f}", f"{latest['MACD']:.2f}", avg_vol_10d, adx_val, st_trend, daily_trend
    except Exception as e: 
        sys_print(f"⚠️ [get_technical_indicators] Error for {symbol}: {e}")
        return "Error", "Error", "Error", 0, 0, "Error", "Error"

def get_stock_news(symbol):
    try:
        query = urllib.parse.quote(f"{symbol} NSE stock")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('./channel/item')
            if not items: return "📰 No recent news found", "NEUTRAL"
            news_list = []
            for item in items[:3]:
                title = item.find('title')
                if title is not None and title.text:
                    clean = title.text.split(' - ')[0].strip()
                    news_list.append(clean[:100])
            if not news_list: return "📰 No headlines found", "NEUTRAL"
            all_news = " ".join(news_list).lower()
            positive_words = ['surge', 'profit', 'gain', 'up', 'high', 'buy', 'growth', 'strong', 'rise', 'rally', 'boost', 'jump']
            negative_words = ['fall', 'loss', 'down', 'crash', 'sell', 'fraud', 'weak', 'drop', 'decline', 'slump', 'risk', 'warn']
            pos_count = sum(1 for w in positive_words if w in all_news)
            neg_count = sum(1 for w in negative_words if w in all_news)
            if pos_count > neg_count: sentiment = "POSITIVE 📈"
            elif neg_count > pos_count: sentiment = "NEGATIVE 📉"
            else: sentiment = "NEUTRAL ➡️"
            news_text = " | ".join(news_list)
            sys_print(f"✅ [DEBUG NEWS] {symbol} | Sentiment: {sentiment} | Headlines: {news_text[:80]}")
            return f"📰 {news_text}", sentiment
        else:
            return "📰 News fetch failed", "NEUTRAL"
    except Exception as e:
        return "📰 News unavailable", "NEUTRAL"

def get_nifty_pcr():
    global upstox_headers
    try:
        nifty_key = urllib.parse.quote("NSE_INDEX|Nifty 50")
        today = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        days_ahead = 3 - today.weekday()
        if days_ahead <= 0: days_ahead += 7
        expiry = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        chain_url = f"https://api.upstox.com/v2/option/chain?instrument_key={nifty_key}&expiry_date={expiry}"
        chain_res = safe_api_call(chain_url)
        if chain_res and chain_res.get('status') == 'success' and 'data' in chain_res:
            total_put_oi = 0; total_call_oi = 0
            for item in chain_res['data']:
                if 'call_options' in item and 'market_data' in item['call_options']:
                    total_call_oi += item['call_options']['market_data'].get('oi', 0)
                if 'put_options' in item and 'market_data' in item['put_options']:
                    total_put_oi += item['put_options']['market_data'].get('oi', 0)
            if total_call_oi > 0:
                pcr = round(total_put_oi / total_call_oi, 2)
                return f"{pcr} (Put OI: {total_put_oi}, Call OI: {total_call_oi})"
            else: return "N/A (No Call OI)"
        else: return "N/A (API Failed)"
    except Exception as e: return "N/A (Error)"
    return "N/A (Data Unavailable)"

# ==========================================
# 🎯 NEW: 3-LEVEL MTF SMC LOGIC (15m BOS -> 5m OB -> 1m CHOCH)
# ==========================================
def check_3_level_smc(token, live_ltp):
    try:
        # सोमवार आणि सुट्ट्यांमुळे आपण मागील 10, 5 आणि 4 दिवसांचा डेटा मागत आहोत
        data_15m = fetch_smart_data(token, "FIFTEEN_MINUTE", 10, "15m_chart")
        data_5m  = fetch_smart_data(token, "FIVE_MINUTE", 5, "5m_chart")
        data_1m  = fetch_smart_data(token, "ONE_MINUTE", 4, "1m_chart")

        if not data_15m or len(data_15m) < 6 or not data_5m or len(data_5m) < 6 or not data_1m or len(data_1m) < 3:
            return False, "HOLD", "Not enough MTF data for analysis"

        cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        
        # ✅ सुरक्षित डेटा कन्व्हर्जन (Fixing "invalid error value specified")
        def clean_df(data):
            df = pd.DataFrame(data, columns=cols)
            for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[c] = pd.to_numeric(df[c], errors='coerce')
            return df.dropna()

        df_15 = clean_df(data_15m)
        df_5  = clean_df(data_5m)
        df_1  = clean_df(data_1m)

        # ✅ पायरी १: HTF ट्रेंड चेक (१५ मिनिटे) - BOS
        recent_15m = df_15.tail(6).copy()
        htf_trend = "SIDEWAYS"
        if recent_15m['Close'].iloc[-1] > recent_15m['High'].iloc[-3]: 
            htf_trend = "BUY"
        elif recent_15m['Close'].iloc[-1] < recent_15m['Low'].iloc[-3]:
            htf_trend = "SELL"
            
        if htf_trend == "SIDEWAYS":
            return False, "HOLD", "L1 Failed: No clear 15m BOS Trend"

        # ✅ पायरी २: MTF सेटअप झोन (५ मिनिटे) - OB/FVG
        recent_5m = df_5.tail(10).copy()
        in_zone = False
        zone_type = ""
        
        if htf_trend == "BUY":
            support_zone = recent_5m['Low'].min()
            if live_ltp <= (support_zone * 1.01) and live_ltp >= (support_zone * 0.99): 
                in_zone = True; zone_type = "Demand Zone (OB/FVG)"
        elif htf_trend == "SELL":
            resist_zone = recent_5m['High'].max()
            if live_ltp >= (resist_zone * 0.99) and live_ltp <= (resist_zone * 1.01): 
                in_zone = True; zone_type = "Supply Zone (OB/FVG)"

        if not in_zone:
            return False, "HOLD", f"L2 Failed: Price not in 5m {zone_type}"

        # ✅ पायरी ३: RELAXED एन्ट्री कन्फर्मेशन (१ मिनिट) - फक्त कॅन्डलचा रंग चेक करणे
        recent_1m = df_1.tail(3).copy()
        choch_detected = False
        
        if htf_trend == "BUY":
            # कडक CHOCH नियम काढला! आता फक्त १-मिनिटाची कॅन्डल हिरवी (Green) असली तरी पास.
            if recent_1m['Close'].iloc[-1] >= recent_1m['Open'].iloc[-1]: choch_detected = True
        elif htf_trend == "SELL":
            # कडक CHOCH नियम काढला! आता फक्त १-मिनिटाची कॅन्डल लाल (Red) असली तरी पास.
            if recent_1m['Close'].iloc[-1] <= recent_1m['Open'].iloc[-1]: choch_detected = True

        if not choch_detected:
            return False, "HOLD", f"L3 Failed: Waiting for Green/Red 1m Candle in {zone_type}"

        return True, htf_trend, f"🎯 3-LEVEL MTF PASSED: 15m BOS -> 5m {zone_type} -> 1m Reversal Confirmed"

    except Exception as e:
        return False, "HOLD", f"❌ MTF Error: {e}"

# --- CORE ANALYSIS FUNCTION ---
def run_smart_analysis(symbol, is_cron=False):
    global PRECALCULATED_DATA, live_feed, STOCKS, LIVE_VIX, INDICES_LIST, current_key_index, GEMINI_KEYS, SMART_CACHE, SMC_MATCHED_STOCKS
    
    sys_print(f"💓 [HEARTBEAT] run_smart_analysis started for {symbol} (is_cron={is_cron})")

    with stocks_lock:
        s = next((x for x in STOCKS if x["name"] == symbol), None)
    if not s: 
        sys_print(f"💓 [HEARTBEAT] Symbol {symbol} not found in STOCKS.")
        return None

    if s: s["status_msg"] = "⚙️ Checking Math Rules..."
    if not is_cron: sys_print(f"⚙️ {symbol}: Manual scan requested. Checking Math Filters...")

    nifty_pct = 0.0
    n_tk = next((i["token"] for i in INDICES_LIST if i["name"] == "NIFTY"), None)
    if n_tk:
        with live_feed_lock:
            nifty_snap = live_feed.get(n_tk, {})
        l = nifty_snap.get('ltp', 0)
        p_c = nifty_snap.get('prev_close', 0)
        if p_c > 0: nifty_pct = ((l - p_c) / p_c) * 100

    stock_token = s.get("token")
    live_ltp = 0.0; prev_close = 0.0; live_low = 0.0; live_vol = 0
    live_high = 0.0; live_bid = "N/A"; live_ask = "N/A"; stock_pct = 0.0 
    day_open = 0.0

    if stock_token:
        with live_feed_lock:
            f = live_feed.get(stock_token, {})
        live_ltp   = f.get("ltp",       0.0)
        prev_close = f.get("prev_close", 0.0)
        live_low   = f.get("low",        0.0)
        live_high  = f.get("high",       0.0)
        live_vol   = f.get("volume",     0)
        live_bid   = f.get("best_bid",   "N/A")
        live_ask   = f.get("best_ask",   "N/A")
        day_open   = f.get("open",       0.0)
        if prev_close > 0: stock_pct = ((live_ltp - prev_close) / prev_close) * 100

    prev_day_high = "N/A"
    prev_day_low  = "N/A"
    try:
        with cache_lock:
            cached_daily = _get_cache_data("1d_ind", stock_token)
        if len(cached_daily) >= 2:
            prev_day_high = cached_daily[-2][2]
            prev_day_low  = cached_daily[-2][3]
    except Exception as e:
        sys_print(f"⚠️ [run_smart_analysis] Prev Day High/Low Error for {symbol}: {e}")

    live_news, news_sentiment = get_stock_news(symbol)
    sys_print(f"🔍 [DEBUG NEWS] {symbol} | News: {live_news[:60]} | Sentiment: {news_sentiment}")
    nifty_pcr = get_nifty_pcr()
    sys_print(f"🔍 [DEBUG PCR] {symbol} | PCR: {nifty_pcr}")

    sys_print(f"💓 [HEARTBEAT] Getting indicators for {symbol}...")
    rsi_val, sma_val, macd_val, avg_vol_10d, adx_val, st_trend, daily_trend = get_technical_indicators(symbol)

    sector_trend, rs_status, idx_name = check_sector_and_rs(symbol, live_ltp, prev_close)
    sys_print(f"🔍 [DEBUG SECTOR & RS] {symbol} | Sector ({idx_name}): {sector_trend} | RS: {rs_status}")

    # ✅ THE NEW 3-LEVEL MTF GATEKEEPER IMPLEMENTATION
    math_smc_passed, math_smc_direction, math_msg = check_3_level_smc(stock_token, live_ltp)

    filter_passed    = True
    rejection_reason = ""

    # === X-RAY DEBUG PRINT (सगळे आकडे तपासा) ===
    sys_print(f"📊 [X-RAY] {symbol} | SMC: {math_smc_direction} (Pass:{math_smc_passed}) | ADX: {adx_val:.2f} | Daily: {daily_trend} | Supertrend: {st_trend} | Sector: {sector_trend} | RS: {rs_status}")
    # ==========================================

    # ==========================================
    # 🚪 THE 9-LEVEL GATEKEEPER APPROACH (PURE MATH LOGIC)
    # ==========================================
    if live_ltp <= 0 or prev_close <= 0:
        filter_passed    = False
        rejection_reason = "Live data not ready yet"
    elif rsi_val == "N/A" or rsi_val == "Error":
        filter_passed    = False
        rejection_reason = "Gate 0: API Data Loading... (Wait/Re-analyze)"
    elif LIVE_VIX > 25: 
        filter_passed    = False
        rejection_reason = f"VIX > 25 ({LIVE_VIX:.2f})"
        
    # ✅ FIX: SMC Logic (Gate 3) सर्वात आधी चेक करा! (यामुळे फ्लक्चुएशन थांबेल)
    elif not math_smc_passed:
        filter_passed    = False
        rejection_reason = f"Gate 3 Failed: MTF SMC Logic ({math_msg})"
        
    # ✅ त्यानंतर ADX (Gate 2) चेक करा
    elif adx_val < 25:
        filter_passed    = False
        rejection_reason = f"Gate 2 Failed: ADX is Weak ({adx_val:.2f} < 25)"
        
    #elif math_smc_direction == "BUY" and daily_trend != "BULLISH":
        #filter_passed    = False
        #rejection_reason = f"Gate 1 Failed: Daily Trend ({daily_trend}) contradicts BUY"
    #elif math_smc_direction == "SELL" and daily_trend != "BEARISH":
        #filter_passed    = False
        #rejection_reason = f"Gate 1 Failed: Daily Trend ({daily_trend}) contradicts SELL"
    #elif st_trend != math_smc_direction:
        #filter_passed    = False
        #rejection_reason = f"Gate 4 Failed: Supertrend ({st_trend}) not aligning with {math_smc_direction}"
    elif math_smc_direction == "BUY" and sector_trend != "BULLISH":
        filter_passed    = False
        rejection_reason = f"Gate 5 Failed: Sector ({idx_name}) Trend ({sector_trend}) contradicts BUY"
    elif math_smc_direction == "SELL" and sector_trend != "BEARISH":
        filter_passed    = False
        rejection_reason = f"Gate 5 Failed: Sector ({idx_name}) Trend ({sector_trend}) contradicts SELL"
    elif math_smc_direction == "BUY" and "WEAK" in rs_status:
        filter_passed    = False
        rejection_reason = f"Gate 6 Failed: Relative Strength is {rs_status} for BUY"
    elif math_smc_direction == "SELL" and "STRONG" in rs_status:
        filter_passed    = False
        rejection_reason = f"Gate 6 Failed: Relative Strength is {rs_status} for SELL"
    
    fallback_data = {
        "status": "success",  
        "charts": {"15m": "", "1h": ""},
        "ai_analysis": f"<span style='color:var(--accent-red); font-weight:800;'>🚨 MATH FILTER FAILED!</span><br><span style='font-size:12px; font-weight:normal; color:#E0E0E0;'>Reason: {rejection_reason}</span>",
        "ai_analysis_2": "Skipped. No chart generation needed.",
        "ai_analysis_3": "Skipped. Waiting for perfect setup.",
        "ai_signal": "HOLD (SKIPPED)", "ai_table_data": "0|0|0|0|0%",
        "ai_smc_data": "Skipped.",
        "ai_trap_data": "Skipped."
    }

    if not filter_passed:
        if s: s["status_msg"] = f"⛔ Filtered: {rejection_reason.split(':')[0]}"
        sys_print(f"⛔ {symbol} FILTERED: {rejection_reason}")
        return fallback_data

    if s: s["status_msg"] = "🧠 Processing AI Explanation..."
    sys_print(f"✅ {symbol}: PASSED ALL 9 GATES! Generating Explanations via AI...")
    
    c15_b64, img15_pil = generate_mpl_chart(symbol, "15m")
    c1h_b64, img1h_pil = generate_mpl_chart(symbol, "1h")
    sys_print(f"🔍 [DEBUG CHART] {symbol} | 15m chart: {'✅ OK' if c15_b64 else '❌ EMPTY'} | 1h chart: {'✅ OK' if c1h_b64 else '❌ EMPTY'}")

    warning_html = f"<span style='color:var(--accent-green); font-weight:800;'>✅ ALGO APPROVED: {math_smc_direction} SIGNAL CONFIRMED!</span><br><br>"

    valid_keys = [k for k in GEMINI_KEYS if k]
    
    if not valid_keys:
        sys_print(f"❌ {symbol}: No valid Gemini API Keys found!")
        if s: s["status_msg"] = "❌ No API Keys"
        return fallback_data
        
    sys_print("💓 [HEARTBEAT] END OF PART 4")
    
    try:
        sys_print(f"💓 [HEARTBEAT] Calling Gemini AI for {symbol}...")
        
        prompt_text = f"""
        You are an expert stock market technical analyst. 
        Analyze these two charts (15-minute and 1-hour) for the stock '{symbol}'.
        
        [IMPORTANT: PURE MATH SIGNAL DETECTED]
        - The Python Algorithm has already verified and confirmed a '{math_smc_direction}' signal based on Daily Trend, ADX > 25, Supertrend, SMC Sweep rules, Sector Alignment ({idx_name}: {sector_trend}), and Relative Strength ({rs_status}).
        - Your job is NOT to find the signal. Your job is ONLY to EXPLAIN the charts logically in Marathi.
        
        [LIVE MARKET DATA]
        - Current Price (LTP): {live_ltp}
        - Best Buyer/Seller: {live_bid} / {live_ask}
        - ADX: {adx_val} (Strong Trend)
        - Supertrend: {st_trend}
        - Daily Trend: {daily_trend}
        - Sector Trend: {sector_trend} ({idx_name})
        - Relative Strength: {rs_status}
        - 📰 News Sentiment: {news_sentiment}
        - 🧮 MATH SMC CHECK: {math_msg}
        
        Please answer the following questions strictly in MARATHI language.
        You must split your response into EIGHT sections using EXACTLY the word '---SPLIT---' between them.
        
        SECTION 1 Questions:
        1) दोन्ही चार्ट आणि व्हॉल्यूम डेटा काय सांगत आहेत? (Python ने जो {math_smc_direction} ट्रेंड सांगितलाय, तो कसा योग्य आहे हे स्पष्ट करा).
        2) SMC lines, FVG, OB आणि दिलेले इंडिकेटर्स काय सांगत आहे?
        3) ट्रेंड किती स्ट्राँग आहे?

        ---SPLIT---

        SECTION 2 Questions (Smart Money Concepts, Liquidity & Volume Trap):
        1) या झोन मध्ये येणे आधी किंमतीने मागचा हाय किंवा लो स्वीप करून स्टॉपलॉस उडवले आहेत का?
        2) या झोनच्यावर किंवा खाली रिटेलर्स ना अडकवण्यासाठी काही आमिष (Liquidity) आहे का?
        3) फेक ब्रेकआउटची शक्यता आहे का?

        ---SPLIT---

        SECTION 3 Questions (Final Action):
        येथे entry कुठे घ्यायची, stoploss कुठे आणि target कुठे ठेवायचे ते सांगा. 10000 capital वर risk-reward किती असेल ते extreme detail मध्ये सांगा.

        ---SPLIT---

        SECTION 4 (Signal Override):
        Do not calculate your own signal. Strictly output EXACTLY ONE WORD: '{math_smc_direction}'. 
        If you detect an extremely obvious Trap based on visuals that math missed, output 'HOLD (TRAP DETECTED)'.

        ---SPLIT---

        SECTION 5 (Table Data & PROBABILITY):
        Strictly output ONLY precise numerical values separated by a pipe character '|' in this exact order:
        Entry|Stoploss|Target|RiskReward|Probability
        Example: 150.25|148.10|155.00|1:2|85%

        ---SPLIT---

        SECTION 6 (SMC Strategy specific questions):
        Answer these IN EXTREME DETAIL, EXPLAINING THE 'WHY' STRICTLY IN MARATHI language:
        1) मार्केटचा मोठा ट्रेंड काय आहे?
        2) Price support/resistance level वर आली आहे का?
        3) तिथे कोणती महत्त्वाची candle बनली आहे का?
        4) Volume वाढला आहे का?

        ---SPLIT---

        SECTION 7 (Operator Trap Analysis):
        Answer IN EXTREME DETAIL, STRICTLY IN MARATHI language:
        1) हा ब्रेकआऊट 'Fakeout' (खोटा) आहे का? 
        2) व्हॉल्यूममध्ये अचानक 'Abnormal' वाढ झाली आहे का?
        3) लांब Wick/Shadow वाली कॅन्डल बनून Stop Loss Hunting झाले आहे का?

        ---SPLIT---

        SECTION 8 (Discord Alert Flag):
        Since the Python Math Algorithm has already cleared this trade, strictly output the exact word 'ALERT_TRUE'.
        """
        
        contents = [prompt_text]
        if img15_pil and img1h_pil:
            contents.append(img15_pil)
            contents.append(img1h_pil)
        else:
            raise ValueError("Charts missing")

        max_retries = len(valid_keys)
        full_text = ""

        for attempt in range(max_retries):
            try:
                with key_lock:
                    idx = current_key_index % max_retries
                    key = valid_keys[idx]
                    current_key_index = (current_key_index + 1) % max_retries
                
                if s: s["status_msg"] = f"🤖 AI Explaining (Attempt {attempt+1})..."
                
                client = genai.Client(api_key=key)
                response = client.models.generate_content(model='gemma-3-27b-it', contents=contents)
                
                if response and response.text:
                    full_text = response.text
                    sys_print(f"💓 [HEARTBEAT] Gemini API success for {symbol} on attempt {attempt+1}")
                    break
                    
            except Exception as ai_err:
                sys_print(f"⚠️ [run_smart_analysis] API Key {attempt+1} failed for {symbol}: {ai_err}")
                
                if attempt == max_retries - 1:
                    sys_print("🛑 ALL KEYS EXHAUSTED! Taking 5 min cooling break...")
                    time.sleep(300)
                else:
                    time.sleep(1)
                continue       
                
        parts = full_text.split("---SPLIT---")
        
        if len(parts) >= 7:
            ai_signal_raw = parts[3].strip().upper()
            if "TRAP" in ai_signal_raw:
                ai_signal_text = "HOLD (TRAP DETECTED)"
            else:
                ai_signal_text = math_smc_direction
            
            sys_print(f"✨ {symbol}: AI Analysis Complete. Pure Math Signal -> {ai_signal_text}")
            
            t_data = parts[4].strip().split('|')
            prob_raw    = t_data[4].strip() if len(t_data) > 4 else "0%"
            prob_val    = prob_raw.split('(')[0].split(' ')[0].strip() 
            prob_digits = re.sub(r'[^0-9]', '', prob_val)
            prob_val    = prob_digits + "%" if prob_digits else "0%"
            
            result_data = {
                "status": "success",
                "charts": {"15m": c15_b64 if c15_b64 else "", "1h": c1h_b64 if c1h_b64 else ""},
                "ai_analysis":   warning_html + parts[0].strip().replace("\n", "<br>"),
                "ai_analysis_2": parts[1].strip().replace("\n", "<br>"),
                "ai_analysis_3": parts[2].strip().replace("\n", "<br>"),
                "ai_signal":     ai_signal_text,
                "ai_table_data": "|".join(t_data[:4]) + "|" + prob_val,
                "ai_smc_data":   parts[5].strip().replace("\n", "<br>"),
                "ai_trap_data":  parts[6].strip().replace("\n", "<br>")
            }

            if len(PRECALCULATED_DATA) >= 30:
                oldest_key = list(PRECALCULATED_DATA.keys())[0]
                del PRECALCULATED_DATA[oldest_key]
            PRECALCULATED_DATA[symbol] = result_data
            if s: s["status_msg"] = f"✅ Signal: {ai_signal_text}"

            if len(parts) >= 8:
                alert_flag = parts[7].strip().upper()
                if "ALERT_TRUE" in alert_flag and "TRAP" not in ai_signal_text:

                    prob_int = int(prob_digits) if prob_digits else 0
                    if prob_int >= 65:
                        time_now_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%I:%M %p")
                        
                        with smc_lock:
                            SMC_MATCHED_STOCKS[symbol] = {
                                "symbol":       symbol,
                                "time":         time_now_str,
                                "signal":       ai_signal_text,
                                "prob":         prob_val,
                                "smc_dir":      math_smc_direction,
                                "ai_smc_data":  result_data["ai_smc_data"],
                                "ai_trap_data": result_data["ai_trap_data"]
                            }
                        sys_print(f"🟢 {symbol} Added to LIVE SMC SCANNER! (Prob: {prob_val}, Dir: {math_smc_direction})")

                        entry_pr = t_data[0] if len(t_data) > 0 else live_ltp
                        sl_pr    = t_data[1] if len(t_data) > 1 else "N/A"
                        tgt_pr   = t_data[2] if len(t_data) > 2 else "N/A"
                        
                        # ✅ DISCORD WEBHOOK ALERT
                        tg_msg = f"🚀 **AI TRADER PRO - ALGO ALERT** 🚀\n\n" \
                                 f"**Stock:** {symbol}\n**Live Price:** ₹{live_ltp}\n" \
                                 f"**Signal:** {ai_signal_text} 🟢\n" \
                                 f"**Daily Trend:** {daily_trend}\n" \
                                 f"**Sector Trend:** {sector_trend} ({idx_name})\n" \
                                 f"**Relative Strength:** {rs_status}\n" \
                                 f"**ADX Status:** {adx_val:.2f} (Strong)\n" \
                                 f"🔥 **Probability:** {prob_val}\n\n" \
                                 f"✅ *All 9 MTF Gatekeepers Cleared!*\n\n" \
                                 f"🎯 **Entry:** ₹{entry_pr}\n🛑 **Stoploss:** ₹{sl_pr}\n💰 **Target:** ₹{tgt_pr}"
                        threading.Thread(target=send_discord_alert, args=(tg_msg,), daemon=True).start()
                    else:
                        sys_print(f"⚠️ {symbol}: Prob {prob_val} < 65% - Skipping alert")
            
            return result_data
            
    except Exception as e:
        sys_print(f"❌ [run_smart_analysis] AI processing totally failed for {symbol}: {e}")
        if s: s["status_msg"] = "❌ AI Error Occurred"
        fallback_data["ai_analysis"] = warning_html + f"⚠️ AI Error: {e}"
        PRECALCULATED_DATA[symbol] = fallback_data
        return fallback_data

    return None

# --- 6. BACKGROUND CRON JOB ---
def background_ai_cron_job():
    global system_active, STOCKS
    sys_print("💓 [HEARTBEAT] background_ai_cron_job started...")
    sys_print("⚙️ Cron Job Initialized. Running 24/7 Mode...")
    while True:
        if not system_active: 
            time.sleep(15)
            continue
            
        # --- नवीन: मार्केट बंद असेल तर AI स्कॅनिंग थांबवणे ---
        mkt_open, _ = is_market_open()
        if not mkt_open:
            time.sleep(60) # मार्केट बंद असेल तर AI शांत बसेल (दर 1 मिनिटाने चेक करेल)
            continue
        # ----------------------------------------------------
        
        sys_print("💓 [HEARTBEAT] Starting background scan loop over STOCKS...")
        sys_print("🚀 Background Math Scanning Started (Checking 9 MTF Gatekeepers)...") 

        with stocks_lock:
            stocks_snap = list(STOCKS)

        for s in stocks_snap:
            if s: s["status_msg"] = "⏳ Scheduled for Algo Scan"

        with ThreadPoolExecutor(max_workers=5) as executor:
            for s in stocks_snap:
                executor.submit(run_smart_analysis, s["name"], True)
            
        sys_print("💓 [HEARTBEAT] Background scan complete. Sleeping 60s.")
        sys_print("⏳ Scan Round Complete! Resting for 1 Minutes (60 seconds)...")
        time.sleep(1.0)

sys_print("💓 [HEARTBEAT] END OF PART 4.4")
# --- 7. FLASK ROUTES --- 
@app.route('/')
def index():
    with stocks_lock:
        stocks_snap = list(STOCKS)
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=stocks_snap, winner=winning_sector_code)

@app.route('/data')
def data():
    with smc_lock:
        smc_snapshot = dict(SMC_MATCHED_STOCKS)
    with process_logs_lock:
        logs_snap = list(PROCESS_LOGS)
    with stocks_lock:
        stocks_snap = list(STOCKS)
    return jsonify({"status": market_status, "ans1": ans1_nifty, "ans2": ans2_sector, "winner": winning_sector_code, "stocks": stocks_snap, "smc_matches": smc_snapshot, "logs": logs_snap})

@app.route('/debug_signals')
def debug_signals():
    with smc_lock:
        smc_snap = dict(SMC_MATCHED_STOCKS)
    result = {}
    for sym, info in smc_snap.items():
        result[sym] = info.get('signal', 'NO SIGNAL')
    return jsonify(result)
    
@socketio.on('toggle_engine')
def handle_toggle(data):
    global system_active
    system_active = data.get('state', True)
    sys_print("SYSTEM STARTED" if system_active else "SYSTEM STOPPED")

@app.route('/get_chart_screenshot/<symbol>')
def get_chart_screenshot(symbol):
    global system_active, PRECALCULATED_DATA
    if not system_active: return jsonify({"status": "error", "message": "System Paused"})
    if symbol in PRECALCULATED_DATA: return jsonify(PRECALCULATED_DATA[symbol])
    
    result = run_smart_analysis(symbol, is_cron=False)
    if result: return jsonify(result)
    return jsonify({"status": "error", "message": "Analysis Failed."})

sys_print("💓 [HEARTBEAT] END OF PART 5.1 ROUTES")
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
html, body, .stock-list, .page-section, .modal-overlay, .smc-modal-box, #terminal-logs {
    scroll-behavior: smooth;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-y: contain;
    will-change: scroll-position;
}

:root {
    --bg-main: #000508;      
    --bg-card: rgba(1, 12, 20, 0.7); 
    --text-main: #ffffff;    
    --text-muted: #00ffff;   
    --border: #00ffff;       
    --accent-blue: #00ffff;  
    --accent-green: #39FF14; 
    --accent-red: #FF3131;   
    --accent-purple: #962eff; 
    --card-shadow: 0 0 15px rgba(0, 255, 255, 0.15); 
}

* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { 
    background-color: var(--bg-main); color: var(--text-main); 
    font-family: 'Inter', sans-serif; margin: 0; height: 100dvh; 
    display: flex; flex-direction: column; overflow: hidden; font-weight: 700; 
}

.header { padding: 15px 20px; background: rgba(0, 5, 8, 0.9); border-bottom: 1px solid rgba(0, 255, 255, 0.2); display: flex; justify-content: space-between; align-items: center; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.brand { font-size: 20px; font-weight: 800; letter-spacing: 1px; background: linear-gradient(135deg, var(--accent-blue), var(--text-main)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-transform: uppercase; text-shadow: 0 0 10px rgba(0, 255, 255, 0.5); }
.header-controls { display: flex; align-items: center; gap: 10px; }
.status-badge { font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; background: rgba(0, 255, 255, 0.1); color: var(--accent-blue); border: 1px solid var(--accent-blue); letter-spacing: 0.5px; display: flex; align-items: center; gap: 5px; box-shadow: 0 0 5px var(--accent-blue); }
.status-dot { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 8px var(--accent-green); }

.switch { position: relative; display: inline-block; width: 40px; height: 20px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255, 255, 255, 0.1); transition: .4s; border: 1px solid var(--accent-red); }
.slider:before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 2px; background-color: var(--accent-red); transition: .4s; box-shadow: 0 0 5px var(--accent-red); }
input:checked + .slider { background-color: rgba(0, 255, 255, 0.2); border-color: var(--accent-green); }
input:checked + .slider:before { transform: translateX(18px); background-color: var(--accent-green); box-shadow: 0 0 5px var(--accent-green); }
.slider.round { border-radius: 20px; }
.slider.round:before { border-radius: 50%; }

.status-bar-marathi { background: rgba(0, 5, 8, 0.8); color: var(--accent-blue); font-size: 11px; text-align: center; padding: 5px; border-bottom: 1px solid var(--border); display: none; font-weight: 800; }

.dashboard-summary { display: flex; gap: 15px; padding: 20px; border-bottom: 1px solid rgba(0, 255, 255, 0.1); background: linear-gradient(180deg, rgba(0, 255, 255, 0.05) 0%, rgba(0,0,0,0) 100%); }
.summary-card { flex: 1; background: var(--bg-card); border: 1px solid rgba(0, 255, 255, 0.3); border-radius: 16px; padding: 15px; display: flex; flex-direction: column; gap: 5px; box-shadow: 0 0 10px rgba(0, 255, 255, 0.1); backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px); }
.summary-label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.8px; }
.summary-value { font-size: 18px; font-weight: 800; text-transform: uppercase; }
.txt-green { color: var(--accent-green); text-shadow: 0 0 10px rgba(57, 255, 20, 0.6); }
.txt-red { color: var(--accent-red); text-shadow: 0 0 10px rgba(255, 49, 49, 0.6); }
.txt-blue { color: var(--accent-blue); text-shadow: 0 0 10px rgba(0, 255, 255, 0.6); }

.filter-bar { padding: 15px 20px; display: flex; gap: 10px; background: var(--bg-main); border-bottom: 1px solid rgba(0, 255, 255, 0.1); overflow-x: auto; scrollbar-width: none; }
.filter-btn { background: rgba(1, 12, 20, 0.8); border: 1px solid rgba(0, 255, 255, 0.3); color: var(--text-muted); padding: 8px 16px; border-radius: 12px; font-size: 12px; font-weight: 700; text-align: center; cursor: pointer; transition: all 0.3s; white-space: nowrap; text-transform: uppercase; }
.filter-btn.active { background: rgba(0, 255, 255, 0.2); color: white; border-color: var(--accent-blue); box-shadow: 0 0 15px rgba(0, 255, 255, 0.4); }

.stock-list { 
    flex: 1; 
    overflow-y: scroll !important; 
    -webkit-overflow-scrolling: touch !important; 
    transform: translateZ(0); 
    padding: 20px; 
    display: flex; 
    flex-direction: column; 
    gap: 12px; 
    overscroll-behavior-y: contain;
    perspective: 1000px;
}

.stock-card { 
    background: var(--bg-card); 
    border: 1px solid rgba(0, 255, 255, 0.4); 
    padding: 18px 20px; 
    border-radius: 16px; 
    display: flex; 
    justify-content: space-between; 
    align-items: center; 
    box-shadow: 0 0 8px rgba(0, 255, 255, 0.2); 
    backdrop-filter: blur(8px); 
    -webkit-backdrop-filter: blur(8px);
    transition: all 0.3s ease; 
    transform: translate3d(0, 0, 0); 
    cursor: pointer; 
    contain: content; 
    content-visibility: auto; 
    contain-intrinsic-size: 80px; 
    will-change: transform;
    backface-visibility: hidden;
}

.st-info { display: flex; flex-direction: column; gap: 4px; }
.st-name { font-size: 16px; font-weight: 800; color: var(--text-main); text-transform: uppercase; }
.st-cat-tag { font-size: 10px; font-weight: 700; color: var(--text-muted); background: rgba(0, 255, 255, 0.1); padding: 3px 8px; border-radius: 6px; width: fit-content; text-transform: uppercase; border: 1px solid rgba(0, 255, 255, 0.2); }
.st-price-box { text-align: right; }
.st-price { font-size: 20px; font-weight: 800; color: var(--accent-green); text-shadow: 0 0 10px rgba(57, 255, 20, 0.6); }
.st-wait { font-size: 14px; font-weight: 600; color: var(--text-muted); }
.st-change { font-size: 12px; font-weight: 600; margin-top: 2px; }

.smc-container { display: none; flex-direction: column; gap: 15px; padding: 20px; }
.gemini-card { background: rgba(150, 46, 255, 0.1); border: 1px solid var(--accent-purple); border-radius: 12px; padding: 15px; text-align: left; box-shadow: 0 0 10px rgba(150, 46, 255, 0.2); margin-bottom: 10px; }
.gemini-title { font-size: 14px; font-weight: 800; color: var(--accent-purple); display: flex; align-items: center; gap: 5px; margin-bottom: 8px; text-transform: uppercase; }
.gemini-text { font-size: 12px; color: #E0E0E0; line-height: 1.5; font-weight: 600; }

.prob-circle {
    width: 55px; height: 55px;
    border-radius: 50%;
    display: flex; justify-content: center; align-items: center;
    font-size: 14px; font-weight: 900; color: #fff;
    border: 2px solid var(--accent-blue);
    box-shadow: 0 0 12px var(--accent-blue), inset 0 0 10px rgba(0, 255, 255, 0.3);
    background: rgba(0, 255, 255, 0.05);
    text-shadow: 0 0 5px rgba(255,255,255,0.8);
}
.prob-circle.green { border-color: var(--accent-green); box-shadow: 0 0 12px var(--accent-green), inset 0 0 10px rgba(57, 255, 20, 0.3); background: rgba(57, 255, 20, 0.05); }
.prob-circle.orange { border-color: #FF8C00; box-shadow: 0 0 12px #FF8C00, inset 0 0 10px rgba(255, 140, 0, 0.3); background: rgba(255, 140, 0, 0.05); }
.prob-circle.red { border-color: var(--accent-red); box-shadow: 0 0 12px var(--accent-red), inset 0 0 10px rgba(255, 49, 49, 0.3); background: rgba(255, 49, 49, 0.05); }

.modal-overlay { 
    position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
    background: rgba(0, 5, 8, 0.9); z-index: 2000; display: flex; justify-content: center; align-items: flex-start; 
    padding-top: 40px; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    overflow-y: scroll !important; 
    -webkit-overflow-scrolling: touch !important; 
    transform: translateZ(0);
}
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

.smc-modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 5, 8, 0.95); z-index: 3000; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(15px); -webkit-backdrop-filter: blur(15px); }
.smc-modal-box { 
    background: rgba(10, 0, 20, 0.9); width: 90%; max-width: 400px; padding: 25px; border-radius: 20px; border: 1px solid var(--accent-purple); box-shadow: 0 0 30px rgba(150, 46, 255, 0.3); animation: popin 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); display: flex; flex-direction: column; gap: 15px; max-height: 85vh; position: relative;
    overflow-y: scroll !important; 
    -webkit-overflow-scrolling: touch !important; 
    transform: translateZ(0); 
}

.page-section { 
    display: none; flex: 1; 
    overflow-y: scroll !important; 
    -webkit-overflow-scrolling: touch !important; 
    transform: translateZ(0); 
    padding: 20px; flex-direction: column; gap: 12px; 
    overscroll-behavior-y: contain;
}
.page-section.active { display: flex; }

.empty-state { text-align: center; color: var(--text-muted); padding: 40px 20px; font-size: 14px; font-weight: 600; }

.bottom-nav { height: 70px; background: rgba(0, 5, 8, 0.95); border-top: 1px solid rgba(0, 255, 255, 0.2); display: flex; justify-content: space-around; align-items: center; padding-bottom: 10px; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
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
let currentWinner = "{{ winner }}";
let activeFilter = "ALL";
let smcActiveTab = 'BUY'; 

let stockDataCache = {}; 
window.lastKnownData = null; 

// ✅ NEW: Auto-scroll toggle logic
let autoScrollEnabled = true;

function toggleAutoScroll() {
    autoScrollEnabled = !autoScrollEnabled;
    const btn = document.getElementById('auto-scroll-btn');
    if(btn) {
        btn.innerHTML = autoScrollEnabled ? "⏸️ Pause Scroll" : "▶️ Resume Scroll";
        btn.style.color = autoScrollEnabled ? "var(--accent-red)" : "var(--accent-green)";
        btn.style.borderColor = autoScrollEnabled ? "var(--accent-red)" : "var(--accent-green)";
        
        if(autoScrollEnabled) {
            const logContainer = document.getElementById('terminal-logs');
            if(logContainer) logContainer.scrollTop = logContainer.scrollHeight;
        }
    }
}

function filterStocks(type) {
    activeFilter = type;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    if(document.getElementById('btn-'+type)) {
        document.getElementById('btn-'+type).classList.add('active');
    }
    
    const normalList = document.getElementById('normal-stock-list');
    const smcContainer = document.getElementById('smc-container');
    
    if(type === 'SMC') {
        if(normalList) normalList.style.display = 'none';
        if(smcContainer) smcContainer.style.display = 'flex';
        if(window.lastKnownData) updateDashboard(window.lastKnownData);
    } else {
        if(normalList) normalList.style.display = 'block';
        if(smcContainer) smcContainer.style.display = 'none';
        applyFilter();
    }
}

function switchSMCTab(tab) {
    smcActiveTab = tab;
    document.querySelectorAll('.smc-tab').forEach(t => t.classList.remove('active'));
    if(document.getElementById('smc-tab-' + tab.toLowerCase())) {
        document.getElementById('smc-tab-' + tab.toLowerCase()).classList.add('active');
    }
    if (window.lastKnownData) updateDashboard(window.lastKnownData);
}

function openSMCPopup(sym) {
    if (!window.lastKnownData || !window.lastKnownData.smc_matches || !window.lastKnownData.smc_matches[sym]) return;
    
    let info = window.lastKnownData.smc_matches[sym];
    document.getElementById('smc-popup-name').innerText = sym;
    
    let badge = document.getElementById('smc-popup-badge');
    badge.innerText = info.signal;
    badge.className = 'signal-badge ' + (info.signal.includes('BUY') ? 'signal-buy' : (info.signal.includes('SELL') ? 'signal-sell' : 'signal-hold'));
    badge.style.display = 'inline-block';
    
    document.getElementById('smc-popup-time').innerText = "AI DETECTED AT: " + info.time;
    document.getElementById('smc-popup-smc-text').innerHTML = info.ai_smc_data;
    document.getElementById('smc-popup-trap-text').innerHTML = info.ai_trap_data;
    
    document.getElementById('smc-modal-overlay').classList.remove('hidden');
}

function closeSMCPopup() {
    document.getElementById('smc-modal-overlay').classList.add('hidden');
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

socket.on('new_process_log', function(data) {
    const logContainer = document.getElementById('terminal-logs');
    if(logContainer) {
        let div = document.createElement('div');
        div.style.marginBottom = '6px';
        div.style.lineHeight = '1.4';
        div.style.borderBottom = '1px solid rgba(0, 255, 255, 0.1)';
        div.style.paddingBottom = '4px';
        
        let msg = data.msg;
        if(msg.includes('❌') || msg.includes('SKIPPED') || msg.includes('Error') || msg.includes('Failed') || msg.includes('🛑') || msg.includes('⚠️')) {
            div.style.color = 'var(--accent-red)';
        } else if(msg.includes('✅') || msg.includes('PASSED') || msg.includes('BUY') || msg.includes('SELL') || msg.includes('🟢') || msg.includes('🏆')) {
            div.style.color = 'var(--accent-green)';
        } else if(msg.includes('⚙️') || msg.includes('🤖') || msg.includes('✨') || msg.includes('🚀')) {
            div.style.color = 'var(--accent-purple)';
        } else {
            div.style.color = 'var(--accent-blue)';
        }
        div.innerText = msg;
        logContainer.appendChild(div);
        
        // ✅ NEW: Auto-scroll logic applied here
        if(autoScrollEnabled) {
            logContainer.scrollTop = logContainer.scrollHeight; 
        }
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
    window.lastKnownData = data; 
    
    // 1. Status Text Update
    const statusText = document.getElementById('status-text');
    if (statusText && statusText.innerText !== data.status) {
        statusText.innerText = data.status;
    }

    // 2. Ans1 (Nifty) Update
    const ans1 = document.getElementById('ans1-disp');
    if (ans1 && ans1.innerHTML !== data.ans1) {
        ans1.innerHTML = data.ans1;
        if(data.ans1.includes("+")) ans1.className = "summary-value txt-green";
        else if(data.ans1.includes("-")) ans1.className = "summary-value txt-red";
        else ans1.className = "summary-value loading-pulse";
    }

    // 3. Ans2 (Sector) Update
    const ans2 = document.getElementById('ans2-disp');
    if (ans2 && ans2.innerText !== data.ans2) {
        ans2.innerText = data.ans2;
        if(data.ans2 !== "LOADING..." && data.ans2 !== "OFF") ans2.className = "summary-value txt-blue";
        else ans2.className = "summary-value loading-pulse";
    }
    
    currentWinner = data.winner;
    
    let activePopupStock = document.getElementById('popup-name') ? document.getElementById('popup-name').innerText : "";
    let isPopupOpen = document.getElementById('modal-overlay') && !document.getElementById('modal-overlay').classList.contains('hidden');

    // 4. Stocks Data Update (LAG FIX: Only update if value changed)
    data.stocks.forEach(s => {
        const priceEl = document.getElementById('price-' + s.name);
        const changeEl = document.getElementById('change-' + s.name);
        const statusEl = document.getElementById('status-' + s.name); 

        if(statusEl && s.status_msg && statusEl.innerText !== s.status_msg) {
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
                if (priceEl.innerText !== s.price) {
                    priceEl.innerText = s.price;
                    priceEl.className = "st-wait loading-pulse";
                }
                if(changeEl && changeEl.innerText !== "") changeEl.innerText = "";
            } else {
                let newPrice = "₹" + s.price;
                if (priceEl.innerText !== newPrice) {
                    priceEl.innerText = newPrice;
                    priceEl.className = "st-price";
                }
                
                if(changeEl && changeEl.innerText !== s.change) {
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
                            if ((entryVal > slVal && ltpVal <= slVal) || (entryVal < slVal && ltpVal >= slVal)) {
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

    // 5. SMC List Container (LAG FIX: Prevent innerHTML rewrite if no change)
    const smcListContainer = document.getElementById('smc-dynamic-list');
    if (smcListContainer && data.smc_matches) {
        let filteredSMC = Object.entries(data.smc_matches).filter(([sym, info]) => {
            let sig = info.signal.toUpperCase();
            if (smcActiveTab === 'BUY') return sig.trim() === 'BUY';
            if (smcActiveTab === 'SELL') return sig.trim() === 'SELL';
            if (smcActiveTab === 'HOLD') return sig.includes('HOLD') || sig.includes('PANIC') || sig.includes('TRAP');
            return true;
        });

        let newHtml = '';
        if (filteredSMC.length > 0) {
            for (const [sym, info] of filteredSMC) {
                let badgeClass = info.signal.includes('BUY') ? 'signal-buy' : (info.signal.includes('SELL') ? 'signal-sell' : 'signal-hold');
                
                let probVal = parseInt(info.prob) || 0;
                let circleColorClass = probVal >= 80 ? 'green' : (probVal >= 60 ? 'orange' : 'red');

                newHtml += `
                <div onclick="openSMCPopup('${sym}')" style="border: 1px solid var(--accent-purple); padding: 15px; border-radius: 12px; margin-bottom: 15px; background: rgba(150, 46, 255, 0.05); box-shadow: 0 0 10px rgba(150, 46, 255, 0.1); cursor:pointer; transition:all 0.3s;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; flex-direction:column; gap:5px;">
                            <span style="font-size:20px; font-weight:800; color:var(--text-main); text-shadow: 0 0 5px var(--accent-blue);">${sym}</span>
                            <span class="signal-badge ${badgeClass}" style="display:inline-block; font-size:11px; padding:4px 10px;">${info.signal}</span>
                            <div style="font-size:10px; font-weight:700; color:var(--text-muted); margin-top:5px; text-transform:uppercase;">AI: ${info.time}</div>
                        </div>
                        <div class="prob-circle ${circleColorClass}">
                            ${probVal}%
                        </div>
                    </div>
                </div>`;
            }
        } else {
            newHtml = `<div class="empty-state">No ${smcActiveTab} signals found currently...<br><br><span class="loading-pulse" style="color:var(--accent-purple);">⚙️ Live AI Scanning Active...</span></div>`;
        }
        
        // Update DOM ONLY if it actually changed
        if (smcListContainer.innerHTML !== newHtml) {
            smcListContainer.innerHTML = newHtml;
        }
    }

    // 6. Terminal Logs
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
            } else if(log.includes('✅') || log.includes('PASSED') || log.includes('BUY') || log.includes('SELL') || log.includes('🟢') || log.includes('🏆')) {
                div.style.color = 'var(--accent-green)';
            } else if(log.includes('⚙️') || log.includes('🤖') || log.includes('✨') || log.includes('🚀')) {
                div.style.color = 'var(--accent-purple)';
            } else {
                div.style.color = 'var(--accent-blue)';
            }
            div.innerText = log;
            logContainer.appendChild(div);
        });
        
        if(autoScrollEnabled) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
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
        <div style="display:flex; gap:10px; margin-bottom: 10px;">
            <div id="smc-tab-buy" class="filter-btn smc-tab active" onclick="switchSMCTab('BUY')" style="color:var(--accent-green); border-color:rgba(57, 255, 20, 0.4); flex:1; text-align:center;">BUY</div>
            <div id="smc-tab-sell" class="filter-btn smc-tab" onclick="switchSMCTab('SELL')" style="color:var(--accent-red); border-color:rgba(255, 49, 49, 0.4); flex:1; text-align:center;">SELL</div>
            <div id="smc-tab-hold" class="filter-btn smc-tab" onclick="switchSMCTab('HOLD')" style="color:var(--accent-blue); border-color:rgba(0, 255, 255, 0.4); flex:1; text-align:center;">HOLD/TRAP</div>
        </div>
        <div id="smc-dynamic-list" style="display:flex; flex-direction:column; gap:10px;">
            <div class="empty-state">
                Waiting for High Probability setups...<br><br>
                <span class="loading-pulse" style="color:var(--accent-purple);">⚙️ Live AI Scanning Active...</span>
            </div>
        </div>
    </div>
</div>

<div id="page-process" class="page-section">
    <div style="font-size:18px; font-weight:800; margin-bottom:10px; color:var(--accent-purple); display:flex; align-items:center; justify-content:space-between; gap:8px;">
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="material-icons-outlined">terminal</span> SYSTEM TERMINAL
        </div>
        <button id="auto-scroll-btn" onclick="toggleAutoScroll()" style="background: transparent; color: var(--accent-green); border: 1px solid var(--accent-green); padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 800; transition: all 0.3s; text-transform: uppercase;">⏸️ Pause Scroll</button>
    </div>
    <div style="background: rgba(0, 0, 0, 0.85); border: 1px solid var(--accent-purple); border-radius: 12px; padding: 15px; height: 100%; min-height: 400px; flex: 1; overflow-y: auto; box-shadow: inset 0 0 15px rgba(0, 0, 0, 1), 0 0 10px rgba(150, 46, 255, 0.2); font-family: monospace; font-size: 12px; letter-spacing: 0.5px;" id="terminal-logs">
        <div style="color: var(--text-muted); margin-bottom:6px; border-bottom: 1px solid rgba(0, 255, 255, 0.1); padding-bottom: 4px;">
            [09:00:00] ⏳ System initialized. Waiting for background processes...
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

<div id="smc-modal-overlay" class="smc-modal-overlay hidden">
    <div class="smc-modal-box">
        <span class="material-icons-outlined modal-close-icon" onclick="closeSMCPopup()">close</span>
        <div class="modal-title">
            <span id="smc-popup-name">STOCK</span>
            <span id="smc-popup-badge" class="signal-badge"></span>
        </div>
        <div id="smc-popup-time" style="font-size:10px; font-weight:700; color:var(--text-muted); margin-bottom:15px; text-transform:uppercase;"></div>
        <div class="gemini-card" style="margin-bottom:10px; border-color:rgba(0, 255, 255, 0.4);">
            <div class="gemini-title"><span class="material-icons-outlined" style="font-size: 18px;">analytics</span>SMC ANALYSIS</div>
            <div id="smc-popup-smc-text" class="gemini-text">Loading...</div>
        </div>
        <div class="gemini-card" style="border-color:rgba(0, 255, 255, 0.4);">
            <div class="gemini-title"><span class="material-icons-outlined" style="font-size: 18px;">radar</span>OPERATOR TRAP ANALYSIS</div>
            <div id="smc-popup-trap-text" class="gemini-text">Loading...</div>
        </div>
    </div>
</div>

<div class="bottom-nav">
    <div class="nav-item active" onclick="switchPage('home', this)">
        <span class="material-icons-outlined nav-icon">home</span>
        <span class="nav-label">Home</span>
    </div>
    <div class="nav-item" onclick="switchPage('process', this)">
        <span class="material-icons-outlined nav-icon">terminal</span>
        <span class="nav-label">Process</span>
    </div>
</div>

</body>
</html>
'''
# 👇 THREADS & SERVER START 👇
sys_print("💓 [HEARTBEAT] Starting Engine Thread...")
t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

sys_print("💓 [HEARTBEAT] Starting Background AI Cron Thread...")
c = threading.Thread(target=background_ai_cron_job)
c.daemon = True
c.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 7860))
    sys_print(f"💓 [HEARTBEAT] Starting Flask SocketIO server on port {port}...")
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
