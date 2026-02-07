import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS (तशाच आहेत) ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..."

# --- 2. DATA SETUP (Tujhe 8 Navin Stocks) ---
# NOTE: Pradeep, Angel One chya site varun ya stocks che khare 'Token ID' shodhun ithe update kar.
TOKEN_MAP = {
    "SOUTHBANK": "00000",   # Token ID takava
    "CENTRALBK": "00000",   # Token ID takava
    "UCOBANK": "00000",     # Token ID takava
    "IDFCFIRSTB": "00000",  # Token ID takava
    "RTNINDIA": "00000",    # Token ID takava
    "OLAELEC": "00000",     # Token ID takava
    "TTML": "00000",        # Token ID takava
    "HFCL": "00000"         # Token ID takava
}

STOCKS = []
for name, token in TOKEN_MAP.items():
    STOCKS.append({"name": name, "token": token, "price": "0.00"})

# --- 3. ENGINE (Market Time Logic) ---
def start_engine():
    global live_data, market_status
    smart_api = None
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            weekday = ist_now.weekday()

            start_time = datetime.strptime("09:15", "%H:%M").time()
            end_time = datetime.strptime("15:30", "%H:%M").time()

            if weekday < 5 and start_time <= current_time <= end_time:
                market_status = "LIVE MARKET"
            else:
                market_status = "MARKET CLOSED"
                time.sleep(60)
                continue

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

# --- 4. HTML TEMPLATE (Clean Slate) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>कान्हादेशी AI (Clean)</title>
<style>
/* जुनीच स्टाईल (Neon Theme) */
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 20px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }

/* Header तसाच ठेवला आहे */
.header { padding: 10px 15px; background: rgba(10, 17, 24, 0.95); border-bottom: 2px solid var(--neon); flex-shrink: 0; display: flex; justify-content: space-between; align-items: center; }
h1 { margin: 0; text-shadow: 0 0 10px var(--neon); font-size: 1.2rem; letter-spacing: 1px; }

/* Status Bar */
.status-bar { display: flex; justify-content: center; gap: 5px; font-size: 0.75rem; font-weight: bold; color: #ccc; }
.status-item { border: 1px solid var(--neon); padding: 2px 5px; border-radius: 4px; background: rgba(0, 242, 255, 0.1); color: var(--neon); }

/* Main Area (Clean - रिकामी जागा) */
.main-container { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; color: #333; font-weight: bold; font-size: 1.5rem; border: 1px dashed #333; margin: 20px; border-radius: 10px; }

</style>
<script>
function updateTime(){
    const now = new Date(); 
    document.getElementById('date-display').innerText = now.toLocaleDateString('en-GB');
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: true});
} 
setInterval(updateTime,1000); 
updateTime();

// Auto Refresh (Optional)
setInterval(function(){ location.reload(); }, 5000);
</script>
</head>
<body>

<div class="header">
    <h1>🔱 कान्हादेशी AI</h1>
    <div class="status-bar">
        <div class="status-item" id="date-display">--/--</div>
        <div class="status-item" id="time-display">--:--</div>
        <div class="status-item">{{ status }}</div>
    </div>
</div>

<div class="main-container">
    5 PROCESS LOADING...<br>
    <span style="font-size:0.8rem; color:#555;">(Waiting for Pradeep's Instructions)</span>
</div>

</body>
</html>
'''

# --- 5. ROUTES ---
@app.route('/')
def index():
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
    return render_template_string(HTML_TEMPLATE, status=market_status)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
