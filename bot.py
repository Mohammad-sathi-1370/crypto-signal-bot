import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.binance.com"

TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"
TOP_COINS = 20

MIN_CONFIRMATIONS = 5
VOLUME_MULTIPLIER = 1.2

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def get_iran_time():
    return datetime.now(IRAN_TZ)


def get_market_coins():
    url = f"{BASE_URL}/api/v3/ticker/24hr"
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()

    excluded = [
        "USDC", "FDUSD", "TUSD", "USDP",
        "DAI", "USDE", "BUSD"
    ]

    coins = []

    for item in data:
        symbol = item["symbol"]

        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]

        if base in excluded:
            continue

        if float(item["quoteVolume"]) <= 0:
            continue

        coins.append((
            symbol,
            float(item["quoteVolume"])
        ))

    coins.sort(key=lambda x: x[1], reverse=True)

    return [coin[0] for coin in coins[:TOP_COINS]]


def get_candles(symbol, timeframe):
    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": timeframe,
        "limit": 250
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    columns = [
        "time", "open", "high", "low",
        "close", "volume", "close_time",
        "quote_volume", "trades",
        "taker_buy", "taker_quote", "ignore"
    ]

    df = pd.DataFrame(data, columns=columns)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # آخرین کندل ممکن است هنوز باز باشد
    df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


def calculate_indicators(df):
    df = df.copy()

    close = df["close"]

    # EMA
    df["ema20"] = close.ewm(
        span=20, adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50, adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200, adjust=False
    ).mean()

    # RSI 14
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1/14, adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1/14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(100)

    # Bollinger Bands
    df["bb_mid"] = close.rolling(20).mean()

    std = close.rolling(20).std()

    df["bb_upper"] = df["bb_mid"] + 2 * std
    df["bb_lower"] = df["bb_mid"] - 2 * std

    # Volume average
    df["volume_avg"] = df["volume"].rolling(20).mean()

    # ATR 14
    previous_close = close.shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1/14, adjust=False
    ).mean()

    return df


def analyze_coin(symbol):
    try:
        df5 = get_candles(symbol, TIMEFRAME)
        df15 = get_candles(symbol, TREND_TIMEFRAME)

        if len(df5) < 210 or len(df15) < 210:
            return None

        df5 = calculate_indicators(df5)
        df15 = calculate_indicators(df15)

        current = df5.iloc[-1]
        previous = df5.iloc[-2]
        trend = df15.iloc[-1]

        price = current["close"]
        atr = current["atr"]

        if pd.isna(atr) or atr <= 0:
            return None

        if price <= 0:
            return None

        # فیلتر نوسان بسیار کم
        if atr / price < 0.001:
            return None

        # هفت تأیید خرید
        buy_checks = [
            current["ema20"] > current["ema50"],

            price > current["ema200"],

            trend["ema20"] > trend["ema50"]
            and trend["close"] > trend["ema50"],

            current["rsi"] > 50
            and current["rsi"] > previous["rsi"],

            price > current["bb_mid"]
            and price <= current["bb_upper"] * 1.01,

            current["volume"] >
            current["volume_avg"] * VOLUME_MULTIPLIER,

            current["close"] > current["open"]
            and current["close"] > previous["high"]
        ]

        # هفت تأیید فروش
        sell_checks = [
            current["ema20"] < current["ema50"],

            price < current["ema200"],

            trend["ema20"] < trend["ema50"]
            and trend["close"] < trend["ema50"],

            current["rsi"] < 50
            and current["rsi"] < previous["rsi"],

            price < current["bb_mid"]
            and price >= current["bb_lower"] * 0.99,

            current["volume"] >
            current["volume_avg"] * VOLUME_MULTIPLIER,

            current["close"] < current["open"]
            and current["close"] < previous["low"]
        ]

        buy_score = sum(buy_checks)
        sell_score = sum(sell_checks)

        if buy_score >= MIN_CONFIRMATIONS:
            direction = "BUY"

        elif sell_score >= MIN_CONFIRMATIONS:
            direction = "SELL"

        else:
            return None

        # جلوگیری از وضعیت متناقض
        if buy_score >= 5 and sell_score >= 5:
            return None

        risk = atr * 1.5

        if direction == "BUY":
            stop_loss = price - risk
            target1 = price + risk
            target2 = price + (risk * 2)

        else:
            stop_loss = price + risk
            target1 = price - risk
            target2 = price - (risk * 2)

        return {
            "symbol": symbol,
            "direction": direction,
            "score": max(buy_score, sell_score),
            "price": price,
            "stop_loss": stop_loss,
            "target1": target1,
            "target2": target2,
            "rsi": current["rsi"],
            "volume_ratio": (
                current["volume"] /
                current["volume_avg"]
            )
        }

    except Exception as error:
        print(f"{symbol} ERROR: {error}")
        return None


def main():
    now = get_iran_time()

    print("=" * 45)
    print("CRYPTO SIGNAL BOT")
    print("Iran time:", now.strftime("%Y-%m-%d %H:%M"))
    print("=" * 45)

    # کنترل ساعات فعالیت
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

        time.sleep(0.15)

    print("\nSCAN FINISHED")
    print("Signals found:", len(signals))

    for signal in signals:
        print("\n-----------------------------")

        print(
            signal["symbol"],
            signal["direction"]
        )

        print(
            "Confirmations:",
            signal["score"],
            "/ 7"
        )

        print("Entry:", signal["price"])
        print("Stop Loss:", signal["stop_loss"])
        print("Target 1:", signal["target1"])
        print("Target 2:", signal["target2"])

        print("RSI:", round(signal["rsi"], 2))

        print(
            "Volume ratio:",
            round(signal["volume_ratio"], 2)
        )

    print("\nBOT SCAN COMPLETED")


if __name__ == "__main__":
    main()
