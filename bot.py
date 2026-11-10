import os
import sys
import json
import time
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================================================
# SMART MONEY CRYPTO SIGNAL BOT - VERSION 5.4
# =========================================================

VERSION = "5.4"

BASE_URL = "https://data-api.binance.vision"
TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"

TOP_COINS = 20
MIN_CONFIRMATIONS = 5

VOLUME_MULTIPLIER = 0.90
ADX_MINIMUM = 14
ADX_CONFIRMATION = 18
MIN_ATR_RATIO = 0.0004

SWEEP_LOOKBACK = 8
LIQUIDITY_LOOKBACK = 12
MAX_STRUCTURE_BARS = 8

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

FVG_LOOKBACK = 25
FVG_MIN_ATR = 0.05
ORDER_BLOCK_LOOKBACK = 10

MIN_RISK_ATR = 0.75
MAX_RISK_ATR = 3.0
SWEEP_STOP_BUFFER = 0.12

STATE_FILE = "signals.json"

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

# =========================================================
# HTTP SESSION
# =========================================================

session = requests.Session()

retry = Retry(
    total=4,
    connect=4,
    read=4,
    backoff_factor=0.7,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.headers.update({
    "User-Agent": "CryptoSignalBot/5.4"
})

# =========================================================
# DIAGNOSTICS
# =========================================================

DIAGNOSTICS = {
    "insufficient_data": 0,
    "invalid_values": 0,
    "adx_rejected": 0,
    "volatility_rejected": 0,
    "confirmations_rejected": 0,
    "sweep_rejected": 0,
    "sweep_detected": 0,
    "structure_rejected": 0,
    "structure_detected": 0,
    "structure_no_pivot": 0,
    "structure_no_break": 0,
    "fvg_rejected": 0,
    "fvg_detected": 0,
    "fvg_retest_detected": 0,
    "order_block_detected": 0,
    "risk_rejected": 0,
    "errors": 0
}

STRUCTURE_DEBUG = []

# =========================================================
# TIME
# =========================================================

def iran_now():
    return datetime.now(IRAN_TZ)


def current_time_string():
    return iran_now().strftime("%Y-%m-%d %H:%M")


def today_string():
    return iran_now().strftime("%Y-%m-%d")


def is_trading_time():
    now = iran_now()
    return 8 <= now.hour < 22


def timestamp_string(timestamp):
    return datetime.fromtimestamp(
        timestamp / 1000,
        tz=IRAN_TZ
    ).strftime("%Y-%m-%d %H:%M")


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_text(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = session.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message
            },
            timeout=20
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print("Telegram rejected message.")
            return False

        print("Telegram message sent.")
        return True

    except Exception as error:
        print("Telegram ERROR:", error)
        return False


def send_signal(signal):
    direction = signal["direction"]

    label = "🟢 LONG" if direction == "BUY" else "🔴 SHORT"

    message = (
        "━━━━━━━━━━━━━━━━━━\n"
        f"🚨 SMART MONEY SIGNAL {VERSION}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 Symbol: {signal['symbol']}\n"
        f"📍 Direction: {label}\n"
        f"⏱ Timeframe: 5m\n"
        f"⭐ Confirmations: {signal['confirmations']}/7\n\n"
        "📊 SMC ANALYSIS\n"
        f"Liquidity Sweep: YES\n"
        f"Structure: {signal['structure']}\n"
        "FVG Retest: YES\n"
        f"Order Block: {'YES' if signal['order_block'] else 'NO'}\n\n"
        "💰 TRADE LEVELS\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"Stop Loss: {signal['stop_loss']:.8g}\n"
        f"TP1: {signal['tp1']:.8g} (1R)\n"
        f"TP2: {signal['tp2']:.8g} (2R)\n"
        f"TP3: {signal['tp3']:.8g} (3R)\n\n"
        "📈 INDICATORS\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"ADX: {signal['adx']:.2f}\n"
        f"Volume Ratio: {signal['volume_ratio']:.2f}\n\n"
        f"🕒 {signal['created_at']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    return send_telegram_text(message)


def send_event_message(signal, event):
    symbol = signal["symbol"]

    messages = {
        "TP1": (
            f"✅ TP1 HIT\n{symbol}\n"
            f"Price: {signal['tp1']:.8g}\n"
            "Milestone: 1R"
        ),
        "TP2": (
            f"🔥 TP2 HIT\n{symbol}\n"
            f"Price: {signal['tp2']:.8g}\n"
            "Milestone: 2R"
        ),
        "TP3": (
            f"🏆 TP3 HIT\n{symbol}\n"
            f"Price: {signal['tp3']:.8g}\n"
            "Milestone: 3R\n"
            "Signal completed."
        ),
        "SL": (
            f"🛑 STOP LOSS HIT\n{symbol}\n"
            f"Price: {signal['stop_loss']:.8g}\n"
            f"Previous milestone: {signal.get('result_stage', 'NONE')}"
        )
    }

    return send_telegram_text(messages[event])


# =========================================================
# STATE STORAGE
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "signals": [],
            "daily": {}
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

    except Exception as error:
        raise RuntimeError(
            f"Cannot safely load {STATE_FILE}: {error}"
        )

    if not isinstance(state, dict):
        raise RuntimeError("Invalid state file format.")

    state.setdefault("signals", [])
    state.setdefault("daily", {})

    if not isinstance(state["signals"], list):
        raise RuntimeError("Invalid signals history.")

    if not isinstance(state["daily"], dict):
        state["daily"] = {}

    for signal in state["signals"]:
        signal.setdefault("tp1_hit", False)
        signal.setdefault("tp2_hit", False)
        signal.setdefault("tp3_hit", False)
        signal.setdefault("telegram_events", {})
        signal.setdefault("pending_events", [])
        signal.setdefault("result_stage", "NONE")

        if "tp3" not in signal and "entry" in signal:
            entry = float(signal["entry"])
            stop = float(signal["stop_loss"])
            risk = abs(entry - stop)

            if signal.get("direction") == "BUY":
                signal["tp3"] = entry + 3 * risk
            else:
                signal["tp3"] = entry - 3 * risk

    return state


def save_state(state):
    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATE_FILE)


def get_daily_record(state):
    today = today_string()

    if today not in state["daily"]:
        state["daily"][today] = {
            "scans": 0,
            "signals": 0,
            "diagnostics": {}
        }

    record = state["daily"][today]

    record.setdefault("scans", 0)
    record.setdefault("signals", 0)
    record.setdefault("diagnostics", {})

    return record


# =========================================================
# MARKET DATA
# =========================================================

def get_market_coins():
    response = session.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        timeout=20
    )

    response.raise_for_status()

    excluded = {
        "USDC", "FDUSD", "TUSD", "USDP",
        "DAI", "USDE", "BUSD"
    }

    coins = []

    for item in response.json():
        symbol = item["symbol"]

        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]

        if base in excluded:
            continue

        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue

        try:
            volume = float(item["quoteVolume"])
        except (ValueError, TypeError):
            continue

        if volume > 0:
            coins.append((symbol, volume))

    coins.sort(
        key=lambda item: item[1],
        reverse=True
    )

    return [item[0] for item in coins[:TOP_COINS]]


def candles_to_dataframe(data):
    columns = [
        "time", "open", "high", "low", "close",
        "volume", "close_time", "quote_volume",
        "trades", "taker_buy", "taker_quote", "ignore"
    ]

    df = pd.DataFrame(data, columns=columns)

    if df.empty:
        return df

    numeric = [
        "time", "open", "high", "low",
        "close", "volume", "close_time"
    ]

    for col in numeric:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(subset=numeric)

    return df.reset_index(drop=True)


def get_candles(symbol, timeframe):
    response = session.get(
        f"{BASE_URL}/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": timeframe,
            "limit": 250
        },
        timeout=20
    )

    response.raise_for_status()

    df = candles_to_dataframe(response.json())

    if df.empty:
        return df

    now_ms = int(time.time() * 1000)

    return df[
        df["close_time"] < now_ms
    ].reset_index(drop=True)


def get_tracking_candles(symbol, start_time):
    cursor = int(start_time)
    now_ms = int(time.time() * 1000)

    all_candles = []

    while cursor < now_ms:
        response = session.get(
            f"{BASE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": TIMEFRAME,
                "startTime": cursor,
                "limit": 1000
            },
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if not data:
            break

        df = candles_to_dataframe(data)

        if df.empty:
            break

        closed = df[
            df["close_time"] < now_ms
        ].copy()

        if not closed.empty:
            all_candles.append(closed)

        last_time = int(df.iloc[-1]["time"])

        next_cursor = last_time + 300001

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        if len(data) < 1000:
            break

        time.sleep(0.1)

    if not all_candles:
        return pd.DataFrame()

    result = pd.concat(
        all_candles,
        ignore_index=True
    )

    return (
        result.drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )


# =========================================================
# INDICATORS
# =========================================================

def calculate_indicators(df):
    df = df.copy()

    close = df["close"]

    df["ema20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200,
        adjust=False
    ).mean()

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = (
        100 - 100 / (1 + rs)
    ).fillna(100)

    df["bb_mid"] = close.rolling(20).mean()

    std = close.rolling(20).std()

    df["bb_upper"] = df["bb_mid"] + 2 * std
    df["bb_lower"] = df["bb_mid"] - 2 * std

    df["volume_avg"] = df["volume"].rolling(20).mean()

    prev_close = close.shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0),
        0
    )

    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0),
        0
    )

    plus_smoothed = plus_dm.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    atr_adx = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    df["plus_di"] = (
        100 * plus_smoothed / atr_adx.replace(0, np.nan)
    )

    df["minus_di"] = (
        100 * minus_smoothed / atr_adx.replace(0, np.nan)
    )

    dx = (
        100 * (df["plus_di"] - df["minus_di"]).abs()
        / (df["plus_di"] + df["minus_di"]).replace(0, np.nan)
    )

    df["adx"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    return df


# =========================================================
# CONFIRMED PIVOTS
# =========================================================

def find_confirmed_swings(df, end_index):
    highs = []
    lows = []

    last_pivot = min(
        end_index - PIVOT_RIGHT,
        len(df) - PIVOT_RIGHT - 1
    )

    for i in range(
        PIVOT_LEFT,
        last_pivot + 1
    ):
        left = df.iloc[i - PIVOT_LEFT:i]
        right = df.iloc[i + 1:i + PIVOT_RIGHT + 1]

        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])

        if (
            high > float(left["high"].max())
            and high >= float(right["high"].max())
        ):
            highs.append({
                "index": i,
                "price": high
            })

        if (
            low < float(left["low"].min())
            and low <= float(right["low"].min())
        ):
            lows.append({
                "index": i,
                "price": low
            })

    return highs, lows


# =========================================================
# LIQUIDITY SWEEP
# =========================================================

def detect_sweep_candidates(df):
    if len(df) < LIQUIDITY_LOOKBACK + 5:
        return []

    last_index = len(df) - 1

    first_index = max(
        LIQUIDITY_LOOKBACK,
        last_index - SWEEP_LOOKBACK
    )

    candidates = []

    # The newest CLOSED candle is included.
    for i in range(
        last_index,
        first_index - 1,
        -1
    ):
        prior = df.iloc[
            i - LIQUIDITY_LOOKBACK:i
        ]

        if len(prior) < LIQUIDITY_LOOKBACK:
            continue

        candle = df.iloc[i]

        liq_low = float(prior["low"].min())
        liq_high = float(prior["high"].max())

        if (
            float(candle["low"]) < liq_low
            and float(candle["close"]) > liq_low
        ):
            candidates.append({
                "direction": "BUY",
                "index": i,
                "sweep_low": float(candle["low"]),
                "sweep_high": float(candle["high"]),
                "liquidity_level": liq_low,
                "time": int(candle["time"])
            })

        elif (
            float(candle["high"]) > liq_high
            and float(candle["close"]) < liq_high
        ):
            candidates.append({
                "direction": "SELL",
                "index": i,
                "sweep_low": float(candle["low"]),
                "sweep_high": float(candle["high"]),
                "liquidity_level": liq_high,
                "time": int(candle["time"])
            })

    return candidates


# =========================================================
# BOS / CHoCH STRUCTURE
# =========================================================

def detect_structure(df, sweep, symbol="UNKNOWN"):
    sweep_index = sweep["index"]
    direction = sweep["direction"]

    highs, lows = find_confirmed_swings(
        df,
        sweep_index - 1
    )

    if direction == "BUY":
        valid = [
            pivot for pivot in highs
            if pivot["index"] < sweep_index
        ]
        reference_type = "SWING_HIGH"

    else:
        valid = [
            pivot for pivot in lows
            if pivot["index"] < sweep_index
        ]
        reference_type = "SWING_LOW"

    if not valid:
        DIAGNOSTICS["structure_no_pivot"] += 1

        STRUCTURE_DEBUG.append({
            "symbol": symbol,
            "direction": direction,
            "reason": "NO_PIVOT",
            "sweep_time": sweep["time"]
        })

        return None

    pivot = valid[-1]

    reference = float(pivot["price"])

    end_index = min(
        len(df) - 1,
        sweep_index + MAX_STRUCTURE_BARS
    )

    best_close = None
    bars_checked = 0

    for j in range(sweep_index + 1, end_index + 1):
        candle = df.iloc[j]

        close = float(candle["close"])

        bars_checked += 1

        if best_close is None:
            best_close = close

        elif direction == "BUY":
            best_close = max(best_close, close)

        else:
            best_close = min(best_close, close)

        broken = (
            close > reference
            if direction == "BUY"
            else close < reference
        )

        if broken:
            name = "BOS"

            # Structure classification is deliberately
            # conservative when no reliable prior trend exists.
            if direction == "BUY":
                previous_lows = [
                    p["price"] for p in lows
                    if p["index"] < pivot["index"]
                ]

                if previous_lows and reference < max(previous_lows):
                    name = "CHoCH"

            else:
                previous_highs = [
                    p["price"] for p in highs
                    if p["index"] < pivot["index"]
                ]

                if previous_highs and reference > min(previous_highs):
                    name = "CHoCH"

            return {
                "name": name,
                "time": int(candle["close_time"]),
                "index": j,
                "reference": reference,
                "pivot_index": pivot["index"],
                "bars_checked": bars_checked
            }

    DIAGNOSTICS["structure_no_break"] += 1

    atr_value = float(df.iloc[sweep_index]["close"])

    STRUCTURE_DEBUG.append({
        "symbol": symbol,
        "direction": direction,
        "reason": "NO_STRUCTURE_BREAK",
        "reference": reference,
        "reference_type": reference_type,
        "best_close": best_close,
        "bars_checked": bars_checked,
        "sweep_time": sweep["time"]
    })

    return None


def find_sweep_structure(df15, symbol):
    candidates = detect_sweep_candidates(df15)

    if not candidates:
        return None, None, 0, False

    for sweep in candidates:
        structure = detect_structure(
            df15,
            sweep,
            symbol
        )

        if structure is not None:
            return (
                sweep,
                structure,
                len(candidates),
                True
            )

    return candidates[0], None, len(candidates), False


# =========================================================
# FAIR VALUE GAP + RETEST
# =========================================================

def detect_fvg_retest(df, direction, after_time, atr):
    if len(df) < 30:
        return None

    last_index = len(df) - 1

    start = max(
        2,
        last_index - FVG_LOOKBACK
    )

    last = df.iloc[last_index]

    counted = False

    for i in range(
        last_index - 1,
        start - 1,
        -1
    ):
        first = df.iloc[i - 2]
        third = df.iloc[i]

        if int(third["close_time"]) <= after_time:
            continue

        if direction == "BUY":
            if float(third["low"]) <= float(first["high"]):
                continue

            zone_low = float(first["high"])
            zone_high = float(third["low"])

        else:
            if float(third["high"]) >= float(first["low"]):
                continue

            zone_low = float(third["high"])
            zone_high = float(first["low"])

        if zone_high - zone_low < atr * FVG_MIN_ATR:
            continue

        later = df.iloc[i + 1:last_index]

        if direction == "BUY":
            invalidated = (
                not later.empty
                and (later["close"] < zone_low).any()
            )
        else:
            invalidated = (
                not later.empty
                and (later["close"] > zone_high).any()
            )

        if invalidated:
            continue

        if not counted:
            DIAGNOSTICS["fvg_detected"] += 1
            counted = True

        touched = (
            float(last["low"]) <= zone_high
            and float(last["high"]) >= zone_low
        )

        if direction == "BUY":
            confirmed = float(last["close"]) > float(last["open"])
        else:
            confirmed = float(last["close"]) < float(last["open"])

        if touched and confirmed:
            DIAGNOSTICS["fvg_retest_detected"] += 1

            return {
                "lower": zone_low,
                "upper": zone_high,
                "time": int(third["close_time"])
            }

    return None


# =========================================================
# ORDER BLOCK - SUPPORTING ONLY
# =========================================================

def detect_order_block(df, direction):
    if len(df) < ORDER_BLOCK_LOOKBACK + 2:
        return False

    recent = df.iloc[
        -(ORDER_BLOCK_LOOKBACK + 1):-1
    ]

    if direction == "BUY":
        return bool(
            (recent["close"] < recent["open"]).any()
        )

    return bool(
        (recent["close"] > recent["open"]).any()
    )


# =========================================================
# SIGNAL GENERATION
# =========================================================

def generate_signal(symbol, df5, df15):
    if len(df5) < 210 or len(df15) < 50:
        DIAGNOSTICS["insufficient_data"] += 1
        return None

    df5 = calculate_indicators(df5)
    df15 = calculate_indicators(df15)

    row = df5.iloc[-1]
    trend = df15.iloc[-1]

    required = [
        row["rsi"],
        row["atr"],
        row["adx"],
        row["volume_avg"],
        row["ema200"],
        row["bb_mid"],
        row["plus_di"],
        row["minus_di"],
        trend["ema20"],
        trend["ema50"],
        trend["ema200"]
    ]

    if not np.isfinite(required).all():
        DIAGNOSTICS["invalid_values"] += 1
        return None

    price = float(row["close"])
    atr = float(row["atr"])

    if price <= 0 or atr <= 0 or row["volume_avg"] <= 0:
        DIAGNOSTICS["invalid_values"] += 1
        return None

    if float(row["adx"]) < ADX_MINIMUM:
        DIAGNOSTICS["adx_rejected"] += 1
        return None

    if atr / price < MIN_ATR_RATIO:
        DIAGNOSTICS["volatility_rejected"] += 1
        return None

    volume_ratio = float(
        row["volume"] / row["volume_avg"]
    )

    sweep, structure, sweep_count, paired = find_sweep_structure(
        df15,
        symbol
    )

    if sweep_count == 0:
        DIAGNOSTICS["sweep_rejected"] += 1
        return None

    DIAGNOSTICS["sweep_detected"] += 1

    if not paired or structure is None:
        DIAGNOSTICS["structure_rejected"] += 1
        return None

    DIAGNOSTICS["structure_detected"] += 1

    direction = sweep["direction"]

    fvg = detect_fvg_retest(
        df5,
        direction,
        structure["time"],
        atr
    )

    if fvg is None:
        DIAGNOSTICS["fvg_rejected"] += 1
        return None

    order_block = detect_order_block(
        df5,
        direction
    )

    if order_block:
        DIAGNOSTICS["order_block_detected"] += 1

    if direction == "BUY":
        checks = [
            row["rsi"] > 50,
            row["close"] > row["bb_mid"],
            row["ema20"] > row["ema50"] > row["ema200"],
            volume_ratio >= VOLUME_MULTIPLIER,
            trend["ema20"] > trend["ema50"] > trend["ema200"],
            row["adx"] >= ADX_CONFIRMATION,
            row["plus_di"] > row["minus_di"]
        ]

    else:
        checks = [
            row["rsi"] < 50,
            row["close"] < row["bb_mid"],
            row["ema20"] < row["ema50"] < row["ema200"],
            volume_ratio >= VOLUME_MULTIPLIER,
            trend["ema20"] < trend["ema50"] < trend["ema200"],
            row["adx"] >= ADX_CONFIRMATION,
            row["minus_di"] > row["plus_di"]
        ]

    confirmations = sum(bool(x) for x in checks)

    if confirmations < MIN_CONFIRMATIONS:
        DIAGNOSTICS["confirmations_rejected"] += 1
        return None

    if direction == "BUY":
        stop_loss = min(
            price - atr * MIN_RISK_ATR,
            sweep["sweep_low"] - atr * SWEEP_STOP_BUFFER
        )

        risk = price - stop_loss

    else:
        stop_loss = max(
            price + atr * MIN_RISK_ATR,
            sweep["sweep_high"] + atr * SWEEP_STOP_BUFFER
        )

        risk = stop_loss - price

    if risk <= 0 or risk > atr * MAX_RISK_ATR:
        DIAGNOSTICS["risk_rejected"] += 1
        return None

    if direction == "BUY":
        tp1 = price + risk
        tp2 = price + risk * 2
        tp3 = price + risk * 3

    else:
        tp1 = price - risk
        tp2 = price - risk * 2
        tp3 = price - risk * 3

    signal_time = int(row["close_time"])

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": price,
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "risk": float(risk),
        "risk_atr": float(risk / atr),
        "confirmations": int(confirmations),
        "rsi": float(row["rsi"]),
        "adx": float(row["adx"]),
        "volume_ratio": volume_ratio,
        "atr": atr,
        "fvg_lower": fvg["lower"],
        "fvg_upper": fvg["upper"],
        "order_block": order_block,
        "structure": structure["name"],
        "structure_time": structure["time"],
        "sweep_time": sweep["time"],
        "signal_time": signal_time,
        "last_checked_time": signal_time,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "result_stage": "NONE",
        "status": "OPEN",
        "telegram_events": {},
        "pending_events": [],
        "created_at": current_time_string(),
        "created_date": today_string()
    }


# =========================================================
# EVENT QUEUE
# =========================================================

def queue_event(signal, event):
    sent = signal.setdefault("telegram_events", {})

    if sent.get(event):
        return

    pending = signal.setdefault("pending_events", [])

    if event not in pending:
        pending.append(event)


def process_pending_events(state):
    changed = False

    for signal in state["signals"]:
        pending = signal.setdefault("pending_events", [])

        for event in list(pending):
            if send_event_message(signal, event):
                signal.setdefault(
                    "telegram_events", {}
                )[event] = True

                pending.remove(event)
                changed = True
            else:
                # Keep failed notifications for a later run.
                break

    return changed


# =========================================================
# SIGNAL TRACKING
# =========================================================

def update_signal_results(state):
    changed = False

    for signal in state["signals"]:
        if signal.get("status") != "OPEN":
            continue

        symbol = signal.get("symbol")

        if not symbol:
            continue

        last_checked = int(
            signal.get(
                "last_checked_time",
                signal.get("signal_time", 0)
            )
        )

        try:
            candles = get_tracking_candles(
                symbol,
                last_checked + 1
            )

        except Exception as error:
            print(f"Tracking error {symbol}: {error}")
            continue

        if candles.empty:
            continue

        direction = signal["direction"]

        stop = float(signal["stop_loss"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal["tp2"])
        tp3 = float(signal["tp3"])

        for _, candle in candles.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            close_time = int(candle["close_time"])

            if direction == "BUY":
                stop_hit = low <= stop
                tp1_hit = high >= tp1
                tp2_hit = high >= tp2
                tp3_hit = high >= tp3

            else:
                stop_hit = high >= stop
                tp1_hit = low <= tp1
                tp2_hit = low <= tp2
                tp3_hit = low <= tp3

            # Conservative intrabar rule:
            # If stop and any target are both touched in
            # the same candle, stop takes priority.
            if stop_hit:
                signal["status"] = "SL"
                signal["result_time"] = close_time
                signal["result_price"] = stop

                queue_event(signal, "SL")

                changed = True
                break

            # Process only the highest newly reached
            # milestone in this candle. Do not award
            # multiple targets in the same candle.

            if not signal.get("tp1_hit", False):
                if tp1_hit:
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = close_time
                    signal["result_stage"] = "TP1"

                    queue_event(signal, "TP1")

                    changed = True
                    continue

            elif not signal.get("tp2_hit", False):
                if tp2_hit:
                    signal["tp2_hit"] = True
                    signal["tp2_time"] = close_time
                    signal["result_stage"] = "TP2"

                    queue_event(signal, "TP2")

                    changed = True
                    continue

            elif not signal.get("tp3_hit", False):
                if tp3_hit:
                    signal["tp3_hit"] = True
                    signal["tp3_time"] = close_time
                    signal["result_stage"] = "TP3"
                    signal["status"] = "TP3"
                    signal["result_time"] = close_time
                    signal["result_price"] = tp3

                    queue_event(signal, "TP3")

                    changed = True
                    break

        signal["last_checked_time"] = int(
            candles.iloc[-1]["close_time"]
        )

        changed = True

    return changed


# =========================================================
# DAILY REPORT
# =========================================================

def signal_created_date(signal):
    value = signal.get("created_date")

    if value:
        return str(value)[:10]

    value = signal.get("created_at", "")

    return str(value)[:10] if value else None


def create_daily_report(state):
    today = today_string()

    today_signals = [
        signal for signal in state["signals"]
        if signal_created_date(signal) == today
    ]

    tp1_count = sum(
        bool(s.get("tp1_hit"))
        for s in today_signals
    )

    tp2_count = sum(
        bool(s.get("tp2_hit"))
        for s in today_signals
    )

    tp3_count = sum(
        bool(s.get("tp3_hit"))
        for s in today_signals
    )

    sl_before_tp1 = sum(
        s.get("status") == "SL"
        and not s.get("tp1_hit", False)
        for s in today_signals
    )

    sl_after_tp1 = sum(
        s.get("status") == "SL"
        and s.get("tp1_hit", False)
        for s in today_signals
    )

    open_count = sum(
        s.get("status") == "OPEN"
        for s in today_signals
    )

    record = state.get("daily", {}).get(
        today,
        {
            "scans": 0,
            "signals": 0,
            "diagnostics": {}
        }
    )

    closed = sl_before_tp1 + sl_after_tp1 + tp3_count

    message = (
        "📊 DAILY PERFORMANCE REPORT\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Date: {today}\n\n"
        f"Scans completed: {record.get('scans', 0)}\n"
        f"New signals: {record.get('signals', 0)}\n\n"
        "TARGET MILESTONES\n"
        f"TP1 reached: {tp1_count}\n"
        f"TP2 reached: {tp2_count}\n"
        f"TP3 completed: {tp3_count}\n\n"
        "FINAL RESULTS\n"
        f"SL before TP1: {sl_before_tp1}\n"
        f"SL after TP1: {sl_after_tp1}\n"
        f"Still open: {open_count}\n\n"
        "Note: TP1/TP2 counts are milestone counts, "
        "not independent completed trades.\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    return message


# =========================================================
# SCANNER
# =========================================================

def run_scanner():
    print("=" * 45)
    print(f"SMART MONEY CRYPTO SIGNAL BOT v{VERSION}")
    print("Iran time:", current_time_string())
    print("=" * 45)

    if not is_trading_time():
        print("Outside trading hours.")
        return

    state = load_state()

    update_signal_results(state)
    process_pending_events(state)

    coins = get_market_coins()

    print(f"Scanning {len(coins)} coins...")

    new_signals = 0

    for symbol in coins:
        try:
            df5 = get_candles(symbol, TIMEFRAME)
            df15 = get_candles(symbol, TREND_TIMEFRAME)

            signal = generate_signal(
                symbol,
                df5,
                df15
            )

            if signal is None:
                continue

            duplicate = any(
                old.get("symbol") == symbol
                and int(old.get("signal_time", 0))
                == signal["signal_time"]
                for old in state["signals"]
            )

            if duplicate:
                continue

            state["signals"].append(signal)

            new_signals += 1

            print(
                f"NEW SIGNAL: {symbol} "
                f"{signal['direction']} "
                f"{signal['confirmations']}/7"
            )

            if not send_signal(signal):
                print(
                    f"Initial Telegram alert failed: {symbol}"
                )

        except Exception as error:
            DIAGNOSTICS["errors"] += 1
            print(f"ERROR {symbol}: {error}")

    record = get_daily_record(state)

    record["scans"] += 1
    record["signals"] += new_signals

    for key, value in DIAGNOSTICS.items():
        record["diagnostics"][key] = (
            record["diagnostics"].get(key, 0) + value
        )

    save_state(state)

    print("=" * 45)
    print("SCAN FINISHED")
    print("Coins scanned:", len(coins))
    print("Signals found:", new_signals)
    print("Diagnostics:", DIAGNOSTICS)

    print("=" * 45)
    print("STRUCTURE DEBUG DETAILS")

    for item in STRUCTURE_DEBUG:
        print(item)

    print("=" * 45)


# =========================================================
# DAILY REPORT RUNNER
# =========================================================

def run_report():
    state = load_state()

    update_signal_results(state)
    process_pending_events(state)

    message = create_daily_report(state)

    send_telegram_text(message)

    save_state(state)

    print(message)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1].lower() == "report":
            run_report()
        else:
            run_scanner()

    except Exception as error:
        print("FATAL ERROR:", error)
        raise
