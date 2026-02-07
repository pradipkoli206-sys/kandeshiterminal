import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "वाट पहा..."
ans2_sector = "डेटा चेक करत आहे..."
winning_sector_code = "ALL" # Default (Sagle dakhva)

# --- 2. DATA SETUP ---
TOKEN_MAP = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
    "NIFTY_IT": "00000",
    "NIFTY_AUTO": "00000",
    
    # Tujhe Stocks (Category wise)
    "SOUTHBANK": "00000",
    "CENTRALBK": "00000",
    "UCOBANK": "00000",
    "IDFCFIRSTB": "00000",
    "RTNINDIA": "00000",
    "OLAELEC": "00000",
    "TTML": "00000",
    "HFCL": "00000"
}

# Stocks la Sector pramane waatun ghetle
STOCK_CATEGORY = {
    "SOUTHBANK": "BANK",
    "CENTRALBK": "BANK",
    "UCOBANK": "BANK",
    "IDFCFIRSTB": "BANK",
    "OLAELEC": "AUTO",
    "RTNINDIA": "POWER", 
    "TTML": "IT",        
    "HFCL": "IT"         
}

STOCKS = []
for name, token in TOKEN_MAP.items():
    if "NIFTY" not in name:
        cat = STOCK_CATEGORY.get(name, "OTHER")
        STOCKS.append({"name": name, "token": token, "price": "0.00", "cat": cat})

# --- 3. ENGINE ---
def start_engine():
    global live_data, market_status, ans1_nifty, ans2_sector, winning_sector_code
    smart_api = None
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            ist_now = utc_now + timedelta(hours=5, minutes=30)
            current_time = ist_now.time()
            weekday = ist_now.weekday()

            start_time = datetime.strptime("09:00", "%H:%M").time()
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

            # Sector Changes Track karnya sathi
            bank_change = -100.0
            it_change = -100.0
            auto_change = -100.0
            nifty_change = 0.0

            for name, token in TOKEN_MAP.items():
                try:
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    if res and res['status']:
                        ltp = float(res['data']['ltp'])
                        close = float(res['data']['close'])
                        live_data[token] = ltp

                        # --- PROCESS 1 CALCULATION ---
                        if start_time <= current_time:
                            change = ltp - close
                            pct_change = (change / close) * 100

                            if name == "NIFTY":
                                nifty_change = change
                                if change > 0: ans1_nifty = "🟢 हो, निफ्टी POSITIVE आहे!"
                                else: ans1_nifty = "🔴 नाही, निफ्टी NEGATIVE आहे."

                            if name == "BANKNIFTY": bank_change = pct_change
                            elif name == "NIFTY_IT": it_change = pct_change
                            elif name == "NIFTY_AUTO": auto_change = pct_change

                except:
                    pass
                time.sleep(0.05)
            
            # --- PROCESS 2: SECTOR WINNER & FILTER ---
            # Konta sector jorat aahe?
            if bank_change > 0.5 and bank_change > it_change and bank_change > auto_change:
                ans2_sector = f"🏦 बँकिंग (BANKING) जोरात आहे ({bank_change:.2f}%)"
                winning_sector_code = "BANK"
            
            elif it_change > 0.5 and it_change > bank_change and it_change > auto_change:
                ans2_sector = f"💻 आयटी (IT/TELE) जोरात आहे ({it_change:.2f}%)"
                winning_sector_code = "IT"

            elif auto_change > 0.5 and auto_change > bank_change and auto_change > it_change:
                ans2_sector = f"🚗 ऑटो (AUTO) जोरात आहे ({auto_change:.2f}%)"
                winning_sector_code = "AUTO"
            
            else:
                ans2_sector = "⚠️ मार्केट मिक्स (MIXED) आहे."
                winning_sector_code = "ALL" # Sagle dakhva jar clear trend nasel
            
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
<title>Process Mode</title>
<style>
:root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --green: #00ff66; --red: #ff3333; --yellow: #ffcc00; }
body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 20px; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }

.header { padding: 10px 15px; background: rgba(10, 17, 24, 0.95); border-bottom: 2px solid var(--neon); flex-shrink: 0; display: flex; justify-content: center; align-items: center; }
.status-bar { display: flex; justify-content: center; gap: 5px; font-size: 0.75rem; font-weight: bold; color: #ccc; }
.status-item { border: 1px solid var(--neon); padding: 2px 5px; border-radius: 4px; background: rgba(0, 242, 255, 0.1); color: var(--neon); }

.main-container { flex: 1; display: flex; flex-direction: column; padding: 20px; overflow-y: auto; align-items: center; }

/* Que Box (TOP SECTION) */
.process-box { width: 95%; max-width: 400px; background: var(--card); border: 2px solid var(--neon); border-radius: 10px; padding: 15px; text-align: center; margin-bottom: 25px; flex-shrink: 0; box-shadow: 0 0 15px rgba(0, 242, 255, 0.15); }
.que-text { font-size: 0.95rem; color: #aaa; margin-bottom: 5px; font-weight: bold; }
.ans-text { font-size: 1.1rem; color: #fff; font-weight: 900; margin-bottom: 15px; padding: 8px; background: rgba(0, 242, 255, 0.1); border-radius: 5px; border: 1px dashed var(--neon); }

/* Stock List (BELOW SECTION) */
.stock-list { width: 95%; max-width: 400px; display: flex; flex-direction: column; gap: 10px; }
.stock-card { background: #161b22; border: 1px solid #333; padding: 15px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; animation: fadeIn 0.5s; transition: 0.3s; }
.stock-card:hover { border-color: var(--neon); box-shadow: 0 0 5px var(--neon); }
.st-name { color: #fff; font-weight: 900; font-size: 1rem; }
.st-price { color: var(--green); font-weight: bold; }
.st-cat { font-size: 0.6rem; background: #333; padding: 2px 5px; border-radius: 3px; color: #888; margin-left: 5px; }

@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
</style>
<script>
function updateTime(){
    const now = new Date(); 
    document.getElementById('date-display').innerText = now.toLocaleDateString('en-GB');
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: true});
} 
setInterval(updateTime,1000); 
updateTime();
setInterval(function(){ location.reload(); }, 5000);
</script>
</head>
<body>

<div class="header">
    <div class="status-bar">
        <div class="status-item" id="date-display">--/--</div>
        <div class="status-item" id="time-display">--:--</div>
        <div class="status-item">{{ status }}</div>
    </div>
</div>

<div class="main-container">
    
    <div class="process-box">
        <div style="color:var(--yellow); font-size:0.8rem; margin-bottom:10px; border-bottom:1px dashed #333; padding-bottom:5px; font-weight:bold;">STEP 1: MARKET OVERVIEW</div>
        
        <div class="que-text">१) आज निफ्टी पॉझिटिव्ह आहे की निगेटिव्ह?</div>
        <div class="ans-text">{{ ans1 }}</div>

        <div class="que-text">२) आज कोणता सेक्टर जोरात आहे?</div>
        <div class="ans-text">{{ ans2 }}</div>
    </div>

    <div class="stock-list">
        <div style="color:#888; font-size:0.8rem; text-align:center; margin-bottom:10px;">👇 FILTERED STOCKS 👇</div>
        
        {% for stock in stocks %}
            {% if filter_code == 'ALL' or stock.cat == filter_code %}
            <div class="stock-card">
                <div>
                    <span class="st-name">{{ stock.name }}</span>
                    <span class="st-cat">{{ stock.cat }}</span>
                </div>
                <div class="st-price">₹{{ stock.price }}</div>
            </div>
            {% endif %}
        {% endfor %}
    </div>

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
    
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, filter_code=winning_sector_code)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
