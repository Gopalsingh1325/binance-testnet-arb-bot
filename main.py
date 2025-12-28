import os
import json
import time
import threading
import requests
import websocket

# =================================================
# CONFIG
# =================================================
BASES = ["BNB", "ETH"]

FEE = 0.001
SLIPPAGE = 0.002
MIN_EDGE = 0.0015

START_BALANCE = 1000.0
TRADE_SIZE = 50
MAX_TRADES = 50
COOLDOWN = 60
MIN_LIQ_USDT = 500

# =================================================

prices = {}
last_trade = {}
balance = START_BALANCE
trade_count = 0
total_pnl = 0.0

INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"

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

# ---------- PAPER TRADE ----------
def paper_trade(pair, direction, edge):
    global balance, trade_count, total_pnl

    if trade_count >= MAX_TRADES or balance < TRADE_SIZE:
        return

    profit = TRADE_SIZE * edge
    balance += profit
    total_pnl += profit
    trade_count += 1

    print("\n" + "=" * 60)
    print("📄 PAPER TRADE EXECUTED")
    print(f"PAIR      : {pair}")
    print(f"DIRECTION : {direction}")
    print(f"EDGE      : {edge*100:.3f}%")
    print(f"PROFIT    : {profit:.2f} USDT")
    print(f"BALANCE   : {balance:.2f} USDT")
    print(f"TRADES    : {trade_count}")

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

        a_bid, a_ask, a_bq, a_aq = a
        b_bid, b_ask, b_bq, b_aq = b
        c_bid, c_ask, c_bq, c_aq = c

        if min(
            a_bid * a_bq,
            b_bid * b_bq,
            c_bid * c_bq
        ) < MIN_LIQ_USDT:
            continue

        # PATH 1
        amt = 1 / a_ask * (1 - FEE)
        amt = amt * b_bid * (1 - FEE)
        final1 = amt * c_bid * (1 - FEE)

        # PATH 2
        amt = 1 / c_ask * (1 - FEE)
        amt = amt / b_ask * (1 - FEE)
        final2 = amt * a_bid * (1 - FEE)

        best = max(final1, final2)
        edge = best - 1 - SLIPPAGE

        if edge > MIN_EDGE:
            last_trade[key] = now
            direction = (
                "USDT → ALT → BASE → USDT"
                if final1 > final2
                else "USDT → BASE → ALT → USDT"
            )
            paper_trade(f"{alt}/{base}", direction, edge)

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
    print("🟢 Binance WebSocket connected")

def on_error(ws, err):
    print("⚠ WebSocket error:", err)

def start_ws():
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/stream?streams=" + "/".join(STREAMS),
        on_message=on_message,
        on_open=on_open,
        on_error=on_error
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)

print("🤖 Arbitrage Bot LIVE (Paper Mode – Cloud Safe)")
start_ws()
