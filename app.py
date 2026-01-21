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

# --- 1. CONFIGURATION ---
API_KEY = os.environ.get('API_KEY')
CLIENT_ID = os.environ.get('CLIENT_ID')
PASSWORD = os.environ.get('PASSWORD')
TOTP_SECRET = os.environ.get('TOTP_SECRET')

WHATSAPP_PHONE = os.environ.get('WHATSAPP_PHONE', '+91XXXXXXXXXX')
WHATSAPP_API_KEY = os.environ.get('WHATSAPP_API_KEY', 'XXXXXX')

# --- STOCK CONFIGURATION ---
STOCKS = [
    "SOUTHBANK", "UCOBANK", "CENTRALBK", "IDFCFIRSTB", "RTNINDIA",
    "RELIANCE", "ZOMATO", "IRFC", "TATASTEEL", "PNB"
]

TOKEN_MAP = {} 

def update_token_map():
    global TOKEN_MAP
    try:
        print("⏳ Downloading Tokens...")
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url)
        data = response.json()
        for item in data:
            if item['exch_seg'] == 'NSE' and item['symbol'].endswith('-EQ'):
                symbol_name = item['symbol'].split('-')[0]
                if symbol_name in STOCKS:
                    TOKEN_MAP[symbol_name] = item['token']
        print("✅ Tokens Updated!")
    except Exception as e:
        print(f"❌ Error: {e}")

update_token_map()

# --- AI CONFIGURATION ---
ai_status = "OK"
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-3-flash-preview')
    except Exception as e:
        model = None
        ai_status = f"Setup Error: {str(e)}"
else:
    model = None
    ai_status = "Error: GEMINI_API_KEY Missing"

# --- SMARTAPI LOGIN ---
smartApi = None
def angel_login():
    global smartApi
    try:
        smartApi = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = smartApi.generateSession(CLIENT_ID, PASSWORD, totp)
        return data['status']
    except Exception:
        return False

angel_login()

# --- WHATSAPP ---
def send_whatsapp_alert(symbol, score, price, trend):
    try:
        message = f"🚨 *{symbol} Alert*\nScore: {score}\nPrice: {price}\nTrend: {trend}"
        encoded_msg = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_PHONE}&text={encoded_msg}&apikey={WHATSAPP_API_KEY}"
        requests.get(url, timeout=5)
    except: pass

# --- DATA FUNCTIONS ---
def fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=5):
    global smartApi
    try:
        todate = datetime.now()
        fromdate = todate - timedelta(days=days)
        params = {
            "exchange": "NSE", "symboltoken": token, "interval": interval,
            "fromdate": fromdate.strftime("%Y-%m-%d %H:%M"),
            "todate": todate.strftime("%Y-%m-%d %H:%M")
        }
        data = smartApi.getCandleData(params)
        if data and data.get('data'):
            df = pd.DataFrame(data['data'], columns=["timestamp", "open", "high", "low", "close", "volume"])
            df['close'] = df['close'].astype(float)
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            return df
    except: return None
    return None

def calculate_technical_score(df, current_price):
    if df is None or len(df) < 20: return 50, "WAITING", 0, "NEUTRAL"
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
    if 30 <= current_rsi <= 45 and trend == "BULLISH 🚀": score += 30 
    elif current_rsi > 70: score -= 20 
    elif current_rsi < 30: score += 20 
    return int(score), trend, 0, round(current_rsi, 1)

# --- MAIN LOGIC ---
history = {}
sent_alerts = {} 

def get_ultra_pro_data(ticker):
    global smartApi
    try:
        if smartApi is None: angel_login()
        token = TOKEN_MAP.get(ticker)
        if not token: return None

        df_15m = fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=5)
        df_1h = fetch_candle_data(token, interval="ONE_HOUR", days=20)

        if df_15m is not None: price = df_15m['close'].iloc[-1]
        else:
            try:
                ltp = smartApi.ltpData("NSE", f"{ticker}-EQ", token)
                price = ltp['data']['ltp']
            except: return None
        
        score, trend, _, rsi_val = calculate_technical_score(df_15m, price)
        
        mtf_msg = "NEUTRAL"
        mtf_bonus = 0
        if df_1h is not None and len(df_1h) > 50:
            df_1h['ema_50'] = df_1h.ta.ema(length=50)
            h_ema = df_1h['ema_50'].iloc[-1]
            h_trend = "BULLISH" if price > h_ema else "BEARISH"
            if "BULLISH" in trend and h_trend == "BULLISH": mtf_bonus = 10; mtf_msg = "ALIGNED 🚀"
            elif "BEARISH" in trend and h_trend == "BEARISH": mtf_bonus = 10; mtf_msg = "ALIGNED 🔻"
            else: mtf_bonus = -15; mtf_msg = "CONFLICT ⚠️"
        
        score += mtf_bonus
        final_score = max(5, min(99, score))
        alerts_count = 1 if final_score > 80 else 0

        if ticker not in history: history[ticker] = {'val': final_score, 'stable': 0, 'dir': '●'}
        else:
            prev = history[ticker]['val']
            if final_score >= 85 and prev >= 85: history[ticker]['stable'] += 1
            else: history[ticker]['stable'] = 0
            history[ticker]['dir'] = "↑" if final_score > prev else ("↓" if final_score < prev else "●")
            history[ticker]['val'] = final_score

        today = datetime.now().strftime("%Y-%m-%d")
        if final_score >= 85 and history[ticker]['stable'] >= 1:
            if f"{ticker}_{today}" not in sent_alerts:
                send_whatsapp_alert(ticker, final_score, price, trend)
                sent_alerts[f"{ticker}_{today}"] = True 
        
        return {
            "symbol": ticker, "price": "{:.2f}".format(price), "score": final_score, 
            "dir": history[ticker]['dir'], "is_stable": history[ticker]['stable'] >= 1,
            "entry": "{:.2f}".format(price), "sl": "{:.2f}".format(price*0.99),
            "tp1": "{:.2f}".format(price*1.015), "tp2": "{:.2f}".format(price*1.025), "tp3": "{:.2f}".format(price*1.04),
            "sup": "{:.2f}".format(price*0.98), "res": "{:.2f}".format(price*1.02),
            "trend": trend, "ob": f"₹{round(price*0.99, 2)}", "fvg": "YES" if final_score > 75 else "NO",
            "slh": "SAFE", "liq": "YES" if rsi_val < 30 else "NO", "brk": "YES" if final_score > 70 else "NO",
            "vol": "HIGH" if final_score > 80 else "AVG", "mtf": mtf_msg, "corr": "POS", "vap": f"₹{round(price, 2)}", 
            "trap": "CHECK", "s1": "OK", "s2": f"RSI:{rsi_val}", "s3": mtf_msg, "s4": "NO TRAP", 
            "s5": "BUY" if final_score > 80 else "WATCH", "nifty": "NIFTY: LIVE", "bn": "BN: LIVE", "alerts": str(alerts_count)
        }
    except: return None

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

# --- नवीन Route चार्ट डेटा साठी (NEW CHART ROUTE) ---
@app.route('/api/history/<symbol>')
def api_history(symbol):
    try:
        token = TOKEN_MAP.get(symbol)
        if not token: return jsonify([])
        df = fetch_candle_data(token, interval="FIFTEEN_MINUTE", days=20)
        if df is None or df.empty: return jsonify([])
        chart_data = []
        for index, row in df.iterrows():
            dt = pd.to_datetime(row['timestamp'])
            ts = int(dt.timestamp()) + 19800 # IST Time Fix
            chart_data.append({ 'time': ts, 'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'] })
        return jsonify(chart_data)
    except: return jsonify([])

@app.route('/api/ai_analysis/<symbol>')
def ai_analysis(symbol):
    if not model: return jsonify({"analysis": f"AI Error: {ai_status}"})
    data = get_ultra_pro_data(symbol)
    prompt = f"Stock: {symbol}, Price: {data['price']}, Trend: {data['trend']}, Score: {data['score']}. Marathi advice in 3 lines: Entry, Risk, Target."
    try: 
        response = model.generate_content(prompt)
        return jsonify({"analysis": response.text})
    except Exception as e: 
        return jsonify({"analysis": f"AI Generate Error: {str(e)}"})

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
        <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            :root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; --up: #00ff66; --down: #ff3333; }
            body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding-bottom: 100px; }
            .header { padding: 15px 0; text-align: center; background: rgba(10, 17, 24, 0.9); border-bottom: 2px solid var(--neon); position: sticky; top: 0; z-index: 1000; backdrop-filter: blur(10px); }
            .container { padding: 20px; max-width: 500px; margin: auto; }
            .pro-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; display: block; text-decoration: none; color: inherit; box-shadow: 0 0 15px var(--neon); cursor: pointer; }
            .pro-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; z-index: 1; }
            .inner-content { position: relative; z-index: 10; padding: 15px; display: flex; align-items: center; justify-content: space-between; }
            .score-circle { width: 65px; height: 65px; border-radius: 50%; border: 2px solid var(--neon); display: flex; flex-direction: column; justify-content: center; align-items: center; }
            .entry-ready { border-color: var(--up); box-shadow: 0 0 15px var(--up); color: var(--up); }
            .c-bar { width: 5px; border-radius: 1px; animation: glow 1.5s infinite; }
            @keyframes glow { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
            .footer { position: fixed; bottom: 0; width: 100%; background: rgba(10, 17, 24, 0.9); padding: 15px; text-align: center; border-top: 1px solid #21262d; font-size: 0.7rem; color: #8b949e; }
            
            /* -- SPA STYLES (NO RELOAD) -- */
            #dashboard-view { display: block; }
            #chart-view { display: none; }
            
            .action-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 20px; padding: 2px; overflow: hidden; width: 100%; box-shadow: 0 0 15px var(--neon); }
            .action-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; z-index: 1; }
            .inner { position: relative; z-index: 10; padding: 15px; text-align: center; }
            .p-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; text-align: left; }
            .p-item { background: rgba(0,242,255,0.05); padding: 8px; border-radius: 8px; border: 1px solid #21262d; }
            .p-label { font-size: 0.55rem; color: #8b949e; display: block; text-transform: uppercase; font-weight: bold; }
            .p-val { font-size: 0.85rem; color: var(--neon); font-weight: 900; }
        </style>
    </head>
    <body>
        <div class="header"><h1>🔱 {{title}}</h1><div id="ticker" style="color:var(--neon); font-weight:bold;"><span id="nifty_top">...</span> | <span id="bn_top">...</span></div></div>
        
        <div class="container" id="dashboard-view">
            <div id="terminal">Loading...</div>
        </div>

        <div class="container" id="chart-view">
            <button onclick="showDashboard()" style="background:#333; color:white; border:none; padding:10px 20px; border-radius:10px; margin-bottom:15px; font-weight:bold; cursor:pointer;">⬅️ BACK</button>
            
            <div class="action-card"><div class="inner"><h1 id="c_symbol" style="color:var(--neon); margin:0; font-size: 2rem;">...</h1></div></div>

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

            <div class="action-card">
                <div style="position:relative; z-index:10; width:100%; height:350px; border-radius:12px; overflow:hidden; background:#0d1117;">
                    <div id="tv_chart_container" style="height:100%; width:100%;"></div>
                </div>
            </div>

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
                </div>
            </div>

            <div class="action-card" onclick="fetchAI()" style="cursor:pointer;"><div class="inner"><span style="color: var(--neon); font-weight: bold; font-size: 0.8rem;">🤖 AI INSIGHT</span><div id="ai_insight" style="font-size: 0.85rem; color: var(--neon); margin-top: 10px; font-weight: bold;">विश्लेषणासाठी क्लिक करा...</div></div></div>
        </div>

        <div class="footer">{{credit}}</div>
        
        <script>
            const beep = new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg');
            let played = new Set();
            let currentSymbol = '';
            let chartInterval = null;
            
            // चार्ट चे वेरिएबल्स (नवीन)
            let chartInstance = null;
            let candleSeries = null;

            function openChart(symbol) {
                currentSymbol = symbol;
                document.getElementById('dashboard-view').style.display = 'none';
                document.getElementById('chart-view').style.display = 'block';
                document.getElementById('c_symbol').innerText = symbol;
                document.getElementById('ai_insight').innerText = "विश्लेषणासाठी क्लिक करा...";
                
                // --- जुना चार्ट क्लिअर करणे ---
                document.getElementById('tv_chart_container').innerHTML = '';

                // --- नवीन Real Chart तयार करणे (TradingView Widget नाही) ---
                chartInstance = LightweightCharts.createChart(document.getElementById('tv_chart_container'), {
                    width: document.getElementById('tv_chart_container').clientWidth,
                    height: 350,
                    layout: { backgroundColor: '#0d1117', textColor: '#d1d4dc' },
                    grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
                    timeScale: { timeVisible: true, secondsVisible: false },
                });

                candleSeries = chartInstance.addCandlestickSeries({
                    upColor: '#00ff66', downColor: '#ff3333', borderVisible: false, wickUpColor: '#00ff66', wickDownColor: '#ff3333'
                });

                // डेटा लोड करा
                loadCandleData(symbol);
                updateChartData(); // SMC डेटासाठी
                
                if(chartInterval) clearInterval(chartInterval);
                chartInterval = setInterval(() => {
                    loadCandleData(symbol);
                    updateChartData();
                }, 5000);
            }

            // नवीन फंक्शन चार्ट डेटा साठी
            async function loadCandleData(symbol) {
                try {
                    const r = await fetch('/api/history/' + symbol);
                    const data = await r.json();
                    if(data.length > 0) {
                        candleSeries.setData(data);
                    }
                } catch(e) { console.log("Chart Data Error"); }
            }

            function showDashboard() {
                document.getElementById('chart-view').style.display = 'none';
                document.getElementById('dashboard-view').style.display = 'block';
                if(chartInterval) clearInterval(chartInterval);
            }

            // तुमचं SMC लॉजिक जसेच्या तसे
            async function updateChartData() {
                if(!currentSymbol) return;
                try {
                    const r = await fetch('/api/smc_live/' + currentSymbol);
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
                } catch(e){}
            }

            async function fetchAI() { 
                if(!currentSymbol) return;
                document.getElementById('ai_insight').innerText = " विचार करत आहे... 🤔"; 
                const r = await fetch('/api/ai_analysis/' + currentSymbol); 
                const d = await r.json(); 
                document.getElementById('ai_insight').innerText = d.analysis; 
            }

            async function update() {
                if(document.getElementById('dashboard-view').style.display === 'none') return;

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
                        
                        // ONCLICK Function (No Page Reload)
                        html += `
                        <div onclick="openChart('${s.symbol}')" class="pro-card">
                            <div class="inner-content">
                                <div><span style="font-size:0.8rem; color:#8b949e; font-weight:bold;">${s.symbol}</span><div style="font-size:1.6rem; font-weight:900; color:var(--neon);">₹${s.price}</div></div>
                                <div class="score-circle ${glow}"><b style="font-size:1.1rem;">${s.score}%</b><span style="font-size:0.5rem; font-weight:bold;">${s.dir}</span></div>
                                <div style="display:flex; align-items:flex-end; gap:2px; height:45px;">
                                    <div class="c-bar" style="height:25px; background:${candleColor}"></div>
                                    <div class="c-bar" style="height:35px; background:${candleColor}"></div>
                                    <div class="c-bar" style="height:30px; background:${candleColor}"></div>
                                </div>
                            </div>
                        </div>`;
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
    return "Merged into Home Page"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
