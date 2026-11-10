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
# SMART MONEY CRYPTO SIGNAL BOT - VERSION 5.7.1
# =========================================================

VERSION = "5.7.1"

BASE_URL = "https://data-api.binance.vision"

TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"

TOP_COINS = 20
MIN_CONFIRMATIONS = 3

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

# Pullback settings
PULLBACK_LOOKBACK = 12
PULLBACK_TOLERANCE_ATR = 0.30
PULLBACK_STOP_BUFFER_ATR = 0.15
MAX_ENTRY_DISTANCE_ATR = 0.50

MAX_STRUCTURE_AGE_MINUTES = 120

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
    "User-Agent": f"CryptoSignalBot/{VERSION}"
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
    "structure_waiting": 0,
    "structure_expired": 0,
    "structure_confirmed": 0,
    "fvg_rejected": 0,
    "fvg_detected": 0,
    "fvg_retest_detected": 0,
    "order_block_detected": 0,
    "pullback_waiting": 0,
    "pullback_rejected": 0,
    "pullback_confirmed": 0,
    "entry_distance_rejected": 0,
    "risk_rejected": 0,
    "risk_rejected_invalid": 0,
    "risk_rejected_too_wide": 0,
    "duplicate_signals": 0,
    "errors": 0
}

STRUCTURE_DEBUG = []
REJECTION_DEBUG = []

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
    return 8 <= iran_now().hour < 22


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
        "⏱ Timeframe: 5m\n"
        f"⭐ Confirmations: {signal['confirmations']}/7\n\n"
        "📊 SMC ANALYSIS\n"
        "Liquidity Sweep: YES\n"
        f"Structure: {signal['structure']}\n"
        f"Entry Model: {signal.get('entry_model', 'PULLBACK')}\n"
        f"FVG Context: {'YES' if signal.get('fvg_retest') else 'NO'}\n"
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
        f"Volume Ratio: {signal['volume_ratio']:.2f}\n"
        f"Risk: {signal['risk_atr']:.2f} ATR\n\n"
        f"🕒 {signal['created_at']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    return send_telegram_text(message)


def send_event_message(signal, event):
    symbol = signal["symbol"]

    if event == "ENTRY":
        return send_signal(signal)

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

    if event not in messages:
        return False

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

        signal.setdefault("tp1_time", None)
        signal.setdefault("tp2_time", None)
        signal.setdefault("tp3_time", None)

        signal.setdefault("result_time", None)
        signal.setdefault("result_price", None)

        if "tp3" not in signal and "entry" in signal:
            entry = float(signal["entry"])
            stop = float(signal["stop_loss"])
            risk = abs(entry - stop)

            if signal.get("direction") == "BUY":
                signal["tp3"] = entry + 3 * risk
            else:
                signal["tp3"] = entry - 3 * risk

        if signal.get("tp3_hit"):
            signal["tp2_hit"] = True
            signal["tp1_hit"] = True
            signal["result_stage"] = "TP3"

        elif signal.get("tp2_hit"):
            signal["tp1_hit"] = True
            signal["result_stage"] = "TP2"

        elif signal.get("tp1_hit"):
            signal["result_stage"] = "TP1"

        signal.setdefault(
            "telegram_events",
            {}
        ).setdefault("ENTRY", True)

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

    df = df[
        (df["high"] >= df["low"])
        & (df["high"] >= df["open"])
        & (df["high"] >= df["close"])
        & (df["low"] <= df["open"])
        & (df["low"] <= df["close"])
        & (df["volume"] >= 0)
    ]

    df = df.drop_duplicates(subset=["time"])
    df = df.sort_values("time")

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
        next_cursor = last_time + 300000

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

    df["rsi"] = 100 - 100 / (1 + rs)

    df.loc[
        (avg_loss == 0) & (avg_gain > 0),
        "rsi"
    ] = 100

    df.loc[
        (avg_gain == 0) & (avg_loss > 0),
        "rsi"
    ] = 0

    df.loc[
        (avg_gain == 0) & (avg_loss == 0),
        "rsi"
    ] = 50

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

    for i in range(PIVOT_LEFT, last_pivot + 1):
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
# STRUCTURE TREND
# =========================================================

def get_structure_trend(highs, lows, before_index):
    prior_highs = [
        p for p in highs
        if p["index"] < before_index
    ]

    prior_lows = [
        p for p in lows
        if p["index"] < before_index
    ]

    if len(prior_highs) < 2 or len(prior_lows) < 2:
        return "NEUTRAL"

    h1, h2 = prior_highs[-2], prior_highs[-1]
    l1, l2 = prior_lows[-2], prior_lows[-1]

    higher_high = h2["price"] > h1["price"]
    lower_high = h2["price"] < h1["price"]

    higher_low = l2["price"] > l1["price"]
    lower_low = l2["price"] < l1["price"]

    if higher_high and higher_low:
        return "UPTREND"

    if lower_high and lower_low:
        return "DOWNTREND"

    return "NEUTRAL"


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
# BOS / CHoCH
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
            "state": "NO_PIVOT",
            "sweep_time": sweep["time"]
        })

        return None

    pivot = valid[-1]
    reference = float(pivot["price"])

    available_bars = len(df) - 1 - sweep_index

    bars_checked = min(
        available_bars,
        MAX_STRUCTURE_BARS
    )

    for j in range(
        sweep_index + 1,
        sweep_index + bars_checked + 1
    ):
        candle = df.iloc[j]
        close = float(candle["close"])

        broken = (
            close > reference
            if direction == "BUY"
            else close < reference
        )

        if not broken:
            continue

        trend = get_structure_trend(
            highs,
            lows,
            pivot["index"]
        )

        if direction == "BUY" and trend == "DOWNTREND":
            name = "CHoCH"

        elif direction == "SELL" and trend == "UPTREND":
            name = "CHoCH"

        else:
            name = "BOS"

        atr_value = float(
            df.iloc[sweep_index]["atr"]
            if "atr" in df.columns
            else 0
        )

        distance = abs(close - reference)

        distance_atr = (
            distance / atr_value
            if atr_value > 0
            else None
        )

        DIAGNOSTICS["structure_confirmed"] += 1

        STRUCTURE_DEBUG.append({
            "symbol": symbol,
            "direction": direction,
            "state": "CONFIRMED",
            "structure": name,
            "reference": reference,
            "reference_type": reference_type,
            "bars_checked": j - sweep_index,
            "distance": distance,
            "distance_atr": distance_atr,
            "sweep_time": sweep["time"]
        })

        return {
            "name": name,
            "trend_context": trend,
            "time": int(candle["close_time"]),
            "index": j,
            "reference": reference,
            "pivot_index": pivot["index"],
            "bars_checked": j - sweep_index
        }

    if available_bars < MAX_STRUCTURE_BARS:
        DIAGNOSTICS["structure_waiting"] += 1

        STRUCTURE_DEBUG.append({
            "symbol": symbol,
            "direction": direction,
            "state": "WAITING_FOR_CONFIRMATION",
            "reference": reference,
            "reference_type": reference_type,
            "bars_checked": bars_checked,
            "bars_required": MAX_STRUCTURE_BARS,
            "sweep_time": sweep["time"]
        })

        return None

    DIAGNOSTICS["structure_no_break"] += 1
    DIAGNOSTICS["structure_expired"] += 1

    STRUCTURE_DEBUG.append({
        "symbol": symbol,
        "direction": direction,
        "state": "EXPIRED_NO_BREAK",
        "reference": reference,
        "reference_type": reference_type,
        "bars_checked": bars_checked,
        "sweep_time": sweep["time"]
    })

    return None


def find_sweep_structure(df15, symbol):
    candidates = detect_sweep_candidates(df15)

    if not candidates:
        return None, None, 0, False

    waiting_candidate = None

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

        if (
            waiting_candidate is None
            and len(df15) - 1 - sweep["index"] < MAX_STRUCTURE_BARS
        ):
            waiting_candidate = sweep

    if waiting_candidate is not None:
        return (
            waiting_candidate,
            None,
            len(candidates),
            False
        )

    return candidates[0], None, len(candidates), False


# =========================================================
# PULLBACK ENTRY
# =========================================================

def detect_pullback_entry(
    df5,
    direction,
    structure,
    sweep,
    atr
):
    """
    Entry only after:
    1. Confirmed BOS/CHoCH.
    2. Price returns to the broken structure level.
    3. A closed 5m candle confirms rejection.
    """

    reference = float(structure["reference"])
    structure_time = int(structure["time"])

    age_minutes = (
        int(df5.iloc[-1]["close_time"]) - structure_time
    ) / 60000

    if age_minutes < 0 or age_minutes > MAX_STRUCTURE_AGE_MINUTES:
        DIAGNOSTICS["pullback_rejected"] += 1
        return None

    after = df5[
        df5["time"] >= structure_time
    ].copy()

    if len(after) < 2:
        DIAGNOSTICS["pullback_waiting"] += 1
        return None

    after = after.tail(PULLBACK_LOOKBACK)

    last = after.iloc[-1]

    tolerance = atr * PULLBACK_TOLERANCE_ATR

    entry = float(last["close"])

    if direction == "BUY":
        touched = (
            float(last["low"]) <= reference + tolerance
            and float(last["low"]) >= reference - tolerance
        )

        rejection = (
            float(last["close"]) > float(last["open"])
            and float(last["close"]) > reference
        )

        stop_anchor = min(
            float(after["low"].min()),
            float(sweep["sweep_low"])
        )

        structural_stop = (
            stop_anchor - atr * PULLBACK_STOP_BUFFER_ATR
        )

        stop_loss = min(
            structural_stop,
            entry - atr * MIN_RISK_ATR
        )

        risk = entry - stop_loss

        distance = max(0, entry - reference)

    else:
        touched = (
            float(last["high"]) >= reference - tolerance
            and float(last["high"]) <= reference + tolerance
        )

        rejection = (
            float(last["close"]) < float(last["open"])
            and float(last["close"]) < reference
        )

        stop_anchor = max(
            float(after["high"].max()),
            float(sweep["sweep_high"])
        )

        structural_stop = (
            stop_anchor + atr * PULLBACK_STOP_BUFFER_ATR
        )

        stop_loss = max(
            structural_stop,
            entry + atr * MIN_RISK_ATR
        )

        risk = stop_loss - entry

        distance = max(0, reference - entry)

    if not touched or not rejection:
        DIAGNOSTICS["pullback_waiting"] += 1
        return None

    if distance > atr * MAX_ENTRY_DISTANCE_ATR:
        DIAGNOSTICS["entry_distance_rejected"] += 1

        REJECTION_DEBUG.append({
            "stage": "PULLBACK",
            "reason": "ENTRY_TOO_FAR_FROM_STRUCTURE",
            "distance_atr": distance / atr
        })

        return None

    if not np.isfinite(risk) or risk <= 0:
        DIAGNOSTICS["risk_rejected"] += 1
        DIAGNOSTICS["risk_rejected_invalid"] += 1
        return None

    if risk > atr * MAX_RISK_ATR:
        DIAGNOSTICS["risk_rejected"] += 1
        DIAGNOSTICS["risk_rejected_too_wide"] += 1

        REJECTION_DEBUG.append({
            "stage": "RISK",
            "reason": "SL_TOO_WIDE",
            "entry": entry,
            "stop_loss": stop_loss,
            "risk_atr": risk / atr,
            "maximum_risk_atr": MAX_RISK_ATR
        })

        return None

    DIAGNOSTICS["pullback_confirmed"] += 1

    return {
        "entry": entry,
        "stop_loss": float(stop_loss),
        "risk": float(risk),
        "risk_atr": float(risk / atr),
        "reference": reference,
        "pullback_time": int(last["close_time"])
    }


# =========================================================
# FAIR VALUE GAP
# =========================================================

def detect_fvg_retest(df, direction, after_time, atr):
    if len(df) < 30:
        return None

    last_index = len(df) - 1
    start = max(2, last_index - FVG_LOOKBACK)

    last = df.iloc[last_index]
    counted = False

    for i in range(last_index - 1, start - 1, -1):
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
            confirmed = (
                float(last["close"]) > float(last["open"])
            )
        else:
            confirmed = (
                float(last["close"]) < float(last["open"])
            )

        if touched and confirmed:
            DIAGNOSTICS["fvg_retest_detected"] += 1

            return {
                "lower": zone_low,
                "upper": zone_high,
                "time": int(third["close_time"])
            }

    return None


# =========================================================
# ORDER BLOCK
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

    df15 = calculate_indicators(df15)

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

    # Confirmation checks
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

        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "CONFIRMATIONS",
            "reason": "CONFIRMATION_SCORE_LOW",
            "score": int(confirmations),
            "required": MIN_CONFIRMATIONS,
            "direction": direction
        })

        return None

    # Pullback entry
    pullback = detect_pullback_entry(
        df5,
        direction,
        structure,
        sweep,
        atr
    )

    if pullback is None:
        return None

    entry = pullback["entry"]
    stop_loss = pullback["stop_loss"]
    risk = pullback["risk"]

    if direction == "BUY":
        tp1 = entry + risk
        tp2 = entry + risk * 2
        tp3 = entry + risk * 3
    else:
        tp1 = entry - risk
        tp2 = entry - risk * 2
        tp3 = entry - risk * 3

    fvg = detect_fvg_retest(
        df5,
        direction,
        structure["time"],
        atr
    )

    order_block = detect_order_block(
        df5,
        direction
    )

    if order_block:
        DIAGNOSTICS["order_block_detected"] += 1

    signal_time = int(row["close_time"])

    return {
        "symbol": symbol,
        "direction": direction,

        "entry": float(entry),
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

        "fvg_lower": fvg["lower"] if fvg else None,
        "fvg_upper": fvg["upper"] if fvg else None,
        "fvg_retest": bool(fvg),
        "fvg_required": False,

        "entry_model": "BOS_CHOCH_PULLBACK",

        "pullback_reference": pullback["reference"],
        "pullback_time": pullback["pullback_time"],

        "order_block": order_block,

        "structure": structure["name"],
        "structure_trend": structure.get(
            "trend_context",
            "NEUTRAL"
        ),

        "structure_time": structure["time"],
        "sweep_time": sweep["time"],

        "signal_time": signal_time,
        "last_checked_time": signal_time,

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "tp1_time": None,
        "tp2_time": None,
        "tp3_time": None,

        "result_stage": "NONE",
        "status": "OPEN",

        "result_time": None,
        "result_price": None,

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
                    "telegram_events",
                    {}
                )[event] = True

                pending.remove(event)
                changed = True

                save_state(state)

            else:
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

                reached_level = (
                    tp3 if high >= tp3
                    else tp2 if high >= tp2
                    else tp1 if high >= tp1
                    else None
                )

            else:
                stop_hit = high >= stop

                reached_level = (
                    tp3 if low <= tp3
                    else tp2 if low <= tp2
                    else tp1 if low <= tp1
                    else None
                )

            # Conservative rule: SL wins if both hit in one candle.
            if stop_hit:
                signal["status"] = "SL"
                signal["result_time"] = close_time
                signal["result_price"] = stop

                queue_event(signal, "SL")

                changed = True
                break

            if reached_level is None:
                continue

            if reached_level == tp3:
                if not signal.get("tp1_hit"):
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = close_time
                    signal["result_stage"] = "TP1"
                    queue_event(signal, "TP1")

                if not signal.get("tp2_hit"):
                    signal["tp2_hit"] = True
                    signal["tp2_time"] = close_time
                    signal["result_stage"] = "TP2"
                    queue_event(signal, "TP2")

                if not signal.get("tp3_hit"):
                    signal["tp3_hit"] = True
                    signal["tp3_time"] = close_time
                    signal["result_stage"] = "TP3"
                    queue_event(signal, "TP3")

                signal["status"] = "TP3"
                signal["result_time"] = close_time
                signal["result_price"] = tp3

                changed = True
                break

            elif reached_level == tp2:
                if not signal.get("tp1_hit"):
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = close_time
                    signal["result_stage"] = "TP1"
                    queue_event(signal, "TP1")

                if not signal.get("tp2_hit"):
                    signal["tp2_hit"] = True
                    signal["tp2_time"] = close_time
                    signal["result_stage"] = "TP2"
                    queue_event(signal, "TP2")

                changed = True

            elif reached_level == tp1:
                if not signal.get("tp1_hit"):
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = close_time
                    signal["result_stage"] = "TP1"
                    queue_event(signal, "TP1")

                changed = True

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
        and not s.get("tp2_hit", False)
        for s in today_signals
    )

    sl_after_tp2 = sum(
        s.get("status") == "SL"
        and s.get("tp2_hit", False)
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
        f"SL after TP2: {sl_after_tp2}\n"
        f"Still open: {open_count}\n\n"
        "TP1/TP2 are milestone counts, "
        "not separate completed trades.\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    return message


# =========================================================
# SCANNER
# =========================================================

def run_scanner():
    global DIAGNOSTICS
    global STRUCTURE_DEBUG
    global REJECTION_DEBUG

    DIAGNOSTICS = {key: 0 for key in DIAGNOSTICS}
    STRUCTURE_DEBUG = []
    REJECTION_DEBUG = []

    print("=" * 45)
    print(f"SMART MONEY CRYPTO SIGNAL BOT v{VERSION}")
    print("Iran time:", current_time_string())
    print("=" * 45)

    if not is_trading_time():
        print("Outside trading hours.")
        return

    state = load_state()

    update_signal_results(state)
    save_state(state)

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
                and old.get("status") in ("OPEN", "TP3", "SL")
                for old in state["signals"]
            )

            if duplicate:
                DIAGNOSTICS["duplicate_signals"] += 1
                continue

            state["signals"].append(signal)

            queue_event(signal, "ENTRY")

            new_signals += 1

            print(
                f"NEW SIGNAL: {symbol} "
                f"{signal['direction']} "
                f"{signal['confirmations']}/7 "
                f"RISK={signal['risk_atr']:.2f} ATR"
            )

            save_state(state)

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

    process_pending_events(state)

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
    print("REJECTION DEBUG DETAILS")

    for item in REJECTION_DEBUG:
        print(item)

    print("=" * 45)


# =========================================================
# DAILY REPORT RUNNER
# =========================================================

def run_report():
    state = load_state()

    update_signal_results(state)
    save_state(state)

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
        if (
            len(sys.argv) > 1
            and sys.argv[1].lower() == "report"
        ):
            run_report()
        else:
            run_scanner()

    except Exception as error:
        print("FATAL ERROR:", error)
        raise
