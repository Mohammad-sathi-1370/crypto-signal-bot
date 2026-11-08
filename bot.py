import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------- SETTINGS ----------------

BASE_URL = "https://data-api.binance.vision"

IRAN_TZ = ZoneInfo("Asia/Tehran")

SCAN_LIMIT = 20
MIN_CONFIRMATIONS = 5
VOLUME_MULTIPLIER = 1.2

TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"

session = requests.Session()
session.headers.update({"User-Agent": "CryptoSignalScanner/2.0"})


# ---------------- TIME ----------------

def iran_time():
    return datetime.now(timezone.utc).astimezone(IRAN_TZ)


# ---------------- BINANCE DATA ----------------

def get_market_coins():
    url = f"{BASE_URL}/api/v3/ticker/24hr"
    response = session.get(url, timeout=20)
    response.raise_for_status()

    tickers = response.json()

    excluded = (
        "USDCUSDT", "FDUSDUSDT", "TUSDUSDT",
        "USDPUSDT", "EURUSDT", "DAIUSDT"
    )

    leveraged_tags = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

    coins = []

    for item in tickers:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if symbol in excluded:
            continue

        if any(symbol.endswith(tag) for tag in leveraged_tags):
            continue

        try:
            quote_volume = float(item["quoteVolume"])
            price = float(item["lastPrice"])

            if quote_volume > 0 and price > 0:
                coins.append((symbol, quote_volume))
        except (ValueError, KeyError, TypeError):
            continue

    coins.sort(key=lambda x: x[1], reverse=True)

    return [coin[0] for coin in coins[:SCAN_LIMIT]]


def get_candles(symbol, interval, limit=250):
    url = f"{BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()

    if len(data) < 205:
        return None

    columns = [
        "open_time", "open", "high", "low", "close",
        "volume", "close_time", "quote_volume",
        "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ]

    df = pd.DataFrame(data, columns=columns)

    numeric_columns = [
        "open", "high", "low", "close", "volume"
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # حذف کندل باز؛ فقط کندل‌های بسته‌شده بررسی می‌شوند.
    df = df.iloc[:-1].copy()
    df.dropna(subset=numeric_columns, inplace=True)

    if len(df) < 205:
        return None

    return df.reset_index(drop=True)


# ---------------- INDICATORS ----------------

def add_indicators(df):
    df = df.copy()

    close = df["close"]

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    df.loc[
        (avg_loss == 0) & (avg_gain > 0), "rsi"
    ] = 100

    df.loc[
        (avg_loss == 0) & (avg_gain == 0), "rsi"
    ] = 50

    df["bb_mid"] = close.rolling(20).mean()
    bb_std = close.rolling(20).std(ddof=0)

    df["bb_upper"] = df["bb_mid"] + (2 * bb_std)
    df["bb_lower"] = df["bb_mid"] - (2 * bb_std)

    # میانگین حجم ۲۰ کندل قبلی، بدون احتساب کندل فعلی
    df["volume_avg"] = df["volume"].rolling(20).mean().shift(1)

    previous_close = close.shift(1)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - previous_close).abs()
    tr3 = (df["low"] - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3], axis=1
    ).max(axis=1)

    df["atr"] = true_range.rolling(14).mean()

    return df


# ---------------- ANALYSIS ----------------

def analyze_coin(symbol):
    try:
        df = get_candles(symbol, TIMEFRAME)
        trend_df = get_candles(symbol, TREND_TIMEFRAME)

        if df is None or trend_df is None:
            return None

        df = add_indicators(df)
        trend_df = add_indicators(trend_df)

        current = df.iloc[-1]
        trend = trend_df.iloc[-1]

        required_values = [
            current["close"],
            current["rsi"],
            current["ema20"],
            current["ema50"],
            current["ema200"],
            current["bb_mid"],
            current["volume"],
            current["volume_avg"],
            current["atr"],
            trend["close"],
            trend["ema50"]
        ]

        if not np.isfinite(required_values).all():
            return None

        price = float(current["close"])
        rsi = float(current["rsi"])
        atr = float(current["atr"])
        volume = float(current["volume"])
        volume_avg = float(current["volume_avg"])

        if price <= 0 or atr <= 0 or volume_avg <= 0:
            return None

        # حجم اجباری: کمتر یا مساوی ۱.۲ یعنی رد سیگنال
        volume_ratio = volume / volume_avg

        if volume_ratio <= VOLUME_MULTIPLIER:
            return None

        # هفت تأیید خرید
        buy_checks = [
            price > current["ema20"],
            current["ema20"] > current["ema50"],
            current["ema50"] > current["ema200"],
            50 < rsi < 70,
            price > current["bb_mid"],
            trend["close"] > trend["ema50"],
            volume_ratio > VOLUME_MULTIPLIER
        ]

        # هفت تأیید فروش
        sell_checks = [
            price < current["ema20"],
            current["ema20"] < current["ema50"],
            current["ema50"] < current["ema200"],
            30 < rsi < 50,
            price < current["bb_mid"],
            trend["close"] < trend["ema50"],
            volume_ratio > VOLUME_MULTIPLIER
        ]

        buy_score = sum(buy_checks)
        sell_score = sum(sell_checks)

        if buy_score >= MIN_CONFIRMATIONS and buy_score > sell_score:
            direction = "BUY"
            confirmations = buy_score

            stop_loss = price - (1.5 * atr)
            risk = price - stop_loss

            take_profit_1 = price + risk
            take_profit_2 = price + (2 * risk)

        elif sell_score >= MIN_CONFIRMATIONS and sell_score > buy_score:
            direction = "SELL"
            confirmations = sell_score

            stop_loss = price + (1.5 * atr)
            risk = stop_loss - price

            take_profit_1 = price - risk
            take_profit_2 = price - (2 * risk)

        else:
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "confirmations": confirmations,
            "entry": price,
            "stop_loss": stop_loss,
            "tp1": take_profit_1,
            "tp2": take_profit_2,
            "rsi": rsi,
            "volume_ratio": volume_ratio
        }

    except Exception as error:
        print(f"Error analyzing {symbol}: {error}")
        return None


# ---------------- MAIN ----------------

def main():
    now = iran_time()

    print("=" * 45)
    print("CRYPTO SIGNAL BOT - VERSION 2")
    print("Iran time:", now.strftime("%Y-%m-%d %H:%M"))
    print("=" * 45)

    # فقط از ساعت ۹ صبح تا ۹ شب ایران
    if not (9 <= now.hour < 21):
        print("Outside operating hours. Bot stopped.")
        return

    try:
        coins = get_market_coins()

        print(f"Scanning {len(coins)} coins...")
        print("Timeframes: 5m + 15m")
        print("Minimum confirmations:", MIN_CONFIRMATIONS)
        print("Mandatory volume ratio >", VOLUME_MULTIPLIER)
        print("-" * 45)

        signals = []

        for index, symbol in enumerate(coins, start=1):
            print(f"[{index}/{len(coins)}] Checking {symbol}...")

            signal = analyze_coin(symbol)

            if signal:
                signals.append(signal)

            time.sleep(0.15)

        print("\nSCAN FINISHED")
        print("Signals found:", len(signals))
        print("-" * 45)

        for signal in signals:
            print(
                f"\n{signal['symbol']} {signal['direction']}"
            )
            print(
                f"Confirmations: {signal['confirmations']}/7"
            )
            print(f"Entry: {signal['entry']:.10g}")
            print(f"Stop Loss: {signal['stop_loss']:.10g}")
            print(f"TP1: {signal['tp1']:.10g}")
            print(f"TP2: {signal['tp2']:.10g}")
            print(f"RSI: {signal['rsi']:.2f}")
            print(f"Volume ratio: {signal['volume_ratio']:.2f}")

        print("\nBOT SCAN COMPLETED")

    except Exception as error:
        print("\nFATAL ERROR:", error)
        raise


if __name__ == "__main__":
    main()
