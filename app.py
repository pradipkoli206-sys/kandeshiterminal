import os
import pyotp
import time
import threading
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- KEYS ---
API_KEY = os.environ.get("API_KEY")
CLIENT_ID = os.environ.get("CLIENT_ID")
PASSWORD = os.environ.get("PASSWORD")
TOTP_KEY = os.environ.get("TOTP_KEY")

live_data = {} 

# --- STOCKS ---
TOKEN_MAP = {
    "RELIANCE": "2885", "TATASTEEL": "3499", "HDFCBANK": "1333", "INFY": "1594",
    "SBIN": "3045", "ICICIBANK": "4963", "AXISBANK": "5900", "WIPRO": "3787"
}
STOCKS = [{"name": k, "token": v} for k, v in TOKEN_MAP.items()]

# --- ENGINE ---
def start_engine():
    global live_data
    smart_api = None
    while True:
        try:
            if smart_api is None:
                print("Connecting...")
                totp = pyotp.TOTP(TOTP_KEY).now()
                smart_api = SmartConnect(api_key=API_KEY)
                data = smart_api.generateSession(CLIENT_ID, PASSWORD, totp)
                if data['status']:
                    print("Login Success")
                else:
                    time.sleep(5)
                    continue
            
            for name, token in TOKEN_MAP.items():
                try:
                    res = smart_api.ltpData("NSE", name + "-EQ", token)
                    if res and res['status']:
                        live_data[token] = res['data']['ltp']
                except:
                    pass
                time.sleep(0.1)
            time.sleep(1)
        except:
            smart_api = None
            time.sleep(5)

t = threading.Thread(target=start_engine)
t.daemon = True
t.start()

# --- SIMPLE HTML (NO BLACK SCREEN ISSUE) ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Simple Trader</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: sans-serif; padding: 20px; text-align: center; }
        .card { border: 1px solid #ccc; margin: 10px; padding: 15px; border-radius: 5px; box-shadow: 2px 2px 5px #eee; }
        .price { font-size: 1.5em; color: green; font-weight: bold; }
        h1 { color: #333; }
    </style>
    <script>
        setInterval(function(){ location.reload(); }, 2000);
    </script>
</head>
<body>
    <h1>📈 कान्हादेशी मार्केट</h1>
    <p>Status: Running</p>
    
    {% for stock in stocks %}
    <div class="card">
        <h3>{{ stock.name }}</h3>
        <div class="price">₹ {{ stock.price }}</div>
    </div>
    {% endfor %}

</body>
</html>
'''

@app.route('/')
def index():
    for stock in STOCKS:
        token = stock["token"]
        stock["price"] = live_data.get(token, 0.00)
    return render_template_string(HTML_TEMPLATE, stocks=STOCKS)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
