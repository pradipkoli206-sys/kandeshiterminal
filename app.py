import os
import sys
import requests
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request, redirect, url_for
from SmartApi import SmartConnect 
import pyotp
import google.generativeai as genai
import pandas as pd
import pandas_ta as ta

app = Flask(__name__)

# --- 1. CONFIGURATION (Environment Variables) ---
API_KEY = os.environ.get('API_KEY')
CLIENT_ID = os.environ.get('CLIENT_ID')
PASSWORD = os.environ.get('PASSWORD')
TOTP_SECRET = os.environ.get('TOTP_SECRET')

# --- WHATSAPP CONFIGURATION ---
WHATSAPP_PHONE = os.environ.get('WHATSAPP_PHONE', '+91XXXXXXXXXX')
WHATSAPP_API_KEY = os.environ.get('WHATSAPP_API_KEY', 'XXXXXX')

# --- STOCK CONFIGURATION ---
# डिफॉल्ट लिस्ट
STOCKS = [
    "SOUTHBANK", "UCOBANK", "CENTRALBK", "IDFCFIRSTB", "RTNINDIA",
    "RELIANCE", "ZOMATO", "IRFC", "TATASTEEL", "PNB"
]

TOKEN_MAP = {}

def update_token_map():
    global TOKEN_MAP
    try:
        print("⏳ Downloading Latest Token List from Angel One...")
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url)
        data = response.json()
        
        for item in data:
            if item['exch_seg'] == 'NSE' and item['symbol'].endswith('-EQ'):
                symbol_name = item['symbol'].split('-')[0]
                TOKEN_MAP[symbol_name] = item['token']
        
        print("✅ Tokens Updated Successfully!")
    except Exception as e:
        print(f"❌ Error fetching tokens: {e}")

update_token_map()

# --- AI CONFIGURATION (SECURE) ---
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
ai_status = "OK"

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        # मॉडेल नाव बदलले आहे जेणेकरून एरर येणार नाही
        model = genai.GenerativeModel('gemini-3-flash-preview') 
    except Exception as e:
        model = None
        ai_status = f"AI Config Error: {str(e)}"
else:
    model = None
    ai_status = "Error: GEMINI_API_KEY not found in environment variables"

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

    return int(score), trend, 0, round(current_rsi, 1)

# --- MAIN LOGIC ---
history = {}
sent_alerts = {} 

def get_ultra_pro_data(ticker):
    global smartApi
    try:
        if smartApi is None: angel_login()
        token = TOKEN_MAP.get(ticker)
        
        # जर टोकन सापडले नाही तर पुन्हा एकदा सर्च करा (डायनॅमिक सर्च)
        if not token:
             return {"symbol": ticker, "price": "N/A", "score": 0, "error": "Invalid Symbol"}

        # 1. Fetch Data
        df_15m = fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=5)
        df_1h = fetch_candle_data(token, interval="ONE_HOUR", days=20)

        if df_15m is not None:
            price = df_15m['close'].iloc[-1]
        else:
            try:
                ltp_data = smartApi.ltpData("NSE", f"{ticker}-EQ", token)
                price = ltp_data['data']['ltp']
            except:
                return None
        
        score, trend, _, rsi_val = calculate_technical_score(df_15m, price)
        
        # MTF Logic
        mtf_msg = "NEUTRAL"
        mtf_bonus = 0
        if df_1h is not None and len(df_1h) > 50:
            df_1h['ema_50'] = df_1h.ta.ema(length=50)
            hourly_ema = df_1h['ema_50'].iloc[-1]
            hourly_trend = "BULLISH" if price > hourly_ema else "BEARISH"
            
            if "BULLISH" in trend and hourly_trend == "BULLISH":
                mtf_bonus = 10
                mtf_msg = "ALIGNED 🚀"
            elif "BEARISH" in trend and hourly_trend == "BEARISH":
                mtf_bonus = 10
                mtf_msg = "ALIGNED 🔻"
            else:
                mtf_bonus = -15
                mtf_msg = "CONFLICT ⚠️"
        
        score += mtf_bonus
        final_score = max(5, min(99, score))
        alerts_count = 1 if final_score > 80 else 0

        # Stability Check
        if ticker not in history:
            history[ticker] = {'val': final_score, 'stable': 0, 'dir': '●'}
        else:
            prev = history[ticker]['val']
            if final_score >= 85 and prev >= 85: history[ticker]['stable'] += 1
            else: history[ticker]['stable'] = 0
            if final_score > prev: history[ticker]['dir'] = "↑"
            elif final_score < prev: history[ticker]['dir'] = "↓"
            history[ticker]['val'] = final_score

        # Alert Logic
        today_date = datetime.now().strftime("%Y-%m-%d")
        alert_key = f"{ticker}_{today_date}"
        if final_score >= 85 and history[ticker]['stable'] >= 1:
            if alert_key not in sent_alerts:
                send_whatsapp_alert(ticker, final_score, price, trend)
                sent_alerts[alert_key] = True 
        
        sl_val = price * 0.99
        return {
            "symbol": ticker, 
            "price": "{:.2f}".format(price), 
            "score": final_score, 
            "dir": history[ticker]['dir'], 
            "is_stable": history[ticker]['stable'] >= 1,
            "entry": "{:.2f}".format(price), 
            "sl": "{:.2f}".format(sl_val),
            "tp1": "{:.2f}".format(price * 1.015), 
            "tp2": "{:.2f}".format(price * 1.025), 
            "tp3": "{:.2f}".format(price * 1.04),
            "sup": "{:.2f}".format(price * 0.98), 
            "res": "{:.2f}".format(price * 1.02),
            "trend": trend,
            "ob": f"₹{round(price * 0.99, 2)}", 
            "fvg": "DETECTED ⚡" if final_score > 75 else "NONE",
            "slh": "SAFE 🟢",
            "liq": "SWEEP 🧹" if rsi_val < 30 else "NO",
            "brk": "POSSIBLE" if final_score > 70 else "NO",
            "vol": "HIGH" if final_score > 80 else "AVG",
            "mtf": mtf_msg,
            "corr": "POSITIVE",
            "vap": f"₹{round(price, 2)}", 
            "trap": "CHECKING...",
            "s1": "DONE ✅", "s2": f"RSI: {rsi_val}", "s3": mtf_msg, 
            "s4": "NO TRAP ✅" if mtf_bonus >= 0 else "TRAP ALERT ⚠️", 
            "s5": "ACTIVATE 🔥" if final_score > 80 else "WATCHING 👀",
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
    valid = sorted([x for x in res if x and 'error' not in x], key=lambda x: x['score'], reverse=True)
    return jsonify({"stocks": valid, "nifty": "NIFTY: LIVE", "bn": "BN: LIVE"})

@app.route('/api/smc_live/<symbol>')
def api_smc_live(symbol):
    data = get_ultra_pro_data(symbol)
    return jsonify(data) if data else jsonify({"error": "not found"})

@app.route('/api/ai_analysis/<symbol>')
def ai_analysis(symbol):
    data = get_ultra_pro_data(symbol)
    if ai_status != "OK" or not model: 
        return jsonify({"analysis": f"AI Error: {ai_status}"})

    prompt = f"""
    SMC तज्ञ म्हणून मराठीत सल्ला द्या:
    स्टॉक: {symbol}
    किंमत: {data['price']}
    ट्रेंड: {data['trend']}
    MTF: {data['mtf']}
    SMC Score: {data['score']}
    Order Block: {data['ob']}
    
    फक्त ३ ओळीत सांगा:
    1. एन्ट्री (Entry) कुठे घ्यावी?
    2. स्टॉपलॉस (SL) काय असावा?
    3. टार्गेट (Target) काय असावे?
    """
    try:
        response = model.generate_content(prompt)
        return jsonify({"analysis": response.text})
    except Exception as e:
        return jsonify({"analysis": f"API Error: {str(e)}"})

# सर्च बार साठी नवीन रूट
@app.route('/search', methods=['POST'])
def search_stock():
    symbol = request.form.get('symbol').upper()
    if symbol in TOKEN_MAP:
        return redirect(url_for('chart', symbol=symbol))
    else:
        # जर लिस्टमध्ये नसेल तर तात्पुरता ऍड करा आणि रिडायरेक्ट करा
        # टीप: यासाठी 'update_token_map' ने तो आधीच फेच केलेला असणे गरजेचे आहे
        if symbol not in STOCKS:
            STOCKS.append(symbol) # तात्पुरते लिस्टमध्ये टाकले
        return redirect(url_for('chart', symbol=symbol))

# --- TEMPLATES ---
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
            
            /* सर्च बार स्टाईल */
            .search-box { margin-bottom: 20px; display: flex; gap: 10px; }
            .search-box input { flex: 1; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: #000; color: #fff; outline: none; }
            .search-box button { padding: 12px 20px; border-radius: 8px; border: none; background: var(--neon); color: #000; font-weight: bold; cursor: pointer; }

            .pro-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; display: block; text-decoration: none; color: inherit; box-shadow: 0 0 15px var(--neon); }
            .pro-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; z-index: 1; }
            .inner-content { position: relative; z-index: 10; padding: 15px; display: flex; align-items: center; justify-content: space-between; }
            .score-circle { width: 65px; height: 65px; border-radius: 50%; border: 2px solid var(--neon); display: flex; flex-direction: column; justify-content: center; align-items: center; }
            .entry-ready { border-color: var(--up); box-shadow: 0 0 15px var(--up); color: var(--up); }
            .c-bar { width: 5px; border-radius: 1px; animation: glow 1.5s infinite; }
            @keyframes glow { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
        </style>
    </head>
    <body>
        <div class="header"><h1>🔱 {{title}}</h1><div id="ticker" style="color:var(--neon); font-weight:bold;">MARKET LIVE</div></div>
        
        <div class="container">
            <form action="/search" method="POST" class="search-box">
                <input type="text" name="symbol" placeholder="स्टॉक शोधा (उदा. SBIN)..." required>
                <button type="submit">GO</button>
            </form>

            <div id="terminal">Loading Stocks...</div>
        </div>

        <script>
            async function update() {
                try {
                    const r = await fetch('/api/pro_feed');
                    const data = await r.json();
                    let html = '';
                    data.stocks.forEach(s => {
                        let isHigh = s.score >= 85 && s.is_stable;
                        let candleColor = s.score >= 80 ? 'var(--up)' : 'var(--down)';
                        let glow = isHigh ? 'entry-ready' : '';
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
    ''', title="कान्हादेशी ट्रेडर: MTF Mode")

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

        <div class="action-card"><div style="position:relative; z-index:10; width:100%; height:300px; border-radius:12px; overflow:hidden;"><div id="tv_chart" style="height:100%;"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({"autosize": true, "symbol": "NSE:{{symbol}}", "interval": "15", "theme": "dark", "style": "1", "toolbar_bg": "#f1f3f6", "enable_publishing": false, "container_id": "tv_chart", "hide_top_toolbar": true});</script></div></div>

        <div class="action-card" onclick="fetchAI()" style="cursor:pointer;"><div class="inner"><span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">🤖 AI INSIGHT (क्लिक करा)</span><div id="ai_insight" style="font-size: 0.85rem; color: var(--neon); margin-top: 10px;">विश्लेषणासाठी येथे टॅप करा...</div></div></div>

        <div class="action-card">
            <div class="inner">
                <span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">📊 SMC LEVELS</span>
                <div class="p-grid">
                    <div class="p-item"><span class="p-label">Price</span><span class="p-val" id="vap">--</span></div>
                    <div class="p-item"><span class="p-label">Score</span><span class="p-val" id="score">--</span></div>
                    <div class="p-item"><span class="p-label">Trend</span><span class="p-val" id="trend">--</span></div>
                    <div class="p-item"><span class="p-label">MTF</span><span class="p-val" id="mtf">--</span></div>
                    <div class="p-item"><span class="p-label">Support</span><span class="p-val" id="sup">--</span></div>
                    <div class="p-item"><span class="p-label">Resistance</span><span class="p-val" id="res">--</span></div>
                    <div class="p-item"><span class="p-label">Order Block</span><span class="p-val" id="ob">--</span></div>
                    <div class="p-item"><span class="p-label">Trap?</span><span class="p-val" id="s4" style="color:var(--down);">--</span></div>
                </div>
            </div>
        </div>

        <script>
            async function fetchAI() { 
                document.getElementById('ai_insight').innerText = "AI विचार करत आहे... कृपया थांबा ⏳"; 
                const r = await fetch('/api/ai_analysis/{{symbol}}'); 
                const d = await r.json(); 
                document.getElementById('ai_insight').innerText = d.analysis; 
            }
            
            async function updateLive() {
                try {
                    const r = await fetch('/api/smc_live/{{symbol}}');
                    const d = await r.json();
                    document.getElementById('vap').innerText = '₹'+d.entry;
                    document.getElementById('score').innerText = d.score + '%';
                    document.getElementById('trend').innerText = d.trend;
                    document.getElementById('mtf').innerText = d.mtf;
                    document.getElementById('sup').innerText = '₹'+d.sup;
                    document.getElementById('res').innerText = '₹'+d.res;
                    document.getElementById('ob').innerText = d.ob;
                    document.getElementById('s4').innerText = d.s4;
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
