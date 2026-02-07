import os
import pyotp
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# --- 1. KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 
market_status = "CHECKING..."
ans1_nifty = "WAIT..."
ans2_sector = "LOADING..."
winning_sector_code = "ALL" 

# --- 2. DATA SETUP ---
TOKEN_MAP = {
    "NIFTY": "99926000", "BANKNIFTY": "99926009",
    "NIFTY_IT": "99926004", "NIFTY_AUTO": "99926002",
    "SOUTHBANK": "3351", "CENTRALBK": "1563", "UCOBANK": "1164", "IDFCFIRSTB": "11184",
    "RTNINDIA": "13425", "OLAELEC": "29135", "TTML": "3515", "HFCL": "1363"
}

STOCK_CATEGORY = {
    "SOUTHBANK": "BANK", "CENTRALBK": "BANK", "UCOBANK": "BANK", "IDFCFIRSTB": "BANK",
    "OLAELEC": "AUTO", "RTNINDIA": "POWER", "TTML": "IT", "HFCL": "IT"
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
                market_status = "🟢 LIVE"
            else:
                market_status = "🔴 CLOSED"

            if smart_api is None:
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if not data['status']:
                    time.sleep(5)
                    continue

            bank_change = -100.0; it_change = -100.0; auto_change = -100.0

            for name, token in TOKEN_MAP.items():
                try:
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    if "NIFTY" in name: res = smart_api.ltpData("NSE", name, token)
                    
                    if res and res['status']:
                        ltp = float(res['data']['ltp'])
                        close = float(res['data']['close'])
                        live_data[token] = ltp

                        change = ltp - close
                        pct_change = (change / close) * 100
                        if name == "NIFTY": ans1_nifty = "POSITIVE ▲" if change > 0 else "NEGATIVE ▼"
                        if name == "BANKNIFTY": bank_change = pct_change
                        elif name == "NIFTY_IT": it_change = pct_change
                        elif name == "NIFTY_AUTO": auto_change = pct_change
                except:
                    pass
                time.sleep(0.05)
            
            if bank_change > it_change and bank_change > auto_change:
                ans2_sector = "BANKING"
                winning_sector_code = "BANK"
            elif it_change > bank_change and it_change > auto_change:
                ans2_sector = "IT / TECH"
                winning_sector_code = "IT"
            elif auto_change > bank_change and auto_change > it_change:
                ans2_sector = "AUTO"
                winning_sector_code = "AUTO"
            else:
                ans2_sector = "MIXED"
                winning_sector_code = "ALL"
            
            time.sleep(1)
        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- 4. HTML TEMPLATE (PREMIUM UI DESIGN) ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI TRADER</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
/* --- MODERN THEME --- */
:root {
    --bg-gradient: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    --card-bg: rgba(255, 255, 255, 0.05);
    --card-border: 1px solid rgba(255, 255, 255, 0.1);
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --accent-blue: #3b82f6;
    --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
}

* { box-sizing: border-box; }
body { 
    background: var(--bg-gradient); 
    color: var(--text-main); 
    font-family: 'Inter', sans-serif; 
    margin: 0; 
    height: 100vh; 
    display: flex; 
    flex-direction: column; 
    overflow: hidden;
}

/* HEADER */
.header {
    padding: 15px 20px;
    background: rgba(15, 23, 42, 0.8);
    backdrop-filter: blur(10px);
    border-bottom: var(--card-border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: var(--shadow);
    z-index: 10;
}
.brand { font-weight: 800; font-size: 1.1rem; letter-spacing: 0.5px; background: linear-gradient(to right, #3b82f6, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.status-badge { font-size: 0.75rem; font-weight: 600; padding: 4px 10px; border-radius: 20px; background: rgba(255,255,255,0.1); border: var(--card-border); }

/* TOP SECTION */
.top-container {
    display: flex;
    height: 38%;
    border-bottom: var(--card-border);
}

/* Left Panel (Q&A) */
.left-panel {
    flex: 6.5;
    padding: 20px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    border-right: var(--card-border);
}
.q-box { margin-bottom: 20px; }
.q-label { color: var(--text-muted); font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }
.a-value { font-size: 1.6rem; font-weight: 800; color: var(--text-main); line-height: 1.2; }
.green-txt { color: var(--accent-green); }
.red-txt { color: var(--accent-red); }

/* Right Panel (Today Stocks) */
.right-panel {
    flex: 3.5;
    background: rgba(0,0,0,0.2);
    display: flex;
    flex-direction: column;
}
.panel-header {
    padding: 10px;
    font-size: 0.75rem;
    font-weight: 700;
    text-align: center;
    background: rgba(255,255,255,0.03);
    border-bottom: var(--card-border);
    color: var(--accent-blue);
}
.mini-list-content { overflow-y: auto; flex: 1; padding: 5px; }
.mini-item {
    font-size: 0.85rem;
    padding: 8px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    color: var(--text-muted);
}
.mini-item:last-child { border: none; }

/* FILTER BAR */
.filter-bar {
    padding: 15px 20px;
    display: flex;
    gap: 12px;
    overflow-x: auto;
    background: rgba(0,0,0,0.1);
    scrollbar-width: none;
}
.filter-btn {
    flex: 1;
    background: transparent;
    border: var(--card-border);
    color: var(--text-muted);
    padding: 10px 0;
    border-radius: 12px;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    text-align: center;
    white-space: nowrap;
}
.filter-btn.active {
    background: var(--accent-blue);
    color: white;
    border-color: var(--accent-blue);
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
}

/* MAIN LIST */
.main-list {
    flex: 1;
    overflow-y: auto;
    padding: 15px 20px;
}
.stock-card {
    background: var(--card-bg);
    border: var(--card-border);
    padding: 16px;
    border-radius: 16px;
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    backdrop-filter: blur(5px);
    transition: transform 0.1s;
}
.stock-card:active { transform: scale(0.98); }
.st-info { display: flex; flex-direction: column; }
.st-name { font-size: 1rem; font-weight: 700; color: var(--text-main); }
.st-cat { font-size: 0.7rem; color: var(--text-muted); margin-top: 2px; }
.st-price { font-size: 1.1rem; font-weight: 600; color: var(--accent-green); text-align: right; }

.hidden { display: none !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 10px; }
</style>
<script>
let currentWinner = "ALL";
let activeFilter = "ALL";

function updateTime(){
    const now = new Date(); 
    document.getElementById('time-display').innerText = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
} 
setInterval(updateTime,1000); 

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
        else if (activeFilter === 'TODAY') {
            if (currentWinner === 'ALL' || cat === currentWinner) show = true;
        } else if (activeFilter === 'PREV') {
            show = false; 
        }

        if(show) card.classList.remove('hidden');
        else card.classList.add('hidden');
    });
    
    // Update Mini List
    const miniItems = document.querySelectorAll('.mini-item');
    miniItems.forEach(item => {
        const cat = item.getAttribute('data-cat');
        if (currentWinner === 'ALL' || cat === currentWinner) item.style.display = 'block';
        else item.style.display = 'none';
    });
}

function fetchData() {
    fetch('/data')
    .then(response => response.json())
    .then(data => {
        // Update Status
        document.getElementById('status-disp').innerText = data.status;
        
        // Update Q&A
        const ans1El = document.getElementById('ans1-disp');
        ans1El.innerText = data.ans1;
        if(data.ans1.includes("POSITIVE")) { ans1El.className = "a-value green-txt"; }
        else if(data.ans1.includes("NEGATIVE")) { ans1El.className = "a-value red-txt"; }
        else { ans1El.className = "a-value"; }

        document.getElementById('ans2-disp').innerText = data.ans2;
        
        // Update Winner
        currentWinner = data.winner;
        
        // Update Prices
        data.stocks.forEach(s => {
            const el = document.getElementById('price-' + s.name);
            if(el) el.innerText = "₹" + s.price;
        });

        applyFilter();
    });
}
setInterval(fetchData, 1000); 
</script>
</head>
<body>

<div class="header">
    <div class="brand">AI TRADER</div>
    <div style="display:flex; gap:10px; align-items:center;">
        <div class="status-badge" id="time-display">--:--</div>
        <div class="status-badge" id="status-disp">{{ status }}</div>
    </div>
</div>

<div class="top-container">
    <div class="left-panel">
        <div class="q-box">
            <div class="q-label">01. MARKET TREND</div>
            <div class="a-value" id="ans1-disp">{{ ans1 }}</div>
        </div>
        <div class="q-box">
            <div class="q-label">02. TOP SECTOR</div>
            <div class="a-value" style="color:var(--accent-blue);" id="ans2-disp">{{ ans2 }}</div>
        </div>
    </div>

    <div class="right-panel">
        <div class="panel-header">🚀 TODAY'S PICKS</div>
        <div class="mini-list-content">
            {% for stock in stocks %}
            <div class="mini-item" data-cat="{{ stock.cat }}">{{ stock.name }}</div>
            {% endfor %}
        </div>
    </div>
</div>

<div class="filter-bar">
    <div id="btn-ALL" class="filter-btn active" onclick="filterStocks('ALL')">All Stocks</div>
    <div id="btn-TODAY" class="filter-btn" onclick="filterStocks('TODAY')">Today's Pick</div>
    <div id="btn-PREV" class="filter-btn" onclick="filterStocks('PREV')">Previous</div>
</div>

<div class="main-list">
    {% for stock in stocks %}
    <div class="stock-card" id="card-{{ stock.name }}" data-cat="{{ stock.cat }}">
        <div class="st-info">
            <span class="st-name">{{ stock.name }}</span>
            <span class="st-cat">{{ stock.cat }}</span>
        </div>
        <div class="st-price" id="price-{{ stock.name }}">₹{{ stock.price }}</div>
    </div>
    {% endfor %}
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
    return render_template_string(HTML_TEMPLATE, status=market_status, ans1=ans1_nifty, ans2=ans2_sector, stocks=STOCKS, winner=winning_sector_code)

@app.route('/data')
def data():
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, "0.00")
    
    return jsonify({
        "status": market_status,
        "ans1": ans1_nifty,
        "ans2": ans2_sector,
        "winner": winning_sector_code,
        "stocks": STOCKS
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
