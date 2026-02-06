import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone # 1. हे नवीन ऍड केले
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..." # 2. स्टेटस ट्रॅक करण्यासाठी

# --- 2. DATA SETUP ---
TOKEN_MAP = {
    "RELIANCE": "2885", "TATASTEEL": "3499", "HDFCBANK": "1333", "INFY": "1594",
    "SBIN": "3045", "ICICIBANK": "4963", "AXISBANK": "5900", "WIPRO": "3787",
    "ADANIENT": "25", "MARUTI": "10999", "BAJFINANCE": "317", "ASIANPAINT": "236"
}

STOCKS = []
for name, token in TOKEN_MAP.items():
    STOCKS.append({"name": name, "token": token, "price": "0.00", "sig": "WAIT"})

SIGNALS = [
    {"symbol": "BANKNIFTY", "type": "BUY", "price": "41500.00", "time": "09:15 AM"},
    {"symbol": "RELIANCE", "type": "SELL", "price": "2450.00", "time": "10:30 AM"},
    {"symbol": "SYSTEM", "type": "INFO", "price": "0.00", "time": "LIVE MODE"}
]

# --- 3. ENGINE (Market Time Logic Added) ---
def start_engine():
    global live_data, market_status
    smart_api = None
    while True:
        try:
            # --- TIME CHECK LOGIC (Day 2 Task) ---
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            weekday = ist_now.weekday() # 0=Mon, 4=Fri, 5=Sat, 6=Sun

            # Market Time: 09:15 to 15:30 (Only Mon-Fri)
            start_time = datetime.strptime("09:15", "%H:%M").time()
            end_time = datetime.strptime("15:30", "%H:%M").time()

            if weekday < 5 and start_time <= current_time <= end_time:
                market_status = "LIVE MARKET"
                # Market चालू आहे, पुढे जा...
            else:
                market_status = "MARKET CLOSED"
                print(f"Market Closed ({ist_now.strftime('%H:%M')}). Sleeping...")
                time.sleep(60) # 1 मिनिट झोपून रहा
                continue # लूप इथेच थांबवा, Login करू नका
            # -------------------------------------

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(5)
                    continue

            for name, token in TOKEN_MAP.items():
                try:
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    if res and res['status']:
                        live_data[token] = res['data']['ltp']
                except:
                    pass
                time.sleep(0.05)
            time.sleep(1)
        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. HTML TEMPLATE ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>कान्हादेशी ट्रेडर</title>
<style>
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 80px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
.header { padding: 10px 15px; background: rgba(10, 17, 24, 0.95); border-bottom: 2px solid var(--neon); flex-shrink: 0; display: flex; justify-content: space-between; align-items: center; }
h1 { margin: 0; text-shadow: 0 0 10px var(--neon); font-size: 1.4rem; letter-spacing: 2px; }
.header-btn { background: transparent; border: 1px solid var(--neon); color: var(--neon); font-size: 0.6rem; font-weight: bold; padding: 8px 10px; border-radius: 5px; cursor: pointer; }
.status-bar { display: flex; justify-content: center; gap: 5px; font-size: 0.75rem; font-weight: bold; color: #ccc; }
.status-item { border: 1px solid var(--neon); padding: 2px 5px; border-radius: 4px; background: rgba(0, 242, 255, 0.1); color: var(--neon); }
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
<script>
function updateTime(){
    const now = new Date(); 
    document.getElementById('date-display').innerText = now.toLocaleDateString('en-GB');
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: true});
} 
setInterval(updateTime,1000); 
updateTime();

// AUTO REFRESH (1.5 Sec)
setInterval(function(){ location.reload(); }, 1500);

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
</head>
<body>
<div class="header">
<div class="header-left"><button class="header-btn">LOGS</button></div>
<div class="header-center"><h1>🔱 {{ title }}</h1><div class="status-bar"><div class="status-item" id="date-display">--/--</div><div class="status-item" id="time-display">--:--</div><div class="status-item">{{ status }}</div></div></div>
<div class="header-right"><button class="header-btn">HISTORY</button></div>
</div>

<div class="split-container">
<div class="panel">
<div class="title-box">WATCHLIST</div>
{% for stock in stocks %}
<div class="pro-card">
<div class="stock-name">{{ stock.name }}</div><div class="stock-price">₹{{ stock.price }}</div><button class="pin-btn">📌</button>
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
<button class="details-btn">DETAILS</button>
</div>
{% endfor %}

<div class="calc-box">
<div class="calc-title">🔢 QTY CALCULATOR</div>
<input type="number" id="userCapital" class="calc-input" placeholder="Enter Capital (₹)" oninput="calculateQty()">
<select id="stockSelect" class="calc-select" onchange="calculateQty()">
<option value="0" data-sig="NONE">-- Select Stock --</option>
{% for stock in stocks %}<option value="{{ stock.price }}" data-sig="BUY">{{ stock.name }}</option>{% endfor %}
</select>
<div id="calcResult" class="calc-result">RESULT</div>
</div>
</div>
</div>

<div class="footer">
<div class="footer-item">NIFTY <span class="footer-val">LIVE</span></div>
<div class="footer-item">BANKNIFTY <span class="footer-val red">LIVE</span></div>
</div>
</body>
</html>
'''

# --- 5. ROUTE ---
@app.route('/')
def index():
    # Update data for HTML
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
            
    # Status pass kela aahe HTML la
    return render_template_string(HTML_TEMPLATE, title="कान्हादेशी ट्रेडर", stocks=STOCKS, signals=SIGNALS, status=market_status)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
