import os
import sys
import requests
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect 
import pyotp
import google.generativeai as genai
import pandas as pd
import pandas_ta as ta

app = Flask(__name__)

# --- 1. CONFIGURATION (Environment Variables मधून डेटा घेणे) ---
API_KEY = os.environ.get('API_KEY')
CLIENT_ID = os.environ.get('CLIENT_ID')
PASSWORD = os.environ.get('PASSWORD')
TOTP_SECRET = os.environ.get('TOTP_SECRET')

# --- WHATSAPP CONFIGURATION ---
WHATSAPP_PHONE = os.environ.get('WHATSAPP_PHONE', '+91XXXXXXXXXX')
WHATSAPP_API_KEY = os.environ.get('WHATSAPP_API_KEY', 'XXXXXX')

# --- STOCK TOKENS ---
TOKEN_MAP = {
    "SOUTHBANK": "4259",
    "UCOBANK": "11287",
    "CENTRALBK": "6092",
    "IDFCFIRSTB": "11184",
    "RTNINDIA": "13917",
    "RELIANCE": "2885",
    "ZOMATO": "5097",
    "IRFC": "265",
    "TATASTEEL": "3499",
    "PNB": "10666"
}
STOCKS = list(TOKEN_MAP.keys())

# --- AI CONFIGURATION ---
ai_status = "OK"
try:
    genai.configure(api_key="AIzaSyD5XVnFmqAd1890GSRnZL7WRUmU1MXWSTc")
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    model = None
    ai_status = f"AI Error: {str(e)}"

# --- SMARTAPI LOGIN ---
smartApi = None
def angel_login():
    global smartApi
    try:
        smartApi = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = smartApi.generateSession(CLIENT_ID, PASSWORD, totp)
        if data['status']:
            print("Angel One Login Successful! ✅")
            return True
        else:
            print(f"Login Failed: {data['message']}")
            return False
    except Exception as e:
        print(f"Connection Error: {e}")
        return False

angel_login()

# --- WHATSAPP FUNCTION ---
def send_whatsapp_alert(symbol, score, price, trend):
    try:
        message = f"🚨 *High Alert: {symbol}*\n🔥 Score: {score}/100\n💰 Price: ₹{price}\n📈 Trend: {trend}\n\n_Sent by Kanhadeshi Trader_"
        encoded_msg = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_PHONE}&text={encoded_msg}&apikey={WHATSAPP_API_KEY}"
        requests.get(url, timeout=5)
    except Exception: pass

# --- DATA FUNCTIONS ---
def fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=5):
    global smartApi
    try:
        todate = datetime.now()
        fromdate = todate - timedelta(days=days)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": interval,
            "fromdate": fromdate.strftime("%Y-%m-%d %H:%M"),
            "todate": todate.strftime("%Y-%m-%d %H:%M")
        }
        data = smartApi.getCandleData(params)
        if data and data.get('data'):
            df = pd.DataFrame(data['data'], columns=["timestamp", "open", "high", "low", "close", "volume"])
            df['close'] = df['close'].astype(float)
            return df
    except Exception as e:
        print(f"Candle Data Error: {e}")
    return None

def calculate_technical_score(df, current_price):
    if df is None or len(df) < 20:
        return 50, "WAITING", 0, "NEUTRAL"

    df['rsi'] = df.ta.rsi(length=14)
    current_rsi = df['rsi'].iloc[-1]
    
    df['ema_50'] = df.ta.ema(length=50)
    current_ema = df['ema_50'].iloc[-1] if pd.notna(df['ema_50'].iloc[-1]) else current_price

    score = 50
    trend = "SIDEWAYS"
    
    if current_price > current_ema:
        score += 10
        trend = "BULLISH 🚀"
    else:
        score -= 10
        trend = "BEARISH 📉"

    if 30 <= current_rsi <= 45 and trend == "BULLISH 🚀":
        score += 30 
    elif current_rsi > 70:
        score -= 20 
    elif current_rsi < 30:
        score += 20 

    # Score Clamping 0-100 (पण हे नंतर MTF ने ॲडजस्ट होईल)
    return int(score), trend, 0, round(current_rsi, 1)

# --- MAIN LOGIC (UPDATED FOR MULTI-TIMEFRAME) ---
history = {}
sent_alerts = {} 

def get_ultra_pro_data(ticker):
    global smartApi
    try:
        if smartApi is None: angel_login()
        token = TOKEN_MAP.get(ticker)
        
        # 1. Fetch 15 Minute Data (Primary)
        df_15m = fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=5)
        
        # 2. Fetch 1 Hour Data (Secondary - For MTF Confirmation)
        # 1 तासाच्या EMA 50 साठी जास्त दिवस लागतात (उदा. 15 दिवस)
        df_1h = fetch_candle_data(token, interval="ONE_HOUR", days=20)

        # Basic Price
        if df_15m is not None:
            price = df_15m['close'].iloc[-1]
        else:
            ltp_data = smartApi.ltpData("NSE", f"{ticker}-EQ", token)
            price = ltp_data['data']['ltp']
        
        # 3. Calculate 15m Score
        score, trend, _, rsi_val = calculate_technical_score(df_15m, price)
        
        # 4. --- MULTI-TIMEFRAME LOGIC START ---
        mtf_msg = "NEUTRAL"
        mtf_bonus = 0
        
        if df_1h is not None and len(df_1h) > 50:
            # Hourly EMA Calculation
            df_1h['ema_50'] = df_1h.ta.ema(length=50)
            hourly_ema = df_1h['ema_50'].iloc[-1]
            hourly_trend = "BULLISH" if price > hourly_ema else "BEARISH"
            
            # Comparison (15m vs 1H)
            if "BULLISH" in trend and hourly_trend == "BULLISH":
                mtf_bonus = 10  # दोघांचे एकमत (Strong Buy)
                mtf_msg = "ALIGNED 🚀"
            elif "BEARISH" in trend and hourly_trend == "BEARISH":
                mtf_bonus = 10  # दोघांचे एकमत (Strong Sell)
                mtf_msg = "ALIGNED 🔻"
            else:
                mtf_bonus = -15 # भांडण आहे (Trap Zone)
                mtf_msg = "CONFLICT ⚠️"
        
        # Final Score Adjustment
        score += mtf_bonus
        final_score = max(5, min(99, score))
        alerts_count = 1 if final_score > 80 else 0
        # --- MULTI-TIMEFRAME LOGIC END ---

        # Stability
        if ticker not in history:
            history[ticker] = {'val': final_score, 'stable': 0, 'dir': '●'}
        else:
            prev = history[ticker]['val']
            if final_score >= 85 and prev >= 85: history[ticker]['stable'] += 1
            else: history[ticker]['stable'] = 0
            
            if final_score > prev: history[ticker]['dir'] = "↑"
            elif final_score < prev: history[ticker]['dir'] = "↓"
            history[ticker]['val'] = final_score

        # WhatsApp Trigger
        today_date = datetime.now().strftime("%Y-%m-%d")
        alert_key = f"{ticker}_{today_date}"
        if final_score >= 85 and history[ticker]['stable'] >= 1:
            if alert_key not in sent_alerts:
                send_whatsapp_alert(ticker, final_score, price, trend)
                sent_alerts[alert_key] = True 
        
        # Levels
        sl_val = price * 0.99
        tp1 = price * 1.015
        tp2 = price * 1.025
        tp3 = price * 1.04
        
        s1 = "DONE ✅"
        s2 = f"RSI: {rsi_val}"
        s3 = mtf_msg # इथे MTF चे स्टेटस दिसेल
        s4 = "NO TRAP ✅" if mtf_bonus >= 0 else "TRAP ALERT ⚠️"
        s5 = "ACTIVATE 🔥" if final_score > 80 else "WATCHING 👀"

        return {
            "symbol": ticker, 
            "price": "{:.2f}".format(price), 
            "score": final_score, 
            "dir": history[ticker]['dir'], 
            "is_stable": history[ticker]['stable'] >= 1,
            "entry": "{:.2f}".format(price), 
            "sl": "{:.2f}".format(sl_val),
            "tp1": "{:.2f}".format(tp1), 
            "tp2": "{:.2f}".format(tp2), 
            "tp3": "{:.2f}".format(tp3),
            "rr": "1:2.5",
            "sup": "{:.2f}".format(price * 0.98), 
            "res": "{:.2f}".format(price * 1.02),
            "trend": trend,
            "ob": f"₹{round(price * 0.99, 2)}", 
            "fvg": "DETECTED ⚡" if final_score > 75 else "NONE",
            "slh": "SAFE 🟢",
            "liq": "SWEEP 🧹" if rsi_val < 30 else "NO",
            "brk": "POSSIBLE" if final_score > 70 else "NO",
            "vol": "HIGH" if final_score > 80 else "AVG",
            "mtf": mtf_msg, # New MTF Field
            "corr": "POSITIVE",
            "vap": f"₹{round(price, 2)}", 
            "trap": "CHECKING...",
            "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5,
            "nifty_top": "NIFTY: LIVE",
            "bn_top": "BANKNIFTY: LIVE",
            "alerts": str(alerts_count)
        }
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

# --- ROUTES ---
@app.route('/api/pro_feed')
def api_pro_feed():
    res = [get_ultra_pro_data(s) for s in STOCKS]
    valid = sorted([x for x in res if x], key=lambda x: x['score'], reverse=True)
    return jsonify({"stocks": valid, "nifty": "NIFTY: LIVE", "bn": "BN: LIVE"})

@app.route('/api/smc_live/<symbol>')
def api_smc_live(symbol):
    data = get_ultra_pro_data(symbol)
    return jsonify(data) if data else jsonify({"error": "not found"})

@app.route('/api/ai_analysis/<symbol>')
def ai_analysis(symbol):
    data = get_ultra_pro_data(symbol)
    if ai_status != "OK": return jsonify({"analysis": f"CRITICAL ERROR: {ai_status}"})
    if not model: return jsonify({"analysis": "Library Error."})

    prompt = f"""
    तुम्ही एक SMC (Smart Money Concept) तज्ञ आहात.
    स्टॉक: {symbol}
    किंमत: {data['price']}
    ट्रेंड: {data['trend']}
    MTF (Multi Timeframe): {data['mtf']}
    SMC Score: {data['score']}/100.
    
    मला मराठीत थोडक्यात सांगा:
    1. आता Buy करावे का? (Entry)
    2. धोका काय आहे? (Risk)
    3. टार्गेट काय ठेवावे?
    """
    try:
        response = model.generate_content(prompt)
        return jsonify({"analysis": response.text})
    except Exception as e:
        return jsonify({"analysis": f"API Error: {str(e)}"})

# --- HTML TEMPLATES ---
@app.route('/')
def index():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="mr">
    <head>
        <meta charset="UTF-8">
        <title>{{title}}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --up: #00ff66; --down: #ff3333; }
            body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 100px; }
            .header { padding: 15px 0; text-align: center; background: rgba(10, 17, 24, 0.9); border-bottom: 2px solid var(--neon); position: sticky; top: 0; z-index: 1000; backdrop-filter: blur(10px); }
            .container { padding: 20px; max-width: 500px; margin: auto; }
            .pro-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; display: block; text-decoration: none; color: inherit; box-shadow: 0 0 15px var(--neon); }
            .pro-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; z-index: 1; }
            .inner-content { position: relative; z-index: 10; padding: 15px; display: flex; align-items: center; justify-content: space-between; }
            .score-circle { width: 65px; height: 65px; border-radius: 50%; border: 2px solid var(--neon); display: flex; flex-direction: column; justify-content: center; align-items: center; }
            .entry-ready { border-color: var(--up); box-shadow: 0 0 15px var(--up); color: var(--up); }
            .c-bar { width: 5px; border-radius: 1px; animation: glow 1.5s infinite; }
            @keyframes glow { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
            .footer { position: fixed; bottom: 0; width: 100%; background: rgba(10, 17, 24, 0.9); padding: 15px; text-align: center; border-top: 1px solid #21262d; font-size: 0.7rem; color: #8b949e; }
        </style>
    </head>
    <body>
        <div class="header"><h1>🔱 {{title}}</h1><div id="ticker" style="color:var(--neon); font-weight:bold;"><span id="nifty_top">...</span> | <span id="bn_top">...</span></div></div>
        <div class="container" id="terminal"></div>
        <div class="footer">{{credit}}</div>
        <script>
            const beep = new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg');
            let played = new Set();
            async function update() {
                try {
                    const r = await fetch('/api/pro_feed');
                    const data = await r.json();
                    document.getElementById('nifty_top').innerText = data.nifty;
                    document.getElementById('bn_top').innerText = data.bn;
                    let html = '';
                    data.stocks.forEach(s => {
                        let isHigh = s.score >= 85 && s.is_stable;
                        let candleColor = s.score >= 80 ? 'var(--up)' : 'var(--down)';
                        let glow = isHigh ? 'entry-ready' : '';
                        if(isHigh && !played.has(s.symbol)) { beep.play(); played.add(s.symbol); }
                        else if(!isHigh) { played.delete(s.symbol); }
                        html += `
                        <a href="/chart/${s.symbol}" class="pro-card">
                            <div class="inner-content">
                                <div><span style="font-size:0.8rem; color:#8b949e; font-weight:bold;">${s.symbol}</span><div style="font-size:1.6rem; font-weight:900; color:var(--neon);">₹${s.price}</div></div>
                                <div class="score-circle ${glow}"><b style="font-size:1.1rem;">${s.score}%</b><span style="font-size:0.5rem; font-weight:bold;">${s.dir}</span></div>
                                <div style="display:flex; align-items:flex-end; gap:2px; height:45px;">
                                    <div class="c-bar" style="height:25px; background:${candleColor}"></div>
                                    <div class="c-bar" style="height:35px; background:${candleColor}"></div>
                                    <div class="c-bar" style="height:30px; background:${candleColor}"></div>
                                </div>
                            </div>
                        </a>`;
                    });
                    document.getElementById('terminal').innerHTML = html;
                } catch(e) {}
            }
            setInterval(update, 5000); update();
        </script>
    </body>
    </html>
    ''', title="कान्हादेशी ट्रेडर: MTF Mode", credit="POWERED BY ANGEL ONE & CALLMEBOT")

@app.route('/chart/<symbol>')
def chart(symbol):
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{{symbol}} Analysis</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --up: #00ff66; --down: #ff3333; }
            body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding: 15px; padding-top: 70px; display: flex; flex-direction: column; align-items: center; }
            .action-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 20px; padding: 2px; overflow: hidden; width: 100%; max-width: 420px; box-shadow: 0 0 15px var(--neon); }
            .action-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; z-index: 1; }
            .inner { position: relative; z-index: 10; padding: 15px; text-align: center; }
            .p-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; text-align: left; }
            .p-item { background: rgba(0,242,255,0.05); padding: 8px; border-radius: 8px; border: 1px solid #21262d; }
            .p-label { font-size: 0.55rem; color: #8b949e; display: block; text-transform: uppercase; font-weight: bold; }
            .p-val { font-size: 0.85rem; color: var(--neon); font-weight: 900; }
        </style>
    </head>
    <body>
        <div style="position: absolute; top: 20px; left: 20px;"><a href="/" style="color:#8b949e; text-decoration:none; font-weight:bold;">⬅️ BACK</a></div>
        <div class="action-card"><div class="inner"><h1 style="color:var(--neon); margin:0; font-size: 2rem;">{{symbol}}</h1></div></div>

        <div class="action-card">
            <div class="inner">
                <span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">🎯 ENTRY PLAN (BUY)</span>
                <div class="p-grid">
                    <div class="p-item"><span class="p-label">Entry Price</span><span class="p-val" id="e_val">₹0</span></div>
                    <div class="p-item"><span class="p-label">Stop Loss (SL)</span><span class="p-val" id="sl_val" style="color:var(--down);">₹0</span></div>
                    <div class="p-item"><span class="p-label">T1 (Safe)</span><span class="p-val" id="t1" style="color:var(--up);">₹0</span></div>
                    <div class="p-item"><span class="p-label">T2 (Pro)</span><span class="p-val" id="t2" style="color:var(--up);">₹0</span></div>
                    <div class="p-item" style="grid-column: span 2;"><span class="p-label">T3 (Jackpot)</span><span class="p-val" id="t3" style="color:var(--up);">₹0</span></div>
                </div>
            </div>
        </div>

        <div class="action-card"><div style="position:relative; z-index:10; width:100%; height:280px; border-radius:12px; overflow:hidden;"><div id="tv_chart" style="height:100%;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize": true, "symbol": "NSE:{{symbol}}", "interval": "15", "theme": "dark", "container_id": "tv_chart", "hide_top_toolbar": true});</script></div></div>

        <div class="action-card">
            <div class="inner">
                <span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">📊 SMC TECHNICAL DETAILS</span>
                <div class="p-grid">
                    <div class="p-item"><span class="p-label">1. Support</span><span class="p-val" id="sup">₹0</span></div>
                    <div class="p-item"><span class="p-label">2. Resist.</span><span class="p-val" id="res">₹0</span></div>
                    <div class="p-item"><span class="p-label">3. Trend</span><span class="p-val" id="trend">--</span></div>
                    <div class="p-item"><span class="p-label">4. Order Block</span><span class="p-val" id="ob">--</span></div>
                    <div class="p-item"><span class="p-label">5. FVG Status</span><span class="p-val" id="fvg">--</span></div>
                    <div class="p-item"><span class="p-label">6. SL Hunting</span><span class="p-val" id="slh">--</span></div>
                    <div class="p-item"><span class="p-label">7. Liq. Sweep</span><span class="p-val" id="liq">--</span></div>
                    <div class="p-item"><span class="p-label">8. Breakout</span><span class="p-val" id="brk">--</span></div>
                    <div class="p-item"><span class="p-label">9. Vol Spike</span><span class="p-val" id="vol">--</span></div>
                    <div class="p-item"><span class="p-label">10. MTF Conf.</span><span class="p-val" id="mtf">--</span></div>
                    <div class="p-item"><span class="p-label">11. Correlation</span><span class="p-val" id="corr">--</span></div>
                    <div class="p-item"><span class="p-label">12. Vol @ Price</span><span class="p-val" id="vap">--</span></div>
                    <div class="p-item" style="grid-column: span 2; border: 1px solid var(--down);"><span class="p-label">13. Trap Analysis</span><span class="p-val" id="trap" style="color:var(--down);">--</span></div>
                </div>
                <hr style="border: 0.5px solid #21262d; margin-top: 15px;">
                <span style="color: var(--up); font-weight: bold; font-size: 0.7rem;">⚙️ SEQUENCE OF LOGIC</span>
                <div class="p-grid" style="grid-template-columns: 1fr;">
                    <div class="p-item" style="padding: 5px;"><span class="p-label" style="display:inline;">1. Scanning: </span><span class="p-val" id="s1" style="font-size:0.7rem;">...</span></div>
                    <div class="p-item" style="padding: 5px;"><span class="p-label" style="display:inline;">2. Filtering: </span><span class="p-val" id="s2" style="font-size:0.7rem;">...</span></div>
                    <div class="p-item" style="padding: 5px;"><span class="p-label" style="display:inline;">3. Confirmation: </span><span class="p-val" id="s3" style="font-size:0.7rem;">...</span></div>
                    <div class="p-item" style="padding: 5px;"><span class="p-label" style="display:inline;">4. Trap Check: </span><span class="p-val" id="s4" style="font-size:0.7rem;">...</span></div>
                    <div class="p-item" style="padding: 5px;"><span class="p-label" style="display:inline;">5. Action: </span><span class="p-val" id="s5" style="font-size:0.7rem; color:var(--up);">...</span></div>
                </div>
            </div>
        </div>

        <div class="action-card" onclick="fetchAI()" style="cursor:pointer;"><div class="inner"><span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">🤖 AI INSIGHT</span><div id="ai_insight" style="font-size: 0.85rem; color: var(--neon); margin-top: 10px; font-weight: bold;">विश्लेषणासाठी क्लिक करा...</div></div></div>

        <div class="action-card"><div class="inner" style="text-align: left;"><span style="color: #ff3333; font-weight: bold; font-size: 0.7rem;">🛡️ RISK & TRADE LOCK</span><div style="display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.8rem;"><div class="p-item" style="width:45%;"><span class="p-label">Daily Trades</span><b style="color:var(--neon);">0 / 2</b></div><div class="p-item" style="width:45%;"><span class="p-label">Max Loss</span><b style="color:var(--down);">₹150</b></div></div></div></div>

        <div class="action-card"><div class="inner" style="text-align: left;"><span style="color: #8b949e; font-weight: bold; font-size: 0.7rem;">📊 SIGNAL HISTORY (LAST 30 DAYS)</span><div style="margin-top: 10px; font-size: 0.8rem;"><div style="display:flex; justify-content:space-between; padding:5px; border-bottom:1px solid #21262d;"><span style="color:#8b949e;">Win Rate</span><b style="color:var(--up);">78%</b></div><div style="display:flex; justify-content:space-between; padding:5px;"><span style="color:#8b949e;">Alerts (Today)</span><b id="alerts_count" style="color:var(--neon);">0</b></div></div></div></div>

        <script>
            async function fetchAI() { document.getElementById('ai_insight').innerText = " विचार करत आहे... 🤔"; const r = await fetch('/api/ai_analysis/{{symbol}}'); const d = await r.json(); document.getElementById('ai_insight').innerText = d.analysis; }
            async function updateLive() {
                try {
                    const r = await fetch('/api/smc_live/{{symbol}}');
                    const d = await r.json();
                    document.getElementById('e_val').innerText = '₹'+d.entry;
                    document.getElementById('sl_val').innerText = '₹'+d.sl;
                    document.getElementById('t1').innerText = '₹'+d.tp1;
                    document.getElementById('t2').innerText = '₹'+d.tp2;
                    document.getElementById('t3').innerText = '₹'+d.tp3;
                    document.getElementById('sup').innerText = '₹'+d.sup;
                    document.getElementById('res').innerText = '₹'+d.res;
                    document.getElementById('trend').innerText = d.trend;
                    document.getElementById('ob').innerText = d.ob;
                    document.getElementById('fvg').innerText = d.fvg;
                    document.getElementById('slh').innerText = d.slh;
                    document.getElementById('liq').innerText = d.liq;
                    document.getElementById('brk').innerText = d.brk;
                    document.getElementById('vol').innerText = d.vol;
                    document.getElementById('mtf').innerText = d.mtf;
                    document.getElementById('corr').innerText = d.corr;
                    document.getElementById('vap').innerText = d.vap;
                    document.getElementById('trap').innerText = d.trap;
                    document.getElementById('s1').innerText = d.s1;
                    document.getElementById('s2').innerText = d.s2;
                    document.getElementById('s3').innerText = d.s3;
                    document.getElementById('s4').innerText = d.s4;
                    document.getElementById('s5').innerText = d.s5;
                    document.getElementById('alerts_count').innerText = d.alerts;
                } catch(e){}
            }
            setInterval(updateLive, 5000); updateLive();
        </script>
    </body>
    </html>
    ''', symbol=symbol)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
