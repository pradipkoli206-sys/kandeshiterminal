import os
import pyotp
import time
import threading
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

# --- 2. DATA STORE ---
live_data = {} 

# --- 3. TOKEN MAP (Token ani Naav) ---
TOKEN_MAP = {
    "RELIANCE": "2885", "TATASTEEL": "3499", "HDFCBANK": "1333", "INFY": "1594",
    "SBIN": "3045", "ICICIBANK": "4963", "AXISBANK": "5900", "WIPRO": "3787",
    "ADANIENT": "25", "MARUTI": "10999", "BAJFINANCE": "317", "ASIANPAINT": "236"
}

# --- 4. STOCK LIST (Tuzya Original Design sathi structure) ---
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
    {"symbol": "SYSTEM", "type": "INFO", "price": "0.00", "time": "LIVE DATA ON"}
]

# --- 5. ENGINE (Data Load Problem Fixed Here) ---
def start_engine():
    global live_data
    smart_api = None
    
    while True:
        try:
            # Login Logic
            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(5)
                    continue

            # Fetch Data (Polling)
            for name, token in TOKEN_MAP.items():
                try:
                    # Fix: Added "-EQ" here so data loads correctly
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    if res and res['status']:
                        live_data[token] = res['data']['ltp']
                except:
                    pass
                time.sleep(0.05) 
            
            time.sleep(1) # Refresh Rate

        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 6. HTML (TUZI ORIGINAL NEON DESIGN - NO CHANGE) ---
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

// AUTO REFRESH (1.5 Seconds)
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
</body>
</html>
'''

# --- 7. ROUTE ---
@app.route('/')
def index():
    # Data Map Karun Price Update Karne
    for stock in STOCKS:
        token = TOKEN_MAP.get(stock["name"])
        if token and token in live_data:
            stock["price"] = live_data[token]
            
    return render_template_string(HTML_TEMPLATE, title="कान्हादेशी ट्रेडर", stocks=STOCKS, signals=SIGNALS)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
