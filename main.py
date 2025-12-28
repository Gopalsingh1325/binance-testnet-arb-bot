import os
import time
import json
import threading
from collections import defaultdict
from binance.client import Client
import websocket
import requests

# =================================================
# CONFIG
# =================================================
BASES = ["BNB", "ETH"]

FEE = 0.001
SLIPPAGE = 0.002
MIN_EDGE = 0.0015

TRADE_USDT = 50
MAX_TRADES_PER_DAY = 20
COOLDOWN = 120          # seconds per triangle
MIN_LIQ_USDT = 500

# =================================================

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET, testnet=True)

prices = {}
last_trade = {}
trade_lock = threading.Lock()
daily_trades = 0
daily_pnl = 0.0

INFO_URL = "https://testnet.binance.vision/api/v3/exchangeInfo"

# ---------- SYMBOL DISCOVERY ----------
def get_symbols():
    r = requests.get(INFO_URL, timeout=10).json()
    return {s["symbol"] for s in r["symbols"] if s["status"] == "TRADING"}

SYMBOLS = get_symbols()

TRIANGLES = []
STREAMS = set()

for s in SYMBOLS:
    if not s.endswith("USDT"):
        continue
    alt = s.replace("USDT", "")
    for base in BASES:
        if alt + base in SYMBOLS and base + "USDT" in SYMBOLS:
            TRIANGLES.append((alt, base))
            STREAMS.update([
                f"{alt.lower()}usdt@bookTicker",
                f"{alt.lower()}{base.lower()}@bookTicker",
                f"{base.lower()}usdt@bookTicker"
            ])

print(f"✔ Triangles: {len(TRIANGLES)}")
print(f"✔ Streams: {len(STREAMS)}")

# ---------- ORDER HELPERS ----------
def place_limit(symbol, side, qty, price):
    return client.create_order(
        symbol=symbol,
        side=side,
        type="LIMIT",
        timeInForce="IOC",
        quantity=round(qty, 6),
        price=f"{price:.6f}"
    )

def cancel_all(symbol):
    try:
        client.cancel_open_orders(symbol=symbol)
    except:
        pass

# ---------- ARBITRAGE EXECUTION ----------
def execute_triangle(alt, base, direction, edge):
    global daily_trades, daily_pnl

    with trade_lock:
        if daily_trades >= MAX_TRADES_PER_DAY:
            return

        print("\n🚀 EXECUTING TRIANGLE", alt, base, direction)

        try:
            if direction == 1:
                # USDT → ALT → BASE → USDT
                a_bid, a_ask, *_ = prices[alt + "USDT"]
                b_bid, b_ask, *_ = prices[alt + base]
                c_bid, c_ask, *_ = prices[base + "USDT"]

                qty_alt = TRADE_USDT / a_ask
                place_limit(alt + "USDT", "BUY", qty_alt, a_ask)
                place_limit(alt + base, "SELL", qty_alt, b_bid)
                place_limit(base + "USDT", "SELL", qty_alt * b_bid, c_bid)

            else:
                # USDT → BASE → ALT → USDT
                a_bid, a_ask, *_ = prices[alt + "USDT"]
                b_bid, b_ask, *_ = prices[alt + base]
                c_bid, c_ask, *_ = prices[base + "USDT"]

                qty_base = TRADE_USDT / c_ask
                place_limit(base + "USDT", "BUY", qty_base, c_ask)
                place_limit(alt + base, "BUY", qty_base / b_ask, b_ask)
                place_limit(alt + "USDT", "SELL", qty_base / b_ask, a_bid)

            daily_trades += 1
            pnl = TRADE_USDT * edge
            daily_pnl += pnl

            print(f"✅ DONE | EDGE {edge*100:.3f}% | PnL ≈ {pnl:.2f} USDT")

        except Exception as e:
            cancel_all(alt + "USDT")
            cancel_all(alt + base)
            cancel_all(base + "USDT")
            print("❌ FAILED – ORDERS CANCELLED", e)

# ---------- ARBITRAGE CHECK ----------
def check_arbitrage():
    now = time.time()

    for alt, base in TRIANGLES:
        key = f"{alt}-{base}"
        if now - last_trade.get(key, 0) < COOLDOWN:
            continue

        try:
            a = prices[alt + "USDT"]
            b = prices[alt + base]
            c = prices[base + "USDT"]
        except KeyError:
            continue

        a_bid, a_ask, a_bq, *_ = a
        b_bid, b_ask, b_bq, *_ = b
        c_bid, c_ask, c_bq, *_ = c

        if min(a_bid * a_bq, b_bid * b_bq, c_bid * c_bq) < MIN_LIQ_USDT:
            continue

        amt = 1 / a_ask * (1 - FEE)
        amt = amt * b_bid * (1 - FEE)
        final1 = amt * c_bid * (1 - FEE)

        amt = 1 / c_ask * (1 - FEE)
        amt = amt / b_ask * (1 - FEE)
        final2 = amt * a_bid * (1 - FEE)

        best = max(final1, final2)
        edge = best - 1 - SLIPPAGE

        if edge > MIN_EDGE:
            last_trade[key] = now
            direction = 1 if final1 > final2 else 2
            execute_triangle(alt, base, direction, edge)

# ---------- WEBSOCKET ----------
def on_message(ws, msg):
    d = json.loads(msg).get("data")
    if not d:
        return
    prices[d["s"]] = (
        float(d["b"]),
        float(d["a"]),
        float(d["B"]),
        float(d["A"])
    )
    check_arbitrage()

def on_open(ws):
    print("🟢 WebSocket connected")

def start_ws():
    ws = websocket.WebSocketApp(
        "wss://testnet.binance.vision/stream?streams=" + "/".join(STREAMS),
        on_message=on_message,
        on_open=on_open
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)

print("🤖 Binance Testnet Arbitrage Bot LIVE")
start_ws()