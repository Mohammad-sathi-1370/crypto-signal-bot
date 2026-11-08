
import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# SETTINGS
BASE_URL = "https://data-api.binance.vision"
TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"
TOP_COINS = 20
MIN_CONFIRMATIONS = 5
VOLUME_MULTIPLIER = 1.2
ADX_MINIMUM = 20
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

session = requests.Session()
session.headers.update({"User-Agent": "CryptoSignalBot/1.0"})

# DIAGNOSTICS
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


# TELEGRAM
def send_telegram_message(signal):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials are missing.")
        return

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

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": message},
            timeout=15
        )
        response.raise_for_status()
        print("Telegram message sent:", signal["symbol"])
    except Exception as error:
        print("Telegram ERROR:", error)


# TOP COINS
def get_market_coins():
    url = f"{BASE_URL}/api/v3/ticker/24hr"
    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    excluded = ["USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "BUSD"]

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


# CANDLES
def get_candles(symbol, timeframe):
    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": timeframe,
        "limit": 250
    }

    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()

    columns = [
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy", "taker_quote", "ignore"
    ]

    df = pd.DataFrame(response.json(), columns=columns)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df.iloc[:-1].copy().reset_index(drop=True)


# INDICATORS
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


# ANALYZE COIN
def analyze_coin(symbol):
    try:
        df5 = calculate_indicators(get_candles(symbol, TIMEFRAME))
        df15 = calculate_indicators(get_candles(symbol, TREND_TIMEFRAME))

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
            trend["ema20"] > trend["ema50"] and trend["close"] > trend["ema50"],
            current["rsi"] > 50 and current["rsi"] > previous["rsi"],
            price > current["bb_mid"] and price <= current["bb_upper"] * 1.01,
            current["volume"] > current["volume_avg"] * VOLUME_MULTIPLIER,
            current["close"] > current["open"] and current["close"] > previous["high"]
        ]

        sell_checks = [
            current["ema20"] < current["ema50"],
            price < current["ema200"],
            trend["ema20"] < trend["ema50"] and trend["close"] < trend["ema50"],
            current["rsi"] < 50 and current["rsi"] < previous["rsi"],
            price < current["bb_mid"] and price >= current["bb_lower"] * 0.99,
            current["volume"] > current["volume_avg"] * VOLUME_MULTIPLIER,
            current["close"] < current["open"] and current["close"] < previous["low"]
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
            "confirmations": score,
            "entry": price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "rsi": float(current["rsi"]),
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "volume_ratio": volume_ratio
        }

    except Exception as error:
        DIAGNOSTICS["errors"] += 1
        print(f"{symbol} ERROR: {error}")
        return None


# MAIN
def main():
    for key in DIAGNOSTICS:
        DIAGNOSTICS[key] = 0

    now = datetime.now(IRAN_TZ)

    print("=" * 45)
    print("CRYPTO SIGNAL BOT - DIAGNOSTIC VERSION")
    print("Iran time:", now.strftime("%Y-%m-%d %H:%M"))
    print("=" * 45)

    if not (9 <= now.hour < 21):
        print("Outside active hours. No scanning.")
        return

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
    print("ADX rejected:", DIAGNOSTICS["adx_rejected"])
    print("Low volatility:", DIAGNOSTICS["volatility_rejected"])
    print("Low volume:", DIAGNOSTICS["volume_rejected"])
    print("Not enough confirmations:", DIAGNOSTICS["confirmations_rejected"])
    print("DI direction rejected:", DIAGNOSTICS["direction_rejected"])
    print("Insufficient candle data:", DIAGNOSTICS["insufficient_data"])
    print("Invalid indicator values:", DIAGNOSTICS["invalid_values"])
    print("Errors:", DIAGNOSTICS["errors"])

    for signal in signals:
        print("\n-----------------------------")
        print(signal["symbol"], signal["direction"])
        print("Confirmations:", signal["confirmations"], "/ 7")
        print("Entry:", signal["entry"])
        print("Stop Loss:", signal["stop_loss"])
        print("TP1:", signal["tp1"])
        print("TP2:", signal["tp2"])
        print("RSI:", round(signal["rsi"], 2))
        print("ADX:", round(signal["adx"], 2))
        print("Volume ratio:", round(signal["volume_ratio"], 2))

        send_telegram_message(signal)

    print("\nBOT SCAN COMPLETED")


if __name__ == "__main__":
    main()
