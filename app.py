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
                        <a href="/chart/${s.symbol}" class
