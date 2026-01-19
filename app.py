import os
import requests
import re
from flask import Flask, render_template_string, jsonify, request
from datetime import datetime
import pytz
from NorenRestApiPy.NorenApi import NorenApi
import pyotp

app = Flask(__name__)

# --- BRANDING ---
APP_TITLE = "कान्हादेशी ट्रेडर: प्रो टर्मिनल"
CREDIT = "P. KOLI | THE DISCIPLINE TRADER"

STOCKS = ["SOUTHBANK", "UCOBANK", "CENTRALBK", "IDFCFIRSTB", "RTNINDIA", "RELIANCE", "ZOMATO", "IRFC", "TATASTEEL", "PNB"]

# --- SHOONYA API CONNECTION ---
class ShoonyaApiPy(NorenApi):
    def __init__(self):
        NorenApi.__init__(self, host='https://api.finvasia.com/NorenWSTP/', websocket='wss://api.finvasia.com/NorenWSTP/')

api = ShoonyaApiPy()
is_logged_in = False

def login_to_shoonya():
    global is_logged_in
    try:
        user    = os.environ.get('SH_USER')
        pwd     = os.environ.get('SH_PWD')
        vc      = os.environ.get('SH_VC')
        apikey  = os.environ.get('SH_APIKEY')
        token   = os.environ.get('SH_TOTP_TOKEN')

        if not all([user, pwd, vc, apikey, token]): return False

        otp = pyotp.TOTP(token).now()
        ret = api.login(userid=user, password=pwd, twoFA=otp, vendor_code=vc, api_secret=apikey, imei='12345')
        
        if ret and ret.get('stat') == 'Ok':
            is_logged_in = True
            return True
        return False
    except: return False

# सुरुवातीला लॉगिन प्रयत्न
login_to_shoonya()

def fetch_live_index():
    try:
        if not is_logged_in: login_to_shoonya()
        # Nifty 50 चा डेटा Shoonya कडून
        quote = api.get_quotes(exchange='NSE', tradingsymbol='Nifty 50-INDEX')
        if quote and quote.get('stat') == 'Ok':
            nifty_change = float(quote['pc'])
            if nifty_change > 0.15: return f"UP {nifty_change}% 📈"
            elif nifty_change < -0.15: return f"DOWN {nifty_change}% 📉"
            else: return f"SIDEWAYS {nifty_change}% ↔️"
        return "NIFTY: LIVE"
    except: return "SIDEWAYS ↔️"

def get_ultra_pro_data(ticker):
    try:
        if not is_logged_in: login_to_shoonya()
        # स्टॉकचा डेटा Shoonya कडून (Real-Time)
        quote = api.get_quotes(exchange='NSE', tradingsymbol=f'{ticker}-EQ')
        
        if not quote or quote.get('stat') != 'Ok': return None
        
        price_val = float(quote['lp'])
        change = float(quote['pc'])
        high_val = float(quote['h'])
        low_val = float(quote['l'])
        
        price = "{:.2f}".format(price_val)
        score = int(75 + (change * 4))

        # --- REAL-TIME SMC LOGIC (Using Shoonya Precise Data) ---
        trend_sync = "YES ✅" if abs(change) > 0.7 else "NO ❌"
        liq_sweep = "YES ✅" if abs(change) > 1.4 else "NO ❌"
        
        # खऱ्या हाय-लो वर आधारित Order Block
        if change > 0:
            ob_zone = f"₹{low_val} - ₹{round(low_val * 1.004, 2)}"
        else:
            ob_zone = f"₹{round(high_val * 0.996, 2)} - ₹{high_val}"
        
        fvg_status = "DETECTED 🔥" if abs(change) > 1.2 else "STABLE ⚖️"

        return {
            "symbol": ticker, "price": price, "score": max(10, min(99, score)),
            "htf_sync": trend_sync, "liq_sweep": liq_sweep, "ob_zone": ob_zone, "fvg": fvg_status
        }
    except: return None

# --- बाकी सर्व राऊट्स (मूळ कोडप्रमाणेच) ---

@app.route('/api/pro_feed')
def api_pro_feed():
    nifty_status = fetch_live_index()
    tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(tz)
    is_open = "OPEN" if (now.weekday() < 5 and (9,15) <= (now.hour, now.minute) <= (15,30)) else "CLOSED"
    res = [get_ultra_pro_data(s) for s in STOCKS]
    valid_stocks = sorted([x for x in res if x], key=lambda x: x['score'], reverse=True)
    return jsonify({"stocks": valid_stocks, "nifty": nifty_status, "market_status": is_open})

@app.route('/api/smc_live/<symbol>')
def api_smc_live(symbol):
    data = get_ultra_pro_data(symbol)
    return jsonify(data) if data else jsonify({"error": "not found"})

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
            .top-bar { display: flex; justify-content: space-between; padding: 0 20px; font-size: 0.7rem; color: var(--neon); margin-bottom: 5px; }
            .dot { width: 6px; height: 6px; background: var(--neon); border-radius: 50%; display: inline-block; margin-right: 5px; animation: blink 1s infinite; }
            @keyframes blink { 0%, 100% { opacity: 0; } 50% { opacity: 1; } }
            .container { padding: 20px; max-width: 500px; margin: auto; }
            .pro-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; display: block; text-decoration: none; color: inherit; box-shadow: 0 0 15px var(--neon); }
            .pro-card::before { content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: conic-gradient(transparent, transparent, var(--neon)); animation: rotate 5s linear infinite; }
            .pro-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; }
            @keyframes rotate { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
            .inner-content { position: relative; z-index: 10; padding: 15px; display: flex; align-items: center; justify-content: space-between; }
            .score-circle { position: relative; width: 65px; height: 65px; border-radius: 50%; display: flex; flex-direction: column; justify-content: center; align-items: center; background: #0d1117; }
            .score-circle::before { content: ''; position: absolute; inset: -3px; border-radius: 50%; padding: 3px; background: conic-gradient(transparent, var(--neon)); animation: rotateCircle 3s linear infinite; -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); -webkit-mask-composite: destination-out; mask-composite: exclude; }
            @keyframes rotateCircle { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
            .c-bar { width: 5px; border-radius: 1px; animation: glow 1.5s infinite; }
            .footer { position: fixed; bottom: 0; width: 100%; background: rgba(10, 17, 24, 0.9); padding: 15px; text-align: center; border-top: 1px solid #21262d; font-size: 0.7rem; color: #8b949e; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="top-bar"><div id="clock">00:00:00</div><div><span class="dot"></span> AI ANALYSIS: ACTIVE</div></div>
            <h1>🔱 {{title}}</h1>
            <div id="ticker" style="font-weight: bold; color: var(--neon); font-size: 0.85rem;">NIFTY 50: LOADING...</div>
        </div>
        <div class="container" id="terminal"></div>
        <div class="footer">{{credit}}</div>
        <script>
            function updateClock() { document.getElementById('clock').innerText = new Date().toLocaleTimeString('en-GB'); }
            setInterval(updateClock, 1000); updateClock();
            async function update() {
                try {
                    const r = await fetch('/api/pro_feed');
                    const data = await r.json();
                    document.getElementById('ticker').innerHTML = `NIFTY 50: ${data.nifty} | STATUS: ${data.market_status}`;
                    let html = '';
                    data.stocks.forEach(s => {
                        let isUp = s.score >= 75;
                        let candleColor = isUp ? 'var(--up)' : 'var(--down)';
                        let candleHtml = '';
                        for(let i=1; i<=8; i++) {
                            let h = isUp ? (i * 3 + 10) : (40 - (i * 3));
                            candleHtml += `<div class="c-bar" style="height:${h}px; background:${candleColor}; animation-delay: ${i*0.1}s"></div>`;
                        }
                        html += `
                        <a href="/chart/${s.symbol}" class="pro-card">
                            <div class="inner-content">
                                <div style="text-align:left;">
                                    <span style="font-size:0.85rem; color:#8b949e; font-weight:bold;">${s.symbol}</span>
                                    <div style="font-size:1.6rem; font-weight:900; color:var(--neon);">₹${s.price}</div>
                                </div>
                                <div class="score-circle">
                                    <b style="font-size:1.1rem;">${s.score}%</b>
                                    <span style="font-size:0.45rem; color:var(--neon); font-weight:bold;">SCORE</span>
                                </div>
                                <div style="display:flex; align-items:flex-end; gap:2px; height:45px;">${candleHtml}</div>
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
    ''', title=APP_TITLE, credit=CREDIT)

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
            :root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; }
            body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding: 20px; padding-top: 80px; display: flex; flex-direction: column; align-items: center; }
            .top-left-back { position: absolute; top: 20px; left: 20px; z-index: 100; }
            .btn-ui { color: #8b949e; text-decoration: none; font-weight: bold; font-size: 0.85rem; padding: 8px 15px; background: rgba(13, 17, 23, 0.9); border-radius: 8px; border: 1px solid #21262d; display: inline-block; }
            .action-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; width: 95%; max-width: 400px; box-shadow: 0 0 15px var(--neon); display: block; text-decoration: none; color: inherit; }
            .action-card::before { content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: conic-gradient(transparent, transparent, var(--neon)); animation: rotate 5s linear infinite; }
            .action-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; }
            @keyframes rotate { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
            .inner-box { position: relative; z-index: 5; padding: 20px; text-align: center; }
        </style>
    </head>
    <body>
        <div class="top-left-back"><a href="/" class="btn-ui">⬅️ BACK</a></div>
        <div class="action-card"><div class="inner-box">
            <h1 style="color: #00f2ff; margin:0;">{{symbol}}</h1>
            <a href="https://in.tradingview.com/chart/?symbol=NSE:{{symbol}}" target="_blank" style="background:#00f2ff; color:#000; padding:10px 20px; border-radius:8px; font-weight:bold; text-decoration:none; display:inline-block; margin-top:10px;">🔱 VIEW CHART</a>
        </div></div>

        <a href="/smc_details/{{symbol}}" class="action-card">
            <div class="inner-box">
                <span style="color: #00f2ff; font-weight: bold; font-size: 0.75rem; letter-spacing: 1px; text-transform: uppercase;">Smart Money Concept (SMC)</span>
                <p style="font-size: 0.7rem; color: #8b949e; margin-top: 5px;">रिअल-टाइम ॲनालिसिससाठी क्लिक करा</p>
            </div>
        </a>

        <div class="action-card"><div class="inner-box">Indicator Analytics</div></div>
        <div class="action-card"><div class="inner-box">Market Confirmation</div></div>
        <div class="action-card"><div class="inner-box">Trade Execution</div></div>
        <div class="action-card"><div class="inner-box">Risk & Discipline</div></div>
    </body>
    </html>
    ''', symbol=symbol)

@app.route('/smc_details/<symbol>')
def smc_details(symbol):
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root { --bg: #02040a; --card: #0d1117; --neon: #00f2ff; }
            body { background: var(--bg); color: #fff; font-family: sans-serif; margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; }
            .action-card { position: relative; background: var(--card); border-radius: 20px; margin-bottom: 25px; padding: 2px; overflow: hidden; width: 100%; max-width: 400px; box-shadow: 0 0 15px var(--neon); }
            .action-card::before { content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: conic-gradient(transparent, transparent, var(--neon)); animation: rotate 5s linear infinite; }
            .action-card::after { content: ''; position: absolute; inset: 4px; background: #0d1117; border-radius: 16px; }
            @keyframes rotate { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
            .inner { position: relative; z-index: 5; padding: 25px; text-align: center; }
            .val { color: var(--neon); font-size: 1.8rem; font-weight: 900; margin-top: 10px; display: block; }
        </style>
    </head>
    <body>
        <div style="width:100%; text-align:left; max-width:400px;"><a href="javascript:history.back()" style="color:#8b949e; text-decoration:none; font-weight:bold;">⬅️ BACK</a></div>
        <h2 style="color:var(--neon); margin: 20px 0;">SMC CORE: {{symbol}}</h2>

        <div class="action-card"><div class="inner">
            <span style="font-size: 0.8rem; color: #8b949e; font-weight: bold; text-transform: uppercase;">15M & 1H Trend Sync</span>
            <span class="val" id="sync">LIVE...</span>
        </div></div>

        <div class="action-card"><div class="inner">
            <span style="font-size: 0.8rem; color: #8b949e; font-weight: bold; text-transform: uppercase;">Liquidity Sweep Status</span>
            <span class="val" id="liq">LIVE...</span>
        </div></div>

        <div class="action-card"><div class="inner">
            <span style="font-size: 0.8rem; color: #8b949e; font-weight: bold; text-transform: uppercase;">Order Block Detection Zone</span>
            <span class="val" id="ob">LIVE...</span>
        </div></div>

        
        <div class="action-card"><div class="inner">
            <span style="font-size: 0.8rem; color: #8b949e; font-weight: bold; text-transform: uppercase;">FVG Detection (Imbalance)</span>
            <span class="val" id="fvg">LIVE...</span>
        </div></div>

        <div class="action-card"><div class="inner">Card 5: Waiting...</div></div>

        <script>
            async function updateSMC() {
                try {
                    const r = await fetch('/api/smc_live/{{symbol}}');
                    const d = await r.json();
                    document.getElementById('sync').innerText = d.htf_sync;
                    document.getElementById('liq').innerText = d.liq_sweep;
                    document.getElementById('ob').innerText = d.ob_zone;
                    document.getElementById('fvg').innerText = d.fvg;
                } catch(e) {}
            }
            setInterval(updateSMC, 5000); updateSMC();
        </script>
    </body>
    </html>
    ''', symbol=symbol)

if __name__ == '__main__':
    # Render साठी पोर्ट सेटिंग
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
