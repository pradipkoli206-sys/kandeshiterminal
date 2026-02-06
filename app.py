import os
import pyotp
from flask import Flask, render_template_string
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import threading
import time

app = Flask(__name__)
SYSTEM_ERROR = False

# --- 1. KEYS FROM RENDER ENVIRONMENT ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

# --- 2. LIVE DATA STORE ---
live_data = {} 

# --- 3. TOKEN MAPPING ---
TOKEN_MAP = {
    "RELIANCE": "2885",
    "TATASTEEL": "3499",
    "HDFCBANK": "1333",
    "INFY": "1594",
    "SBIN": "3045",
    "ICICIBANK": "4963",
    "AXISBANK": "5900",
    "WIPRO": "3787",
    "ADANIENT": "25",
    "MARUTI": "10999",
    "BAJFINANCE": "317",
    "ASIANPAINT": "236"
}

STOCKS = [
    {"name": "RELIANCE", "price": 0.00, "sig": "WAIT"},
    {"name": "TATASTEEL", "price": 0.00, "sig": "WAIT"},
    {"name": "HDFCBANK", "price": 0.00, "sig": "WAIT"},
    {"name": "INFY", "price": 0.00, "sig": "WAIT"},
    {"name": "SBIN", "price": 0.00, "sig": "NONE"},
    {"name": "ICICIBANK", "price": 0.00, "sig": "NONE"},
    {"name": "AXISBANK", "price": 0.00, "sig": "NONE"},
    {"name": "WIPRO", "price": 0.00, "sig": "NONE"},
    {"name": "ADANIENT", "price": 0.00, "sig": "NONE"},
    {"name": "MARUTI", "price": 0.00, "sig": "NONE"},
    {"name": "BAJFINANCE", "price": 0.00, "sig": "NONE"},
    {"name": "ASIANPAINT", "price": 0.00, "sig": "NONE"}
]

SIGNALS = [
    {"symbol": "SYSTEM", "type": "INFO", "price": "0.00", "time": "WAITING FOR DATA..."}
]

# --- 4. WEBSOCKET ENGINE (DEBUG MODE ADDED) ---
def start_socket():
    global live_data
    print("🚀 Starting Angel One WebSocket on Render...")
    
    if not API_KEY or not CLIENT_ID or not TOTP_KEY:
        print("❌ Error: API Keys not found in Environment Variables!")
        return

    try:
        # Step A: Generate OTP
        print("🔐 Generating TOTP...")
        try:
            totp = pyotp.TOTP(TOTP_KEY).now()
        except Exception as e:
            print(f"❌ TOTP Error: {e} (Check TOTP_KEY in Render)")
            return
        
        # Step B: Login with DEBUG PRINTS
        print(f"📡 Sending Login Request for {CLIENT_ID}...")
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        
        # --- जासूसी ओळ (ही ओळ महत्वाची आहे) ---
        print(f"🕵️ ANGEL ONE SERVER RESPONSE: {data}") 
        # -------------------------------------

        if data['status'] == False:
            print(f"❌ LOGIN FAILED: {data['message']}")
            print(f"⚠️ Error Code: {data['errorcode']}")
            return

        feed_token = obj.getfeedToken()
        
        # Step C: Callbacks
        def on_data(wsapp, msg):
            token = msg.get("token")
            ltp = msg.get("last_traded_price")
            if token and ltp:
                live_data[token] = ltp / 100

        def on_open(wsapp):
            print("✅ WebSocket Connected! (Market Data Incoming)")
            token_list = list(TOKEN_MAP.values())
            wsapp.subscribe("correlation_id", 1, [{"exchangeType": 1, "tokens": token_list}])

        def on_error(wsapp, error):
            print(f"❌ Socket Error: {error}")

        # Step D: Connect
        sws = SmartWebSocketV2(data["data"]["jwtToken"], API_KEY, CLIENT_ID, feed_token)
        sws.on_data = on_data
        sws.on_open = on_open
        sws.on_error = on_error
        sws.connect()
        
    except Exception as e:
        print(f"⚠️ Connection Failed: {e}")

# Start Thread
t = threading.Thread(target=start_socket)
t.daemon = True
t.start()

# --- 5. HTML TEMPLATE (SAME AS YOURS) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 80px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
.header { padding: 10px 15px; background: rgba(10, 17, 24, 0.95); border-bottom: 2px solid var(--neon); flex-shrink: 0; display: flex; justify-content: space-between; align-items: center; }
h1 { margin: 0; text-shadow: 0 0 10px var(--neon); font-size: 1.4rem; letter-spacing: 2px; }
.header-btn { background: transparent; border: 1px solid var(--neon); color: var(--neon); font-size: 0.6rem; font-weight: bold; padding: 8px 10px; border-radius: 5px; cursor: pointer; }
.status-bar { display: flex; justify-content: center; gap: 5px; font-size: 0.75rem; font-weight: bold; color: #ccc; }
.status-item { border: 1px solid var(--neon); padding: 2px 5px; border-radius: 4px; background: rgba(0, 242, 255, 0.1); color: var(--neon); }
.error-active { animation: blink-red 1s infinite; font-weight: 900; border-color: var(--red) !important; color: var(--red) !important; }
@keyframes blink-red { 0%, 100% { background-color: rgba(255, 51, 51, 0.1); } 50% { background-color: var(--red); color: #000; } }
.split-container { display: flex; flex: 1; overflow: hidden; width: 100%; }
.panel { width: 50%; padding: 10px; padding-bottom: 100px; overflow-y: auto; display: flex; flex-direction: column; }
.vertical-separator { width: 2px; background: var(--neon); box-shadow: 0 0 15px var(--neon); height: 100%; }
.title-box { width: fit-content; margin: 0 auto 15px auto; border: 2px solid var(--neon); padding: 5px 15px; text-align: center; font-size: 1rem; font-weight: 900; color: var(--neon); background: rgba(0, 242, 255, 0.1); letter-spacing: 1px; text-transform: uppercase; }
.pro-card { position: relative; background: var(--card); border-radius: 8px; margin-bottom: 8px; padding: 10px 8px; display: flex; justify-content: space-between; align-items: center; border: 1px solid rgba(0, 242, 255, 0.2); box-shadow: 0 0 5px var(--neon); }
.stock-name { font-size: 0.85rem; font-weight: 900; color: var(--neon); text-transform: uppercase; flex: 2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stock-price { font-size: 0.8rem; font-weight: bold; color: var(--green); text-align: right; margin-right: 5px; flex: 1; }
.pin-btn { background: transparent; border: 1px solid var(--neon); color: var(--neon); border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 0.7rem; flex-shrink: 0; }
.signal-card { background: var(--card); border-radius: 8px; padding: 10px; margin-bottom: 10px; border: 1px solid rgba(0, 242, 255, 0.2); box-shadow: 0 0 5px var(--neon); display: flex; flex-direction: column; gap: 10px; }
.sig-top-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.sig-info { display: flex; flex-direction: column; gap: 2px; }
.sig-symbol { font-size: 0.9rem; font-weight: 900; color: #fff; }
.sig-price { font-size: 0.7rem; color: #888; }
.sig-badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.7rem; color: #000; }
.badge-buy { background: var(--green); box-shadow: 0 0 8px var(--green); }
.badge-sell { background: var(--red); box-shadow: 0 0 8px var(--red); }
.details-btn { background: transparent; border: 1px solid var(--neon); color: var(--neon); padding: 5px 15px; border-radius: 4px; font-size: 0.7rem; font-weight: bold; text-transform: uppercase; cursor: pointer; width: 100%; text-align: center; }
.details-btn:hover { background: var(--neon); color: #000; box-shadow: 0 0 8px var(--neon); }
.calc-box { margin-top: 15px; background: rgba(0, 242, 255, 0.05); border: 1px dashed var(--neon); border-radius: 8px; padding: 15px; display: flex; flex-direction: column; gap: 10px; }
.calc-title { color: var(--neon); font-size: 0.9rem; font-weight: 900; text-align: center; margin-bottom: 5px; }
.calc-input, .calc-select { background: #000; border: 1px solid #333; color: #fff; padding: 8px; border-radius: 4px; width: 100%; font-weight: bold; box-sizing: border-box; }
.calc-select:focus, .calc-input:focus { border-color: var(--neon); outline: none; }
.calc-result { background: #222; color: #888; padding: 8px; border-radius: 4px; text-align: center; font-weight: 900; font-size: 0.9rem; margin-top: 5px; border: 1px solid #444; }
.footer { position: fixed; bottom: 0; left: 0; width: 100%; height: 50px; background: rgba(5, 8, 15, 0.95); border-top: 1px solid var(--neon); display: flex; justify-content: space-around; align-items: center; z-index: 2000; box-shadow: 0 -5px 15px rgba(0, 242, 255, 0.1); }
.footer-item { font-size: 0.8rem; font-weight: bold; color: #ccc; text-transform: uppercase; }
.footer-val { color: var(--green); margin-left: 5px; }
.footer-val.red { color: var(--red); }
</style>
</head>
<body>
<div class="header">
<div class="header-left"><button class="header-btn {% if has_error %}error-active{% endif %}">{% if has_error %}⚠️ SYSTEM ERROR{% else %}ERROR LOGS{% endif %}</button></div>
<div class="header-center"><h1>🔱 {{ title }}</h1><div class="status-bar"><div class="status-item" id="date-display">--/--</div><div class="status-item" id="time-display">--:--</div><div class="status-item">LIVE</div></div></div>
<div class="header-right"><button class="header-btn">HISTORY</button></div>
</div>
<div class="split-container">
<div class="panel">
<div class="title-box">WATCHLIST</div>
{% for stock in stocks %}
<div class="pro-card">
<div class="stock-name">{{ stock.name }}</div><div class="stock-price">₹{{ stock.price }}</div><button class="pin-btn" title="Pin">📌</button>
</div>
{% endfor %}
</div>
<div class="vertical-separator"></div>
<div class="panel">
<div class="title-box">SIGNALS</div>
{% for sig in signals %}
<div class="signal-card">
<div class="sig-top-row">
<div class="sig-info"><div class="sig-symbol">{{ sig.symbol }}</div><div class="sig-price">@ {{ sig.price }} | {{ sig.time }}</div></div>
<div class="sig-badge {% if sig.type == 'BUY' %}badge-buy{% elif sig.type == 'SELL' %}badge-sell{% else %}badge-buy{% endif %}">{{ sig.type }}</div>
</div>
<button class="details-btn">CLICK FOR DETAILS</button>
</div>
{% endfor %}
<div class="calc-box">
<div class="calc-title">🔢 QTY CALCULATOR</div>
<input type="number" id="userCapital" class="calc-input" placeholder="Enter Capital (₹)" value="50000" oninput="calculateQty()">
<select id="stockSelect" class="calc-select" onchange="calculateQty()">
<option value="0" data-sig="NONE">-- Select Stock --</option>
{% for stock in stocks %}<option value="{{ stock.price }}" data-sig="{{ stock.sig }}">{{ stock.name }}</option>{% endfor %}
</select>
<div id="calcResult" class="calc-result">SELECT STOCK</div>
</div>
</div>
</div>
<div class="footer">
<div class="footer-item">NIFTY <span class="footer-val">LIVE</span></div>
<div class="footer-item">BANKNIFTY <span class="footer-val red">LIVE</span></div>
</div>
<script>
function updateTime(){
    const now = new Date(); 
    document.getElementById('date-display').innerText = now.toLocaleDateString('en-GB');
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: true});
} 
setInterval(updateTime,1000); 
updateTime();

function calculateQty(){
const cap=document.getElementById('userCapital').value; const select=document.getElementById('stockSelect');
const price=select.value; const sigType=select.options[select.selectedIndex].getAttribute('data-sig');
const res=document.getElementById('calcResult');
if(price=="0"||cap==""){
res.innerText="SELECT STOCK"; res.style.background="#222"; res.style.color="#888"; res.style.boxShadow="none"; res.style.border="1px solid #444"; return;
}
if(sigType==="NONE"){
res.innerText="NO ACTIVE SIGNAL"; res.style.background="#333"; res.style.color="#888"; res.style.boxShadow="none"; res.style.border="1px solid #444"; return;
}
const qty=Math.floor(cap/price); const totalVal=(qty*price).toFixed(2);
let actionText=""; let bgColor=""; let txtColor=""; let shadow="";
if(sigType==="SELL"){ actionText="SELL"; bgColor="var(--red)"; txtColor="#fff"; shadow="0 0 10px var(--red)"; }
else if(sigType==="BUY"){ actionText="BUY"; bgColor="var(--neon)"; txtColor="#000"; shadow="0 0 10px var(--neon)"; }
res.innerHTML=`${actionText} <b>${qty}</b> QTY<br><span style="font-size:0.7rem">Value: ₹${totalVal}</span>`;
res.style.background=bgColor; res.style.color=txtColor; res.style.boxShadow=shadow; res.style.border="none";
}
</script>
</body>
</html>
'''

# --- 6. ROUTE ---
@app.route('/')
def index():
    for stock in STOCKS:
        token = TOKEN_MAP.get(stock["name"])
        if token and token in live_data:
            stock["price"] = live_data[token]
            
    return render_template_string(HTML_TEMPLATE, title="कान्हादेशी ट्रेडर", stocks=STOCKS, signals=SIGNALS, has_error=SYSTEM_ERROR)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
