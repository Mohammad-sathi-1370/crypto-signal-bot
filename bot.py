import os
import sys
import json
import time
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =====================================================
# SMART MONEY CRYPTO SIGNAL BOT - VERSION 5.3 DEBUG
# =====================================================

VERSION = "5.3-DEBUG"

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
STRUCTURE_LOOKBACK = 5
MAX_STRUCTURE_BARS = 8

FVG_LOOKBACK = 25
FVG_MIN_ATR = 0.05

ORDER_BLOCK_LOOKBACK = 10

MIN_RISK_ATR = 0.75
MAX_RISK_ATR = 3.0
SWEEP_STOP_BUFFER = 0.12

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

STATE_FILE = "signals.json"

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

# =====================================================
# HTTP SESSION
# =====================================================

session = requests.Session()

retry_strategy = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.7,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)

session.headers.update({
    "User-Agent": f"CryptoSignalBot/{VERSION}"
})

# =====================================================
# DIAGNOSTICS
# =====================================================

DIAGNOSTICS = {
    "insufficient_data": 0,
    "invalid_values": 0,
    "adx_rejected": 0,
    "volatility_rejected": 0,
    "volume_rejected": 0,
    "confirmations_rejected": 0,
    "direction_rejected": 0,
    "sweep_rejected": 0,
    "sweep_detected": 0,
    "structure_rejected": 0,
    "structure_detected": 0,
    "structure_no_pivot": 0,
    "structure_no_break": 0,
    "structure_pivots_found": 0,
    "structure_bars_checked": 0,
    "fvg_rejected": 0,
    "fvg_detected": 0,
    "fvg_retest_detected": 0,
    "order_block_detected": 0,
    "risk_rejected": 0,
    "errors": 0
}

STRUCTURE_DEBUG = []

# =====================================================
# TIME
# =====================================================

def iran_now():
    return datetime.now(IRAN_TZ)


def current_time_string():
    return iran_now().strftime("%Y-%m-%d %H:%M")


def today_string():
    return iran_now().strftime("%Y-%m-%d")


def is_trading_time():
    return 8 <= iran_now().hour < 22


# =====================================================
# TELEGRAM
# =====================================================

def send_telegram_text(message):

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
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

    ob_text = "YES" if signal["order_block"] else "NO"

    message = (
        "🚨 SMART MONEY SIGNAL 🚨\n\n"
        f"Coin: {signal['symbol']}\n"
        f"Direction: {signal['direction']}\n"
        f"Confirmations: {signal['confirmations']}/7\n\n"
        "SMC CONFIRMATIONS:\n"
        "Liquidity Sweep: YES\n"
        f"Structure: {signal['structure']}\n"
        "FVG Retest: YES\n"
        f"Order Block: {ob_text}\n\n"
        f"FVG Zone: {signal['fvg_lower']:.8g}"
        f" - {signal['fvg_upper']:.8g}\n\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"Stop Loss: {signal['stop_loss']:.8g}\n"
        f"TP1: {signal['tp1']:.8g}\n"
        f"TP2: {signal['tp2']:.8g}\n\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"ADX: {signal['adx']:.2f}\n"
        f"Volume Ratio: {signal['volume_ratio']:.2f}\n\n"
        f"Bot Version: {VERSION}"
    )

    return send_telegram_text(message)


# =====================================================
# STATE STORAGE
# =====================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {
            "signals": [],
            "daily": {}
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

    except (json.JSONDecodeError, OSError) as error:
        print("CRITICAL: State file could not be read:", error)
        raise RuntimeError(
            "State file is damaged. Refusing to overwrite signal history."
        )

    if not isinstance(state, dict):
        raise RuntimeError("Invalid state file format.")

    state.setdefault("signals", [])
    state.setdefault("daily", {})

    if not isinstance(state["signals"], list):
        raise RuntimeError("Invalid signals history.")

    if not isinstance(state["daily"], dict):
        raise RuntimeError("Invalid daily history.")

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

    for key in DIAGNOSTICS:
        record["diagnostics"].setdefault(key, 0)

    return record


# =====================================================
# MARKET DATA
# =====================================================

def get_market_coins():

    response = session.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        timeout=20
    )

    response.raise_for_status()

    excluded = {
        "USDC", "FDUSD", "TUSD", "USDP",
        "DAI", "USDE", "BUSD", "EUR",
        "AEUR", "UST", "USTC"
    }

    leveraged_suffixes = (
        "UP", "DOWN", "BULL", "BEAR"
    )

    coins = []

    for item in response.json():

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        base_asset = symbol[:-4]

        if base_asset in excluded:
            continue

        if base_asset.endswith(leveraged_suffixes):
            continue

        try:
            volume = float(item["quoteVolume"])
        except (ValueError, TypeError, KeyError):
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

        last_time = int(df.iloc[-1]["close_time"])

        if len(data) < 1000:
            break

        next_cursor = last_time + 1

        if next_cursor <= cursor:
            break

        cursor = next_cursor
        time.sleep(0.1)

    if not all_candles:
        return pd.DataFrame()

    result = pd.concat(
        all_candles,
        ignore_index=True
    )

    return (
        result
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )


# =====================================================
# INDICATORS
# =====================================================

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

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

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
        100 * plus_smoothed /
        atr_adx.replace(0, np.nan)
    )

    df["minus_di"] = (
        100 * minus_smoothed /
        atr_adx.replace(0, np.nan)
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


# =====================================================
# CONFIRMED SWING POINTS
# =====================================================

def find_confirmed_swings(df, end_index):

    highs = []
    lows = []

    last_pivot = end_index - PIVOT_RIGHT

    for i in range(
        PIVOT_LEFT,
        last_pivot + 1
    ):

        left = df.iloc[i - PIVOT_LEFT:i]
        right = df.iloc[i + 1:i + PIVOT_RIGHT + 1]

        if len(right) < PIVOT_RIGHT:
            continue

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


# =====================================================
# LIQUIDITY SWEEP
# =====================================================

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
        last_index - 1,
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


# =====================================================
# MARKET STRUCTURE - DEBUG VERSION
# =====================================================

def detect_structure(df, sweep):

    sweep_index = sweep["index"]
    direction = sweep["direction"]

    highs, lows = find_confirmed_swings(
        df,
        sweep_index
    )

    if direction == "BUY":
        valid = [
            item for item in highs
            if item["index"] < sweep_index
        ]
    else:
        valid = [
            item for item in lows
            if item["index"] < sweep_index
        ]

    DIAGNOSTICS["structure_pivots_found"] += len(valid)

    if not valid:

        DIAGNOSTICS["structure_no_pivot"] += 1

        STRUCTURE_DEBUG.append({
            "direction": direction,
            "sweep_time": sweep["time"],
            "reason": "NO_CONFIRMED_PIVOT",
            "pivot_count": 0,
            "reference": None,
            "bars_checked": 0,
            "best_close": None
        })

        return None

    reference = valid[-1]

    end_index = min(
        len(df) - 1,
        sweep_index + MAX_STRUCTURE_BARS
    )

    bars_checked = 0
    best_close = None

    for j in range(
        sweep_index + 1,
        end_index + 1
    ):

        candle = df.iloc[j]
        close = float(candle["close"])

        bars_checked += 1

        if best_close is None:
            best_close = close

        elif direction == "BUY" and close > best_close:
            best_close = close

        elif direction == "SELL" and close < best_close:
            best_close = close

        DIAGNOSTICS["structure_bars_checked"] += 1

        if direction == "BUY":

            if close > reference["price"]:

                STRUCTURE_DEBUG.append({
                    "direction": direction,
                    "sweep_time": sweep["time"],
                    "reason": "STRUCTURE_CONFIRMED",
                    "pivot_count": len(valid),
                    "reference": reference["price"],
                    "bars_checked": bars_checked,
                    "best_close": best_close
                })

                return {
                    "name": "BOS/CHoCH",
                    "time": int(candle["close_time"]),
                    "index": j
                }

        else:

            if close < reference["price"]:

                STRUCTURE_DEBUG.append({
                    "direction": direction,
                    "sweep_time": sweep["time"],
                    "reason": "STRUCTURE_CONFIRMED",
                    "pivot_count": len(valid),
                    "reference": reference["price"],
                    "bars_checked": bars_checked,
                    "best_close": best_close
                })

                return {
                    "name": "BOS/CHoCH",
                    "time": int(candle["close_time"]),
                    "index": j
                }

    DIAGNOSTICS["structure_no_break"] += 1

    STRUCTURE_DEBUG.append({
        "direction": direction,
        "sweep_time": sweep["time"],
        "reason": "NO_STRUCTURE_BREAK",
        "pivot_count": len(valid),
        "reference": reference["price"],
        "bars_checked": bars_checked,
        "best_close": best_close
    })

    return None


def find_sweep_structure(df15):

    candidates = detect_sweep_candidates(df15)

    if not candidates:
        return None, None, 0, False

    for sweep in candidates:

        structure = detect_structure(
            df15,
            sweep
        )

        if structure is not None:
            return (
                sweep,
                structure,
                len(candidates),
                True
            )

    return (
        candidates[0],
        None,
        len(candidates),
        False
    )


# =====================================================
# FAIR VALUE GAP + RETEST
# =====================================================

def detect_fvg_retest(df, direction, after_time):

    if len(df) < 30:
        return None

    last_index = len(df) - 1

    start = max(
        2,
        last_index - FVG_LOOKBACK
    )

    last = df.iloc[last_index]
    counted_fvg = False

    for i in range(
        last_index - 1,
        start - 1,
        -1
    ):

        first = df.iloc[i - 2]
        third = df.iloc[i]

        if int(third["close_time"]) <= after_time:
            continue

        atr_value = float(third["atr"])

        if not np.isfinite(atr_value) or atr_value <= 0:
            continue

        if direction == "BUY":

            if float(third["low"]) <= float(first["high"]):
                continue

            zone_low = float(first["high"])
            zone_high = float(third["low"])

            gap_size = zone_high - zone_low

            if gap_size < atr_value * FVG_MIN_ATR:
                continue

            later = df.iloc[i + 1:last_index]

            if (
                not later.empty
                and (later["close"] < zone_low).any()
            ):
                continue

            if not counted_fvg:
                DIAGNOSTICS["fvg_detected"] += 1
                counted_fvg = True

            touched = (
                float(last["low"]) <= zone_high
                and float(last["high"]) >= zone_low
            )

            if (
                touched
                and float(last["close"]) > float(last["open"])
            ):

                DIAGNOSTICS["fvg_retest_detected"] += 1

                return {
                    "lower": zone_low,
                    "upper": zone_high,
                    "time": int(third["close_time"])
                }

        else:

            if float(third["high"]) >= float(first["low"]):
                continue

            zone_low = float(third["high"])
            zone_high = float(first["low"])

            gap_size = zone_high - zone_low

            if gap_size < atr_value * FVG_MIN_ATR:
                continue

            later = df.iloc[i + 1:last_index]

            if (
                not later.empty
                and (later["close"] > zone_high).any()
            ):
                continue

            if not counted_fvg:
                DIAGNOSTICS["fvg_detected"] += 1
                counted_fvg = True

            touched = (
                float(last["high"]) >= zone_low
                and float(last["low"]) <= zone_high
            )

            if (
                touched
                and float(last["close"]) < float(last["open"])
            ):

                DIAGNOSTICS["fvg_retest_detected"] += 1

                return {
                    "lower": zone_low,
                    "upper": zone_high,
                    "time": int(third["close_time"])
                }

    return None


# =====================================================
# ORDER BLOCK
# =====================================================

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


# =====================================================
# SIGNAL GENERATION
# =====================================================

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

    if atr <= 0 or float(row["volume_avg"]) <= 0:
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

    sweep, structure, sweep_count, paired = (
        find_sweep_structure(df15)
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
        structure["time"]
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

    confirmations = sum(
        bool(check) for check in checks
    )

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

    if (
        risk <= 0
        or risk > atr * MAX_RISK_ATR
    ):
        DIAGNOSTICS["risk_rejected"] += 1
        return None

    if direction == "BUY":

        tp1 = price + risk
        tp2 = price + risk * 2

    else:

        tp1 = price - risk
        tp2 = price - risk * 2

    signal_time = int(row["close_time"])

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": price,
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "confirmations": int(confirmations),
        "rsi": float(row["rsi"]),
        "adx": float(row["adx"]),
        "volume_ratio": volume_ratio,
        "atr": atr,
        "fvg_lower": fvg["lower"],
        "fvg_upper": fvg["upper"],
        "order_block": order_block,
        "structure": structure["name"],
        "sweep_time": sweep["time"],
        "signal_time": signal_time,
        "last_checked_time": signal_time,
        "tp1_hit": False,
        "status": "OPEN",
        "created_at": current_time_string(),
        "created_date": today_string()
    }


# =====================================================
# SIGNAL TRACKING
# =====================================================

def update_signal_results(state):

    changed = False

    for signal in state["signals"]:

        if signal.get("status") not in ("OPEN", None):
            continue

        symbol = signal.get("symbol")

        if not symbol:
            continue

        last_checked = signal.get(
            "last_checked_time",
            signal.get("signal_time", 0)
        )

        try:
            candles = get_tracking_candles(
                symbol,
                int(last_checked) + 1
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

        tp1_hit = bool(signal.get("tp1_hit", False))

        for _, candle in candles.iterrows():

            high = float(candle["high"])
            low = float(candle["low"])
            close_time = int(candle["close_time"])

            if direction == "BUY":

                stop_hit = low <= stop
                first_hit = high >= tp1
                second_hit = high >= tp2

            else:

                stop_hit = high >= stop
                first_hit = low <= tp1
                second_hit = low <= tp2

            if not tp1_hit:

                if stop_hit:

                    signal["status"] = "SL"
                    signal["result_time"] = close_time
                    signal["result_price"] = stop

                    changed = True
                    break

                if first_hit:

                    tp1_hit = True
                    signal["tp1_hit"] = True
                    signal["tp1_time"] = close_time

                    changed = True
                    continue

            else:

                if stop_hit:

                    signal["status"] = "TP1"
                    signal["result_time"] = close_time
                    signal["result_price"] = stop

                    changed = True
                    break

                if second_hit:

                    signal["status"] = "TP2"
                    signal["result_time"] = close_time
                    signal["result_price"] = tp2

                    changed = True
                    break

        signal["last_checked_time"] = int(
            candles.iloc[-1]["close_time"]
        )

        changed = True

    return changed


# =====================================================
# DAILY REPORT
# =====================================================

def signal_created_date(signal):

    value = signal.get("created_date")

    if value:
        return str(value)[:10]

    value = signal.get("created_at", "")

    return str(value)[:10] if value else None


def create_daily_report(state):

    today = today_string()

    today_signals = [
        s for s in state["signals"]
        if signal_created_date(s) == today
    ]

    tp1_then_sl = sum(
        s.get("status") == "TP1"
        for s in today_signals
    )

    tp2_count = sum(
        s.get("status") == "TP2"
        for s in today_signals
    )

    sl_count = sum(
        s.get("status") == "SL"
        for s in today_signals
    )

    open_count = sum(
        s.get("status", "OPEN") == "OPEN"
        for s in today_signals
    )

    tp1_reached = tp1_then_sl + tp2_count

    total_closed = (
        tp1_then_sl + tp2_count + sl_count
    )

    tp1_hit_rate = (
        tp1_reached / total_closed * 100
        if total_closed else 0
    )

    record = state.get(
        "daily", {}
    ).get(
        today,
        {
            "scans": 0,
            "signals": 0,
            "diagnostics": {}
        }
    )

    diagnostics = record.get(
        "diagnostics", {}
    )

    message = (
        "📊 DAILY PERFORMANCE REPORT\n"
        f"Date: {today}\n\n"
        f"Scans completed: {record.get('scans', 0)}\n"
        f"New signals today: {record.get('signals', 0)}\n\n"
        "TODAY'S SIGNALS STATUS:\n"
        f"TP1 reached (including TP1 then SL): {tp1_reached}\n"
        f"TP1 then SL: {tp1_then_sl}\n"
        f"TP2 reached: {tp2_count}\n"
        f"Stop Loss before TP1: {sl_count}\n"
        f"Still open: {open_count}\n\n"
        f"TP1 hit rate among closed signals: {tp1_hit_rate:.1f}%\n\n"
        "SMART MONEY DIAGNOSTICS:\n"
        f"Liquidity Sweep detected: {diagnostics.get('sweep_detected', 0)}\n"
        f"Liquidity Sweep rejected: {diagnostics.get('sweep_rejected', 0)}\n"
        f"BOS / CHoCH detected: {diagnostics.get('structure_detected', 0)}\n"
        f"Structure rejected: {diagnostics.get('structure_rejected', 0)}\n"
        f"Structure no pivot: {diagnostics.get('structure_no_pivot', 0)}\n"
        f"Structure no break: {diagnostics.get('structure_no_break', 0)}\n"
        f"Structure pivots found: {diagnostics.get('structure_pivots_found', 0)}\n"
        f"Structure bars checked: {diagnostics.get('structure_bars_checked', 0)}\n"
        f"FVG detected: {diagnostics.get('fvg_detected', 0)}\n"
        f"FVG Retest confirmed: {diagnostics.get('fvg_retest_detected', 0)}\n"
        f"FVG rejected: {diagnostics.get('fvg_rejected', 0)}\n"
        f"Order Block detected: {diagnostics.get('order_block_detected', 0)}\n\n"
        "OTHER FILTERS:\n"
    )

    for key in [
        "insufficient_data",
        "invalid_values",
        "adx_rejected",
        "volatility_rejected",
        "volume_rejected",
        "confirmations_rejected",
        "direction_rejected",
        "risk_rejected",
        "errors"
    ]:

        message += (
            f"{key}: {diagnostics.get(key, 0)}\n"
        )

    return message + (
        f"\nReport time: {current_time_string()}"
    )


# =====================================================
# MAIN SCANNER
# =====================================================

def run_scanner():

    if not is_trading_time():

        print(
            "Outside trading hours: 08:00-22:00 Iran."
        )

        return

    state = load_state()
    record = get_daily_record(state)

    print("=" * 45)

    print(
        f"SMART MONEY CRYPTO SIGNAL BOT v{VERSION}"
    )

    print(
        "Iran time:",
        current_time_string()
    )

    print("=" * 45)

    for key in DIAGNOSTICS:
        DIAGNOSTICS[key] = 0

    STRUCTURE_DEBUG.clear()

    update_signal_results(state)

    try:

        coins = get_market_coins()

    except Exception as error:

        print("Market coins ERROR:", error)

        DIAGNOSTICS["errors"] += 1

        for key, value in DIAGNOSTICS.items():

            record["diagnostics"][key] = (
                record["diagnostics"].get(key, 0) + value
            )

        save_state(state)
        return

    print(f"Scanning {len(coins)} coins...")

    found = 0

    for symbol in coins:

        try:

            df5 = get_candles(
                symbol,
                TIMEFRAME
            )

            df15 = get_candles(
                symbol,
                TREND_TIMEFRAME
            )

            if df5.empty or df15.empty:

                DIAGNOSTICS["insufficient_data"] += 1
                continue

            signal = generate_signal(
                symbol,
                df5,
                df15
            )

            if signal is None:

                time.sleep(0.15)
                continue

            duplicate = any(
                item.get("symbol") == symbol
                and item.get("signal_time") == signal["signal_time"]
                for item in state["signals"]
            )

            if duplicate:
                continue

            state["signals"].append(signal)

            found += 1
            record["signals"] += 1

            print(
                f"{symbol} {signal['direction']} "
                f"{signal['confirmations']}/7"
            )

            send_signal(signal)

            time.sleep(0.2)

        except Exception as error:

            DIAGNOSTICS["errors"] += 1

            print(
                f"ERROR {symbol}: {error}"
            )

    record["scans"] += 1

    for key, value in DIAGNOSTICS.items():

        record["diagnostics"][key] = (
            record["diagnostics"].get(key, 0) + value
        )

    save_state(state)

    print("=" * 45)
    print("SCAN FINISHED")
    print("Coins scanned:", len(coins))
    print("Signals found:", found)
    print("Diagnostics:", DIAGNOSTICS)

    print("=" * 45)
    print("STRUCTURE DEBUG DETAILS")

    if not STRUCTURE_DEBUG:
        print("No structure debug records generated.")

    for item in STRUCTURE_DEBUG:

        print(
            f"Direction: {item['direction']} | "
            f"Reason: {item['reason']} | "
            f"Pivot count: {item['pivot_count']} | "
            f"Reference: {item['reference']} | "
            f"Bars checked: {item['bars_checked']} | "
            f"Best close: {item['best_close']}"
        )

    print("=" * 45)


# =====================================================
# REPORT MODE
# =====================================================

def run_report():

    state = load_state()

    update_signal_results(state)

    message = create_daily_report(state)

    print(message)

    send_telegram_text(message)

    save_state(state)


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":

    try:

        if (
            len(sys.argv) > 1
            and sys.argv[1] == "report"
        ):

            run_report()

        else:

            run_scanner()

    except Exception as error:

        print("FATAL ERROR:", error)
        raise
