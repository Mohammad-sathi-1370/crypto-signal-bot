import os
import sys
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ================= SETTINGS =================

BASE_URL = "https://data-api.binance.vision"
TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"

TOP_COINS = 20
MIN_CONFIRMATIONS = 5
VOLUME_MULTIPLIER = 1.2
ADX_MINIMUM = 20

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
STATE_FILE = "signals.json"

session = requests.Session()
session.headers.update({"User-Agent": "CryptoSignalBot/2.0"})

DIAGNOSTICS = {
    "insufficient_data": 0,
    "invalid_values": 0,
    "adx_rejected": 0,
    "volatility_rejected": 0,
    "volume_rejected": 0,
    "confirmations_rejected": 0,
    "direction_rejected": 0,
    "errors": 0
}


# ================= TELEGRAM =================

def send_telegram_text(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials are missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": message},
            timeout=20
        )
        response.raise_for_status()
        print("Telegram message sent.")
        return True

    except Exception as error:
        print("Telegram ERROR:", error)
        return False


def send_telegram_message(signal):
    message = (
        f"🚨 CRYPTO SIGNAL 🚨\n\n"
        f"Coin: {signal['symbol']}\n"
        f"Direction: {signal['direction']}\n"
        f"Confirmations: {signal['confirmations']}/7\n"
        f"ADX: {signal['adx']:.2f}\n\n"
        f"Entry: {signal['entry']:.10g}\n"
        f"Stop Loss: {signal['stop_loss']:.10g}\n"
        f"TP1: {signal['tp1']:.10g}\n"
        f"TP2: {signal['tp2']:.10g}\n\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"Volume Ratio: {signal['volume_ratio']:.2f}"
    )

    return send_telegram_text(message)


# ================= PERMANENT STORAGE =================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"signals": [], "daily": {}}

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        state = json.load(file)

    if not isinstance(state, dict):
        raise ValueError("Invalid signals.json format")

    state.setdefault("signals", [])
    state.setdefault("daily", {})

    return state


def save_state(state):
    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)

    os.replace(temp_file, STATE_FILE)


def get_today():
    return datetime.now(IRAN_TZ).strftime("%Y-%m-%d")


def get_daily_record(state):
    today = get_today()

    if today not in state["daily"]:
        state["daily"][today] = {
            "scans": 0,
            "signals": 0,
            "diagnostics": {
                key: 0 for key in DIAGNOSTICS
            }
        }

    return state["daily"][today]


# ================= TOP COINS =================

def get_market_coins():
    url = f"{BASE_URL}/api/v3/ticker/24hr"

    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()

    excluded = [
        "USDC", "FDUSD", "TUSD",
        "USDP", "DAI", "USDE", "BUSD"
    ]

    coins = []

    for item in data:
        symbol = item["symbol"]

        if not symbol.endswith("USDT"):
            continue

        if symbol[:-4] in excluded:
            continue

        quote_volume = float(item["quoteVolume"])

        if quote_volume > 0:
            coins.append((symbol, quote_volume))

    coins.sort(key=lambda x: x[1], reverse=True)

    return [coin[0] for coin in coins[:TOP_COINS]]


# ================= CANDLES =================

def get_candles(symbol, timeframe, start_time=None):
    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": timeframe,
        "limit": 250
    }

    if start_time is not None:
        params["startTime"] = int(start_time)
        params["limit"] = 1000

    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()

    columns = [
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy", "taker_quote", "ignore"
    ]

    df = pd.DataFrame(response.json(), columns=columns)

    if df.empty:
        return df

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["time"] = df["time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")

    # Only closed candles
    return df.iloc[:-1].copy().reset_index(drop=True)


# ================= INDICATORS =================

def calculate_indicators(df):
    df = df.copy()
    close = df["close"]

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(100)

    df["bb_mid"] = close.rolling(20).mean()
    std = close.rolling(20).std()

    df["bb_upper"] = df["bb_mid"] + 2 * std
    df["bb_lower"] = df["bb_mid"] - 2 * std

    df["volume_avg"] = df["volume"].rolling(20).mean()

    previous_close = close.shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()

    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0), 0
    )

    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0), 0
    )

    atr_adx = tr.ewm(alpha=1/14, adjust=False).mean()

    plus_smoothed = plus_dm.ewm(
        alpha=1/14, adjust=False
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=1/14, adjust=False
    ).mean()

    df["plus_di"] = 100 * (
        plus_smoothed / atr_adx.replace(0, np.nan)
    )

    df["minus_di"] = 100 * (
        minus_smoothed / atr_adx.replace(0, np.nan)
    )

    dx = 100 * (
        (df["plus_di"] - df["minus_di"]).abs()
        /
        (df["plus_di"] + df["minus_di"]).replace(0, np.nan)
    )

    df["adx"] = dx.ewm(alpha=1/14, adjust=False).mean()

    return df


# ================= ANALYZE COIN =================

def analyze_coin(symbol):
    try:
        df5 = calculate_indicators(
            get_candles(symbol, TIMEFRAME)
        )

        df15 = calculate_indicators(
            get_candles(symbol, TREND_TIMEFRAME)
        )

        if len(df5) < 210 or len(df15) < 210:
            DIAGNOSTICS["insufficient_data"] += 1
            return None

        current = df5.iloc[-1]
        previous = df5.iloc[-2]
        trend = df15.iloc[-1]

        required_values = [
            current["close"], current["open"],
            current["rsi"], current["ema20"],
            current["ema50"], current["ema200"],
            current["bb_mid"], current["bb_upper"],
            current["bb_lower"], current["volume"],
            current["volume_avg"], current["atr"],
            current["adx"], current["plus_di"],
            current["minus_di"], previous["rsi"],
            previous["high"], previous["low"],
            trend["close"], trend["ema20"], trend["ema50"]
        ]

        if any(pd.isna(value) for value in required_values):
            DIAGNOSTICS["invalid_values"] += 1
            return None

        price = float(current["close"])
        atr = float(current["atr"])
        adx = float(current["adx"])
        plus_di = float(current["plus_di"])
        minus_di = float(current["minus_di"])

        if price <= 0 or atr <= 0 or current["volume_avg"] <= 0:
            DIAGNOSTICS["invalid_values"] += 1
            return None

        if adx < ADX_MINIMUM:
            DIAGNOSTICS["adx_rejected"] += 1
            return None

        if atr / price < 0.001:
            DIAGNOSTICS["volatility_rejected"] += 1
            return None

        volume_ratio = current["volume"] / current["volume_avg"]

        if volume_ratio <= VOLUME_MULTIPLIER:
            DIAGNOSTICS["volume_rejected"] += 1
            return None

        buy_checks = [
            current["ema20"] > current["ema50"],
            price > current["ema200"],
            trend["ema20"] > trend["ema50"]
            and trend["close"] > trend["ema50"],
            current["rsi"] > 50
            and current["rsi"] > previous["rsi"],
            price > current["bb_mid"]
            and price <= current["bb_upper"] * 1.01,
            current["volume"] > current["volume_avg"] * VOLUME_MULTIPLIER,
            current["close"] > current["open"]
            and current["close"] > previous["high"]
        ]

        sell_checks = [
            current["ema20"] < current["ema50"],
            price < current["ema200"],
            trend["ema20"] < trend["ema50"]
            and trend["close"] < trend["ema50"],
            current["rsi"] < 50
            and current["rsi"] < previous["rsi"],
            price < current["bb_mid"]
            and price >= current["bb_lower"] * 0.99,
            current["volume"] > current["volume_avg"] * VOLUME_MULTIPLIER,
            current["close"] < current["open"]
            and current["close"] < previous["low"]
        ]

        buy_score = sum(buy_checks)
        sell_score = sum(sell_checks)

        if buy_score >= MIN_CONFIRMATIONS and buy_score > sell_score:
            if plus_di <= minus_di:
                DIAGNOSTICS["direction_rejected"] += 1
                return None

            direction = "BUY"
            score = buy_score

        elif sell_score >= MIN_CONFIRMATIONS and sell_score > buy_score:
            if minus_di <= plus_di:
                DIAGNOSTICS["direction_rejected"] += 1
                return None

            direction = "SELL"
            score = sell_score

        else:
            DIAGNOSTICS["confirmations_rejected"] += 1
            return None

        risk = atr * 1.5

        if direction == "BUY":
            stop_loss = price - risk
            tp1 = price + risk
            tp2 = price + (risk * 2)
        else:
            stop_loss = price + risk
            tp1 = price - risk
            tp2 = price - (risk * 2)

        return {
            "symbol": symbol,
            "direction": direction,
            "confirmations": int(score),
            "entry": price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "rsi": float(current["rsi"]),
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "volume_ratio": float(volume_ratio),
            "signal_time": int(current["close_time"]) + 1,
            "signal_date": get_today(),
            "tp1_hit": False,
            "status": "OPEN"
        }

    except Exception as error:
        DIAGNOSTICS["errors"] += 1
        print(f"{symbol} ERROR:", error)
        return None


# ================= SAVE NEW SIGNALS =================

def add_new_signals(state, signals):
    record = get_daily_record(state)

    for signal in signals:
        signal_id = (
            f"{signal['symbol']}_"
            f"{signal['direction']}_"
            f"{signal['signal_time']}"
        )

        if any(
            item.get("id") == signal_id
            for item in state["signals"]
        ):
            continue

        signal["id"] = signal_id
        state["signals"].append(signal)
        record["signals"] += 1

        print("Saved signal:", signal_id)


# ================= TRACK SIGNAL RESULTS =================

def update_signal_results(state):
    for signal in state["signals"]:

        if signal.get("status") != "OPEN":
            continue

        try:
            df = get_candles(
                signal["symbol"],
                TIMEFRAME,
                signal["signal_time"]
            )

            if df.empty:
                continue

            for _, candle in df.iterrows():

                high = float(candle["high"])
                low = float(candle["low"])

                entry = float(signal["entry"])
                sl = float(signal["stop_loss"])
                tp1 = float(signal["tp1"])
                tp2 = float(signal["tp2"])

                if signal["direction"] == "BUY":
                    sl_hit = low <= sl
                    tp1_hit = high >= tp1
                    tp2_hit = high >= tp2
                else:
                    sl_hit = high >= sl
                    tp1_hit = low <= tp1
                    tp2_hit = low <= tp2

                # Conservative rule: SL first
                if sl_hit:
                    signal["status"] = "SL"
                    signal["result_time"] = int(candle["close_time"])
                    print("SL reached:", signal["symbol"])
                    break

                if tp2_hit:
                    signal["tp1_hit"] = True
                    signal["status"] = "TP2"
                    signal["result_time"] = int(candle["close_time"])
                    print("TP2 reached:", signal["symbol"])
                    break

                if tp1_hit and not signal.get("tp1_hit"):
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = int(candle["close_time"])
                    print("TP1 reached:", signal["symbol"])

        except Exception as error:
            print(
                "Tracking ERROR:",
                signal["symbol"],
                error
            )


# ================= DAILY DIAGNOSTICS =================

def add_diagnostics(state):
    record = get_daily_record(state)

    record["scans"] += 1

    for key, value in DIAGNOSTICS.items():
        record["diagnostics"][key] += value


# ================= DAILY REPORT =================

def send_daily_report(state):
    today = get_today()
    record = state["daily"].get(today)

    if record is None:
        record = {
            "scans": 0,
            "signals": 0,
            "diagnostics": {
                key: 0 for key in DIAGNOSTICS
            }
        }

    today_signals = [
        signal for signal in state["signals"]
        if signal.get("signal_date") == today
    ]

    total = len(today_signals)

    tp1_count = sum(
        1 for signal in today_signals
        if signal.get("tp1_hit")
    )

    tp2_count = sum(
        1 for signal in today_signals
        if signal.get("status") == "TP2"
    )

    sl_count = sum(
        1 for signal in today_signals
        if signal.get("status") == "SL"
    )

    open_count = sum(
        1 for signal in today_signals
        if signal.get("status") == "OPEN"
    )

    resolved = tp2_count + sl_count

    win_rate = (
        (tp2_count / resolved) * 100
        if resolved > 0 else 0
    )

    diagnostics = record["diagnostics"]

    message = (
        f"📊 DAILY BOT PERFORMANCE\n"
        f"Date: {today}\n\n"
        f"Scans completed: {record['scans']}\n"
        f"Total signals: {total}\n\n"
        f"🎯 TP1 reached: {tp1_count}\n"
        f"🏆 TP2 reached: {tp2_count}\n"
        f"🛑 Stop Loss: {sl_count}\n"
        f"⏳ Still open: {open_count}\n\n"
        f"Win rate (TP2 vs SL): {win_rate:.1f}%\n\n"
        f"📋 DIAGNOSTICS\n"
        f"ADX rejected: {diagnostics['adx_rejected']}\n"
        f"Low volatility: {diagnostics['volatility_rejected']}\n"
        f"Low volume: {diagnostics['volume_rejected']}\n"
        f"Low confirmations: {diagnostics['confirmations_rejected']}\n"
        f"DI rejected: {diagnostics['direction_rejected']}\n"
        f"Insufficient data: {diagnostics['insufficient_data']}\n"
        f"Invalid values: {diagnostics['invalid_values']}\n"
        f"Errors: {diagnostics['errors']}\n\n"
        f"Report time: "
        f"{datetime.now(IRAN_TZ).strftime('%H:%M')}"
    )

    send_telegram_text(message)


# ================= MAIN SCAN =================

def main():
    for key in DIAGNOSTICS:
        DIAGNOSTICS[key] = 0

    now = datetime.now(IRAN_TZ)

    print("=" * 45)
    print("CRYPTO SIGNAL BOT - PERFORMANCE VERSION")
    print("Iran time:", now.strftime("%Y-%m-%d %H:%M"))
    print("=" * 45)

    if not (8 <= now.hour < 22):
        print("Outside active hours. No scanning.")
        return

    state = load_state()

    # Update previously issued signals
    update_signal_results(state)

    coins = get_market_coins()
    print(f"Scanning {len(coins)} coins...")

    signals = []

    for symbol in coins:
        result = analyze_coin(symbol)

        if result:
            signals.append(result)

        time.sleep(0.2)

    print("\n========== SCAN RESULTS ==========")
    print("Coins scanned:", len(coins))
    print("Signals found:", len(signals))

    print("\n========== DIAGNOSTICS ==========")

    for key, value in DIAGNOSTICS.items():
        print(key, ":", value)

    add_new_signals(state, signals)
    add_diagnostics(state)

    for signal in signals:
        print(
            signal["symbol"],
            signal["direction"],
            signal["confirmations"],
            "/ 7"
        )

        send_telegram_message(signal)

    save_state(state)

    print("\nBOT SCAN COMPLETED")


# ================= REPORT MODE =================

def report_mode():
    print("Starting daily report...")

    state = load_state()

    # Update outcomes before preparing report
    update_signal_results(state)

    send_daily_report(state)

    save_state(state)

    print("DAILY REPORT COMPLETED")


# ================= START =================

if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report_mode()
    else:
        main()
