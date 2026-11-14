import os
import json
import time
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================================================
# SMART MONEY CRYPTO SIGNAL BOT - VERSION 6.6.11
# =========================================================

VERSION = "6.6.12"
MAX_RISK_ATR = 4.0
MAX_SIGNAL_TRACKING_AGE_MS = 48 * 60 * 60 * 1000  # Stop monitoring each signal after 48 hours.

MAX_BARS_SINCE_STRUCTURE = 4  # Keep a confirmed structure eligible for up to 60 minutes.
MAX_ENTRY_EXTENSION_ATR = 0.80

RETEST_MAX_BARS_AFTER_STRUCTURE = 12  # Twelve closed 5m candles = 60 minutes, aligned with the 15m structure-age limit.
RETEST_TOLERANCE_ATR = 0.20
RETEST_MAX_PENETRATION_ATR = 0.40
RETEST_EVENT_MAX_AGE_BARS = 4
RETEST_CURRENT_PRICE_MAX_DISTANCE_ATR = 0.55

# Independent early-reversal model; it supplements rather than weakens SMC.

# Targets use the larger of the R-multiple distance and the ATR distance.
# This avoids artificially tiny targets when the structural stop is tight.
TP_ATR_MULTIPLIERS = (1.0, 2.0, 3.0)
TARGET_STRUCTURE_BUFFER_ATR = 0.15
TRAILING_STOP_ATR_MULTIPLIER = 1.5
TARGET_PIVOT_LEFT = 2
TARGET_PIVOT_RIGHT = 2
TARGET_LOOKBACK_5M = 72
TARGET_LOOKBACK_15M = 32
EARLY_REVERSAL_LOOKBACK = 12
EARLY_REVERSAL_MAX_AGE_BARS = 2
EARLY_REVERSAL_MIN_WICK_RATIO = 0.35
EARLY_REVERSAL_MIN_SCORE = 6
EARLY_REVERSAL_MIN_VOLUME_RATIO = 1.00
EARLY_REVERSAL_MAX_ENTRY_DISTANCE_ATR = 0.35
EARLY_REVERSAL_MIN_RISK_ATR = 0.15

# Dedicated swing-turn model: prioritize early rejection at local 1h extremes.
SWING_REVERSAL_LOOKBACK_5M = 24
SWING_REVERSAL_LOOKBACK_1H = 12
SWING_REVERSAL_MAX_EVENT_AGE = 1
SWING_REVERSAL_MIN_WICK_RATIO = 0.30
SWING_REVERSAL_MIN_SCORE = 4
SWING_REVERSAL_MIN_VOLUME_RATIO = 0.80
SWING_REVERSAL_MAX_ENTRY_DISTANCE_ATR = 0.45
SWING_REVERSAL_STOP_BUFFER_ATR = 0.22

BASE_URL = "https://data-api.binance.vision"

TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"

TOP_COINS = 50
MIN_CONFIRMATIONS = 6

VOLUME_MULTIPLIER = 0.90
ADX_MINIMUM = 14
ADX_CONFIRMATION = 18
MIN_ATR_RATIO = 0.0004

SWEEP_LOOKBACK = 8
LIQUIDITY_LOOKBACK = 12
MAX_STRUCTURE_BARS = 4
WAITING_SETUP_MAX_AGE_MS = (MAX_STRUCTURE_BARS + 1) * 15 * 60 * 1000

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_PIVOT_AGE = 32

FVG_LOOKBACK = 25
FVG_MIN_ATR = 0.05

ORDER_BLOCK_LOOKBACK = 24
ORDER_BLOCK_MAX_OPPOSING_CANDLES = 5
ORDER_BLOCK_DISPLACEMENT_ATR = 0.65
ORDER_BLOCK_MIN_RANGE_ATR = 0.90
ORDER_BLOCK_CLOSE_EXTREME = 0.30

FALLBACK_STOP_ATR = 0.75
SWEEP_STOP_BUFFER = 0.18

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
    "structure_stale_confirmed": 0,
    "early_reversal_candidates": 0,
    "early_reversal_confirmed": 0,
    "early_reversal_rejected": 0,
    "swing_reversal_candidates": 0,
    "swing_reversal_confirmed": 0,
    "swing_reversal_rejected": 0,
    "momentum_candidates": 0,
    "momentum_confirmed": 0,
    "momentum_rejected": 0,
    "momentum_reject_breakout": 0,
    "momentum_reject_trend": 0,
    "momentum_reject_volume_adx": 0,
    "momentum_reject_extension": 0,
    "momentum_reject_risk": 0,
    "late_entry_rejected": 0,
    "entry_extension_rejected": 0,
    "retest_waiting": 0,
    "retest_confirmed": 0,
    "retest_rejected": 0,
    "retest_expired": 0,
    "pending_retest_active": 0,
    "pending_retest_added": 0,
    "pending_retest_replaced": 0,
    "pending_retest_expired": 0,
    "pending_retest_stale": 0,
    "waiting_queue_active": 0,
    "waiting_queue_expired": 0,
    "waiting_queue_replaced": 0,
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
    "target_distance_rejected": 0,
    "target_structure_rejected": 0,
    "duplicate_signals": 0,
    "open_trade_locked": 0,
    "news_warning_active": 0,
    "news_calendar_errors": 0,
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
    return 8 <= iran_now().hour < 24


def timestamp_string(timestamp):
    return datetime.fromtimestamp(
        timestamp / 1000,
        tz=IRAN_TZ
    ).strftime("%Y-%m-%d %H:%M")


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_message(message, reply_to_message_id=None):
    """Send a Telegram message and return the Telegram message object."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials missing.")
        return None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }

    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)

    try:
        response = session.post(url, data=payload, timeout=20)
        response.raise_for_status()
        result = response.json()

        if not result.get("ok") or not isinstance(result.get("result"), dict):
            print("Telegram rejected message:", result)
            return None

        return result["result"]

    except Exception as error:
        print("Telegram ERROR:", error)
        return None


def send_telegram_text(message):
    return send_telegram_message(message) is not None

def send_signal(signal):
    direction = signal["direction"]
    label = "🟢 LONG" if direction == "BUY" else "🔴 SHORT"

    risk_atr = float(signal.get("risk_atr", 0))
    high_risk = risk_atr > MAX_RISK_ATR

    ob_zone = signal.get("order_block_zone") or {}

    ob_zone_text = (
        f"Order Block Zone: {float(ob_zone['lower']):.8g} - "
        f"{float(ob_zone['upper']):.8g}\n"
        if ob_zone else ""
    )

    risk_warning = ""

    if high_risk:
        risk_warning = (
            "\n\n🚨 HIGH RISK WARNING\n"
            f"Risk is above {MAX_RISK_ATR:.1f} ATR!\n"
            "Consider reducing position size or skipping entry.\n"
            "This signal is NOT blocked by the bot."
        )

    message = (
        "━━━━━━━━━━━━━━━━━━\n"
        f"🚨 SMART MONEY SIGNAL {VERSION}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 Symbol: {signal['symbol']}\n"
        f"📍 Direction: {label}\n"
        "⏱ Timeframe: 5m\n"
        f"⭐ Confirmations: {signal['confirmations']}/{signal.get('confirmation_total', 7)}\n\n"
        f"📊 {signal.get('analysis_label', 'SMC ANALYSIS')}\n"
        f"Liquidity Sweep: {'YES' if signal.get('liquidity_sweep', True) else 'NO'}\n"
        f"Structure: {signal['structure']}\n"
        f"Entry Model: {signal.get('entry_model', 'FRESH_STRUCTURE_DIRECT_CLOSED_5M')}\n"
        f"Structure Age: {signal.get('bars_since_structure', 0)} {'closed 5m bars' if signal.get('entry_model', '').startswith('EARLY_REVERSAL') else 'closed 15m bars'}\n"
        f"Entry Extension: {signal.get('entry_extension_atr', 0.0):.2f} ATR\n"
        f"FVG Context: {'YES' if signal.get('fvg_retest') else 'NO'}\n"
        f"Order Block: {'YES' if signal['order_block'] else 'NO'}\n"
        f"{ob_zone_text}\n"
        "💰 TRADE LEVELS\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"Stop Loss: {signal['stop_loss']:.8g}\n"
        f"TP1: {signal['tp1']:.8g} ({signal.get('tp1_r', 1.0):.2f}R)\n"
        f"TP2: {signal['tp2']:.8g} ({signal.get('tp2_r', 2.0):.2f}R)\n"
        f"TP3: {signal['tp3']:.8g} ({signal.get('tp3_r', 3.0):.2f}R)\n"
        "📈 INDICATORS\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"ADX: {signal['adx']:.2f}\n"
        f"Volume Ratio: {signal['volume_ratio']:.2f}\n"
        f"Risk: {risk_atr:.2f} ATR\n"
        f"Targets: {signal.get('tp1_atr', 1.0):.2f}/{signal.get('tp2_atr', 2.0):.2f}/{signal.get('tp3_atr', 3.0):.2f} ATR"
        f"{risk_warning}"
        + (
            f"\n\n⚠️ HIGH-IMPACT NEWS WINDOW\n"
            f"{signal['news_warning']}\n"
            "Please assess volatility before entry.\n"
            if signal.get("news_warning") else ""
        )
        + f"\n🕒 {signal['created_at']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    result = send_telegram_message(message)
    if not result:
        return False

    signal["telegram_message_id"] = result.get("message_id")
    chat = result.get("chat") or {}
    signal["telegram_chat_id"] = chat.get("id") or os.getenv("TELEGRAM_CHAT_ID")
    return signal.get("telegram_message_id") is not None


def send_event_message(signal, event):
    """Reply to the original signal message for each TP/SL milestone."""
    symbol = signal["symbol"]
    messages = {
        "TP1": (
            f"✅ TP1 HIT — {symbol}\n"
            f"Target: {float(signal['tp1']):.8g}\n"
            f"Milestone: {float(signal.get('tp1_r', 1.0)):.2f}R"
        ),
        "TP2": (
            f"🔥 TP2 HIT — {symbol}\n"
            f"Target: {float(signal['tp2']):.8g}\n"
            f"Milestone: {float(signal.get('tp2_r', 2.0)):.2f}R"
        ),
        "TP3": (
            f"🚀 TP3 HIT — {symbol}\n"
            f"Target: {float(signal['tp3']):.8g}\n"
            f"Milestone: {float(signal.get('tp3_r', 3.0)):.2f}R\n"
            "Trailing stop activated — trend extension is being monitored"
        ),
        "TRAILING_STOP": (
            f"🔒 TRAILING STOP HIT — {symbol}\n"
            f"Exit level: {float(signal.get('trailing_stop', signal.get('active_stop', signal['stop_loss']))):.8g}\n"
            "Profit protection closed the remaining position"
        ),
        "SL": (
            f"🛑 STOP LOSS HIT — {symbol}\n"
            f"Stop: {float(signal['stop_loss']):.8g}"
        )
    }

    if event not in messages:
        return False

    message_id = signal.get("telegram_message_id")
    if not message_id:
        print(
            f"Cannot reply to legacy signal {symbol}: "
            "original Telegram message_id is not stored."
        )
        return False

    result = send_telegram_message(
        messages[event],
        reply_to_message_id=message_id
    )
    return result is not None


# =========================================================
# STATE STORAGE
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "signals": [],
            "news_filter": {},
            "pending_setups": {},
            "pending_retests": {}
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
    state.setdefault("news_filter", {})
    state.setdefault("pending_setups", {})
    state.setdefault("pending_retests", {})

    if not isinstance(state["signals"], list):
        raise RuntimeError("Invalid signals state.")

    if not isinstance(state["news_filter"], dict):
        state["news_filter"] = {}
    if not isinstance(state["pending_setups"], dict):
        state["pending_setups"] = {}
    if not isinstance(state["pending_retests"], dict):
        state["pending_retests"] = {}

    # Remove legacy performance/statistics containers.
    state.pop("daily", None)
    state.pop("weekly_reports", None)

    for signal in state["signals"]:
        if not isinstance(signal, dict):
            continue

        signal.setdefault("tp1_hit", False)
        signal.setdefault("tp2_hit", False)
        signal.setdefault("tp3_hit", False)
        signal.setdefault("trailing_active", False)
        signal.setdefault("telegram_events", {})
        signal.setdefault("pending_events", [])

        # Old state files did not retain Telegram's original message id.
        # Such legacy signals remain trackable, but cannot receive reply messages.
        if not isinstance(signal["telegram_events"], dict):
            signal["telegram_events"] = {}
        if not isinstance(signal["pending_events"], list):
            signal["pending_events"] = []

        # Normalize older state labels without retaining statistical fields.
        if signal.get("status") == "SL_AFTER_TP1":
            signal["status"] = "SL"
        if signal.get("status") == "TP2":
            signal["status"] = "TP2"
            signal["tp1_hit"] = True
            signal["tp2_hit"] = True

        for key in (
            "stat_result", "result_locked", "result_stage",
            "result_time", "result_price", "sl_after_tp1",
            "tp1_time", "tp2_time", "tp3_time", "result_notice_sent"
        ):
            signal.pop(key, None)

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


# =========================================================
# MARKET DATA
# =========================================================

def get_market_coins():
    """
    Top 50 liquid Binance USDT markets intersected
    with active Nobitex assets.
    """

    nobitex_response = session.get(
        NOBITEX_STATS_URL,
        timeout=15
    )

    nobitex_response.raise_for_status()

    nobitex_payload = nobitex_response.json()
    stats = nobitex_payload.get("stats", {})

    if not isinstance(stats, dict) or not stats:
        raise RuntimeError(
            "Nobitex market list is empty or has an unexpected format"
        )

    nobitex_assets = set()

    for market, details in stats.items():
        if not isinstance(details, dict):
            continue

        if details.get("isClosed") is True:
            continue

        normalized = str(market).lower().replace("_", "-")
        parts = normalized.split("-")

        if (
            len(parts) == 2
            and parts[1] in ("rls", "usdt")
        ):
            nobitex_assets.add(parts[0].upper())

    if not nobitex_assets:
        raise RuntimeError(
            "Could not identify active Nobitex spot assets"
        )

    response = session.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        timeout=20
    )

    response.raise_for_status()

    excluded = {
        "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE",
        "BUSD", "USDS", "UST", "USTC", "EUR", "TRY",
        "BRL", "BIDR", "IDRT", "UAH", "RUB", "AUD", "GBP"
    }

    coins = []

    for item in response.json():
        symbol = str(item.get("symbol", ""))

        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]

        if base not in nobitex_assets:
            continue

        if base in excluded:
            continue

        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue

        try:
            quote_volume = float(item.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue

        if quote_volume > 0:
            coins.append((symbol, quote_volume))

    coins.sort(
        key=lambda item: item[1],
        reverse=True
    )

    return [
        symbol for symbol, _ in coins[:TOP_COINS]
    ]


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
# HIGH-IMPACT ECONOMIC NEWS FILTER
# =========================================================

NEWS_API_URL = "https://showmytrades.com/api/public/calendar/v1"
NOBITEX_STATS_URL = "https://apiv2.nobitex.ir/market/stats"

NEWS_CURRENCIES = (
    "USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,CNY"
)

NEWS_IMPACT = "high"
NEWS_DAYS_AHEAD = 7

NEWS_REFRESH_SECONDS = 15 * 60
NEWS_CACHE_MAX_AGE = 30 * 60

NEWS_PAUSE_BEFORE_MINUTES = 60
NEWS_PAUSE_AFTER_MINUTES = 90

NEWS_CACHE = []
NEWS_CACHE_MONOTONIC = 0.0
NEWS_SERVER_TIME = 0
NEWS_SERVER_MONOTONIC = 0.0
NEWS_LAST_ERROR = None


def refresh_news_calendar(force=False):
    """
    Refresh public high-impact economic calendar.
    This is warning-only and never blocks signals.
    """

    global NEWS_CACHE
    global NEWS_CACHE_MONOTONIC
    global NEWS_SERVER_TIME
    global NEWS_SERVER_MONOTONIC
    global NEWS_LAST_ERROR

    now_mono = time.monotonic()

    if (
        not force
        and NEWS_SERVER_TIME > 0
        and now_mono - NEWS_CACHE_MONOTONIC
        < NEWS_REFRESH_SECONDS
    ):
        return True

    try:
        response = session.get(
            NEWS_API_URL,
            params={
                "currency": NEWS_CURRENCIES,
                "impact": NEWS_IMPACT,
                "days": NEWS_DAYS_AHEAD,
                "format": "json"
            },
            timeout=15
        )

        response.raise_for_status()

        payload = response.json()

        events = (
            payload.get("events")
            if isinstance(payload, dict)
            else None
        )

        server_time = (
            payload.get("server_time")
            if isinstance(payload, dict)
            else None
        )

        if not isinstance(events, list) or not server_time:
            raise ValueError(
                "Unexpected economic calendar response"
            )

        clean_events = []

        for event in events:
            if not isinstance(event, dict):
                continue

            try:
                ts = int(event.get("ts", 0))
            except (TypeError, ValueError):
                continue

            if ts <= 0:
                continue

            if str(event.get("impact", "")).lower() != "high":
                continue

            clean_events.append(event)

        NEWS_CACHE = clean_events
        NEWS_CACHE_MONOTONIC = now_mono
        NEWS_SERVER_TIME = int(server_time)
        NEWS_SERVER_MONOTONIC = now_mono
        NEWS_LAST_ERROR = None

        return True

    except Exception as error:
        NEWS_LAST_ERROR = str(error)

        if (
            NEWS_CACHE
            and now_mono - NEWS_CACHE_MONOTONIC
            <= NEWS_CACHE_MAX_AGE
        ):
            return True

        return False


def news_clock_now():
    """
    Use calendar provider UTC clock,
    advanced by monotonic elapsed time.
    """

    if NEWS_SERVER_TIME and NEWS_SERVER_MONOTONIC:
        return (
            NEWS_SERVER_TIME
            + int(time.monotonic() - NEWS_SERVER_MONOTONIC)
        )

    return int(time.time())


def active_high_impact_news(force_refresh=False):
    """
    Return:
    (news_active, matching_event, calendar_ok)
    """

    calendar_ok = refresh_news_calendar(
        force=force_refresh
    )

    if not calendar_ok:
        return False, None, False

    now_utc = news_clock_now()

    before = NEWS_PAUSE_BEFORE_MINUTES * 60
    after = NEWS_PAUSE_AFTER_MINUTES * 60

    active = []

    for event in NEWS_CACHE:
        try:
            event_ts = int(event.get("ts", 0))
        except (TypeError, ValueError):
            continue

        if event_ts - before <= now_utc <= event_ts + after:
            active.append(event)

    if not active:
        return False, None, True

    event = min(
        active,
        key=lambda item: abs(
            int(item.get("ts", 0)) - now_utc
        )
    )

    return True, event, True


def format_news_event(event):
    if not event:
        return "High-impact economic calendar unavailable"

    title = event.get("title", "High-impact event")
    currency = event.get("currency", "")
    event_time = event.get("time", "")

    return (
        f"{currency} | {title} | UTC: {event_time}"
    )


def signal_created_timestamp_ms(signal):
    """Return a signal creation timestamp in milliseconds when available."""
    for key in ("signal_time", "created_at_ms", "sweep_time"):
        value = signal.get(key)
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            # Be tolerant of legacy state saved in Unix seconds.
            return timestamp * 1000 if timestamp < 10**12 else timestamp

    created_at = signal.get("created_at")
    if created_at:
        try:
            parsed = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M")
            return int(parsed.replace(tzinfo=IRAN_TZ).timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            pass

    return None


def prune_expired_signals(state, now_ms=None):
    """Drop signals older than 48 hours so they no longer lock or trigger tracking."""
    now_ms = int(now_ms or time.time() * 1000)
    active = []
    expired = 0

    for signal in state.get("signals", []):
        if not isinstance(signal, dict):
            continue

        created_ms = signal_created_timestamp_ms(signal)
        if created_ms is not None and now_ms - created_ms >= MAX_SIGNAL_TRACKING_AGE_MS:
            expired += 1
            continue

        active.append(signal)

    # Always write back the cleaned list, even when no timestamped signal expired.
    state["signals"] = active
    if expired:
        print(f"Expired signal tracking removed after 48 hours: {expired}")

    return expired


def has_open_trade_for_symbol(state, symbol):
    """Keep the symbol locked until the initial stop or trailing stop exits."""

    return any(
        old.get("symbol") == symbol
        and old.get("status") == "OPEN"
        for old in state.get("signals", [])
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

    df["bb_upper"] = (
        df["bb_mid"] + 2 * std
    )

    df["bb_lower"] = (
        df["bb_mid"] - 2 * std
    )

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
        100 * plus_smoothed
        / atr_adx.replace(0, np.nan)
    )

    df["minus_di"] = (
        100 * minus_smoothed
        / atr_adx.replace(0, np.nan)
    )

    dx = (
        100 * (df["plus_di"] - df["minus_di"]).abs()
        / (
            df["plus_di"] + df["minus_di"]
        ).replace(0, np.nan)
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
        left = df.iloc[
            i - PIVOT_LEFT:i
        ]

        right = df.iloc[
            i + 1:i + PIVOT_RIGHT + 1
        ]

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

def append_structure_debug(item):
    """Keep one debug/state event per symbol, direction, state and sweep candle."""
    key = (
        item.get("symbol"),
        item.get("direction"),
        item.get("state"),
        int(item.get("sweep_time", 0) or 0)
    )
    for existing in STRUCTURE_DEBUG:
        existing_key = (
            existing.get("symbol"),
            existing.get("direction"),
            existing.get("state"),
            int(existing.get("sweep_time", 0) or 0)
        )
        if existing_key == key:
            # Preserve any extra metadata supplied by a repeated evaluation.
            for name, value in item.items():
                if value is not None:
                    existing[name] = value
            return
    STRUCTURE_DEBUG.append(item)


def detect_structure(df, sweep, symbol="UNKNOWN"):
    sweep_index = int(sweep["index"])
    direction = sweep["direction"]

    highs, lows = find_confirmed_swings(
        df,
        sweep_index - 1
    )

    pivot_pool = highs if direction == "BUY" else lows

    reference_type = (
        "SWING_HIGH"
        if direction == "BUY"
        else "SWING_LOW"
    )

    valid = []

    for candidate in pivot_pool:
        pivot_index = int(candidate["index"])
        age = sweep_index - pivot_index

        if age <= 0 or age > MAX_PIVOT_AGE:
            continue

        reference = float(candidate["price"])

        between = df.iloc[
            pivot_index + 1:sweep_index
        ]

        if between.empty:
            continue

        already_broken = (
            (between["close"] > reference).any()
            if direction == "BUY"
            else (between["close"] < reference).any()
        )

        if not already_broken:
            valid.append(candidate)

    if not valid:
        DIAGNOSTICS["structure_no_pivot"] += 1

        append_structure_debug({
            "symbol": symbol,
            "direction": direction,
            "state": "NO_FRESH_UNBROKEN_PIVOT",
            "max_pivot_age": MAX_PIVOT_AGE,
            "sweep_time": sweep["time"]
        })

        return None

    pivot = max(
        valid,
        key=lambda item: int(item["index"])
    )

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
            df.iloc[sweep_index].get("atr", 0) or 0
        )

        distance = abs(close - reference)

        distance_atr = (
            distance / atr_value
            if atr_value > 0
            else None
        )

        DIAGNOSTICS["structure_confirmed"] += 1

        append_structure_debug({
            "symbol": symbol,
            "direction": direction,
            "state": "CONFIRMED",
            "structure": name,
            "reference": reference,
            "reference_type": reference_type,
            "pivot_age": sweep_index - int(pivot["index"]),
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
            "pivot_index": int(pivot["index"]),
            "bars_checked": j - sweep_index
        }

    if available_bars < MAX_STRUCTURE_BARS:
        DIAGNOSTICS["structure_waiting"] += 1

        append_structure_debug({
            "symbol": symbol,
            "direction": direction,
            "state": "WAITING_FOR_CONFIRMATION",
            "reference": reference,
            "reference_type": reference_type,
            "pivot_age": sweep_index - int(pivot["index"]),
            "bars_checked": bars_checked,
            "bars_required": MAX_STRUCTURE_BARS,
            "sweep_time": sweep["time"],
            "sweep_low": float(sweep["sweep_low"]),
            "sweep_high": float(sweep["sweep_high"]),
            "liquidity_level": float(sweep.get("liquidity_level", reference))
        })

        return None

    DIAGNOSTICS["structure_no_break"] += 1
    DIAGNOSTICS["structure_expired"] += 1

    append_structure_debug({
        "symbol": symbol,
        "direction": direction,
        "state": "EXPIRED_NO_BREAK",
        "reference": reference,
        "reference_type": reference_type,
        "bars_checked": bars_checked,
        "sweep_time": sweep["time"]
    })

    return None



def resume_pending_structure(df15, symbol, state):
    """Resume a persisted sweep candidate instead of treating the queue as display-only."""
    setup = state.setdefault("pending_setups", {}).get(symbol)
    if not isinstance(setup, dict):
        return None, None

    try:
        sweep_time = int(setup["sweep_time"])
        direction = str(setup["direction"])
        reference = float(setup["reference"])
        sweep_low = float(setup["sweep_low"])
        sweep_high = float(setup["sweep_high"])
        liquidity_level = float(setup.get("liquidity_level", reference))
    except (KeyError, TypeError, ValueError):
        state["pending_setups"].pop(symbol, None)
        return None, None

    times = pd.to_numeric(df15["time"], errors="coerce")
    matches = np.flatnonzero(times.to_numpy() == sweep_time)
    if len(matches) == 0:
        expires_at = int(setup.get("expires_at", 0) or 0)
        if not expires_at or int(time.time() * 1000) >= expires_at:
            state["pending_setups"].pop(symbol, None)
        return None, None

    sweep_index = int(matches[-1])
    sweep = {
        "direction": direction,
        "index": sweep_index,
        "sweep_low": sweep_low,
        "sweep_high": sweep_high,
        "liquidity_level": liquidity_level,
        "time": sweep_time
    }
    structure = detect_structure(df15, sweep, symbol)
    return sweep, structure

def find_sweep_structure(df15, symbol):
    """Prefer the newest actionable structure; never let an old BOS/CHoCH
    mask a newer sweep that is still waiting for confirmation.
    """
    candidates = detect_sweep_candidates(df15)

    if not candidates:
        return None, None, 0, False

    waiting_candidate = None

    # detect_sweep_candidates returns newest sweeps first. Only return a
    # confirmed structure while it is still inside the normal freshness
    # window. A stale confirmation is skipped so the loop can inspect newer
    # waiting candidates (or other fresh confirmations) instead.
    for sweep in candidates:
        structure = detect_structure(
            df15,
            sweep,
            symbol
        )

        if structure is not None:
            bars_since_structure = int(
                (pd.to_numeric(df15["time"], errors="coerce")
                 > int(structure["time"])).sum()
            )

            if bars_since_structure <= MAX_BARS_SINCE_STRUCTURE:
                return (
                    sweep,
                    structure,
                    len(candidates),
                    True
                )

            DIAGNOSTICS["structure_stale_confirmed"] += 1
            REJECTION_DEBUG.append({
                "symbol": symbol,
                "stage": "STRUCTURE_SELECTION",
                "reason": "STALE_CONFIRMED_STRUCTURE_SKIPPED",
                "direction": sweep.get("direction"),
                "bars_since_structure": bars_since_structure,
                "max_bars": MAX_BARS_SINCE_STRUCTURE,
                "sweep_time": int(sweep.get("time", 0) or 0)
            })
            continue

        if (
            waiting_candidate is None
            and len(df15) - 1 - sweep["index"]
            < MAX_STRUCTURE_BARS
        ):
            waiting_candidate = sweep

    if waiting_candidate is not None:
        return (
            waiting_candidate,
            None,
            len(candidates),
            False
        )

    return (
        candidates[0],
        None,
        len(candidates),
        False
    )


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

        later = df.iloc[
            i + 1:last_index
        ]

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

def detect_order_block(df, direction, structure_time, structure_reference):
    """
    Find the last opposing candle before a qualifying displacement.

    The displacement must close through the confirmed 15m structure level.
    The candidate zone is rejected if price closes through its far edge.
    Returns zone metadata or None.
    """

    if len(df) < ORDER_BLOCK_LOOKBACK + 5:
        return None

    work = df.copy()

    if "atr" not in work.columns:
        work = calculate_indicators(work)

    eligible = work[
        work["close_time"].astype("int64") <= int(structure_time)
    ]

    if len(eligible) < 6:
        return None

    end_index = int(eligible.index[-1])
    start_index = max(
        1,
        end_index - ORDER_BLOCK_LOOKBACK
    )

    ref = float(structure_reference)

    for displacement_index in range(
        end_index,
        start_index,
        -1
    ):
        displacement = work.iloc[displacement_index]

        op = float(displacement["open"])
        cl = float(displacement["close"])
        hi = float(displacement["high"])
        lo = float(displacement["low"])

        atr_value = float(
            displacement.get("atr", 0) or 0
        )

        if not np.isfinite(atr_value) or atr_value <= 0:
            continue

        body = abs(cl - op)
        candle_range = hi - lo

        if candle_range <= 0:
            continue

        if direction == "BUY":
            directional = cl > op and cl > ref

            close_near_extreme = (
                (hi - cl) / candle_range
                <= ORDER_BLOCK_CLOSE_EXTREME
            )

        else:
            directional = cl < op and cl < ref

            close_near_extreme = (
                (cl - lo) / candle_range
                <= ORDER_BLOCK_CLOSE_EXTREME
            )

        if not directional:
            continue

        if body < atr_value * ORDER_BLOCK_DISPLACEMENT_ATR:
            continue

        if candle_range < atr_value * ORDER_BLOCK_MIN_RANGE_ATR:
            continue

        if not close_near_extreme:
            continue

        ob_index = None

        lower_search = max(
            start_index,
            displacement_index - ORDER_BLOCK_MAX_OPPOSING_CANDLES
        )

        for candidate_index in range(
            displacement_index - 1,
            lower_search - 1,
            -1
        ):
            candidate = work.iloc[candidate_index]

            cop = float(candidate["open"])
            ccl = float(candidate["close"])

            if (
                direction == "BUY"
                and ccl < cop
            ) or (
                direction == "SELL"
                and ccl > cop
            ):
                ob_index = candidate_index
                break

        if ob_index is None:
            continue

        ob = work.iloc[ob_index]

        zone_low = float(ob["low"])
        zone_high = float(ob["high"])

        if (
            not np.isfinite([zone_low, zone_high]).all()
            or zone_high <= zone_low
        ):
            continue

        mitigation = work.iloc[
            displacement_index + 1:
        ]

        if not mitigation.empty:
            invalidated = (
                (mitigation["close"] < zone_low).any()
                if direction == "BUY"
                else (mitigation["close"] > zone_high).any()
            )

            if invalidated:
                continue

        return {
            "lower": zone_low,
            "upper": zone_high,
            "candle_time": int(ob["time"]),
            "displacement_time": int(displacement["time"]),
            "displacement_atr": round(
                body / atr_value,
                3
            )
        }

    return None


# =========================================================
# SIGNAL GENERATION
# =========================================================


def register_pending_retest(state, symbol, sweep, structure, df15):
    """Persist a confirmed BOS/CHoCH until its fresh 5m retest is actionable."""
    pending = state.setdefault("pending_retests", {})
    structure_time = int(structure["time"])
    sweep_time = int(sweep["time"])

    age_bars = int(
        (pd.to_numeric(df15["time"], errors="coerce") > structure_time).sum()
    )

    if age_bars > MAX_BARS_SINCE_STRUCTURE:
        DIAGNOSTICS["late_entry_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "PENDING_RETEST",
            "reason": "STRUCTURE_TOO_OLD_AT_REGISTRATION",
            "bars_since_structure": age_bars,
            "max_bars": MAX_BARS_SINCE_STRUCTURE,
            "structure_time": structure_time
        })
        return False

    existing = pending.get(symbol)
    if existing:
        try:
            old_time = int(existing.get("structure_time", 0))
        except (TypeError, ValueError):
            old_time = 0

        if old_time == structure_time and existing.get("direction") == sweep["direction"]:
            return False

        # Keep a newer pending setup; replace only with a genuinely newer structure.
        if old_time >= structure_time:
            return False

        DIAGNOSTICS["pending_retest_replaced"] += 1

    created_at = int(time.time() * 1000)
    setup = {
        "symbol": symbol,
        "direction": sweep["direction"],
        "structure": structure["name"],
        "structure_trend": structure.get("trend_context", "NEUTRAL"),
        "structure_time": structure_time,
        "structure_index": int(structure.get("index", len(df15) - 1)),
        "structure_reference": float(structure["reference"]),
        "sweep_time": sweep_time,
        "sweep_low": float(sweep["sweep_low"]),
        "sweep_high": float(sweep["sweep_high"]),
        "liquidity_level": float(sweep.get("liquidity_level", structure["reference"])),
        "created_at_ms": created_at,
        "expires_at": structure_time + RETEST_MAX_BARS_AFTER_STRUCTURE * 5 * 60 * 1000,
        "last_checked_time": structure_time,
        "status": "WAITING_FOR_RETEST"
    }

    pending[symbol] = setup
    DIAGNOSTICS["pending_retest_added"] += 1
    save_state(state)
    return True


def prune_pending_retests(state):
    """Remove malformed, time-expired, or over-age persisted retest setups."""
    pending = state.setdefault("pending_retests", {})
    now_ms = int(time.time() * 1000)
    expired = 0

    for symbol, setup in list(pending.items()):
        try:
            structure_time = int(setup.get("structure_time", 0))
            expires_at = int(setup.get("expires_at", 0))
        except (TypeError, ValueError, AttributeError):
            pending.pop(symbol, None)
            expired += 1
            continue

        if (
            not structure_time
            or not expires_at
            or now_ms > expires_at + 5 * 60 * 1000
        ):
            pending.pop(symbol, None)
            expired += 1

    DIAGNOSTICS["pending_retest_expired"] += expired
    DIAGNOSTICS["pending_retest_active"] = len(pending)
    return expired


def evaluate_pending_retest(state, symbol, df5, df15):
    """
    Evaluate a persisted BOS/CHoCH setup using closed 5m candles.

    A qualified retest may be a few candles old when the bot next scans,
    but it is actionable only while price remains close to the structure
    level and no later candle has invalidated the reclaim.
    """
    pending = state.setdefault("pending_retests", {})
    setup = pending.get(symbol)

    if not setup:
        return None

    try:
        structure_time = int(setup["structure_time"])
        reference = float(setup["structure_reference"])
        direction = str(setup["direction"])
    except (KeyError, TypeError, ValueError):
        pending.pop(symbol, None)
        DIAGNOSTICS["pending_retest_expired"] += 1
        return None

    age_15m = int(
        (pd.to_numeric(df15["time"], errors="coerce") > structure_time).sum()
    )

    post = df5[
        pd.to_numeric(df5["time"], errors="coerce") > structure_time
    ].sort_values("time").reset_index(drop=True)

    if age_15m > MAX_BARS_SINCE_STRUCTURE:
        pending.pop(symbol, None)
        DIAGNOSTICS["late_entry_rejected"] += 1
        DIAGNOSTICS["retest_expired"] += 1
        DIAGNOSTICS["pending_retest_expired"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "RETEST_FRESHNESS",
            "reason": "STRUCTURE_TOO_OLD",
            "bars_since_structure": age_15m,
            "max_bars": MAX_BARS_SINCE_STRUCTURE,
            "direction": direction
        })
        return None

    if post.empty:
        DIAGNOSTICS["retest_waiting"] += 1
        return None

    if len(post) > RETEST_MAX_BARS_AFTER_STRUCTURE:
        pending.pop(symbol, None)
        DIAGNOSTICS["retest_expired"] += 1
        DIAGNOSTICS["pending_retest_expired"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "RETEST",
            "reason": "RETEST_WINDOW_EXPIRED",
            "bars_after_structure": int(len(post)),
            "max_bars": RETEST_MAX_BARS_AFTER_STRUCTURE,
            "direction": direction
        })
        return None

    latest = post.iloc[-1]
    latest_close_time = int(latest["close_time"])
    current_price = float(latest["close"])
    current_atr = float(df5.iloc[-1]["atr"])

    tolerance = current_atr * RETEST_TOLERANCE_ATR
    if (
        not np.isfinite(tolerance)
        or tolerance <= 0
        or not np.isfinite(current_price)
        or current_price <= 0
    ):
        DIAGNOSTICS["retest_waiting"] += 1
        return None

    qualifying = []
    for index, candle in post.iterrows():
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])

        # Permit a controlled wick-through of the level. The previous
        # two-sided tolerance band rejected deeper liquidity probes even
        # when the candle reclaimed the level and closed in the trade
        # direction. Penetration remains capped at 0.40 ATR to avoid
        # treating a broken structure as a valid retest.
        max_penetration = current_atr * RETEST_MAX_PENETRATION_ATR
        if direction == "BUY":
            touched = (
                l <= reference + tolerance
                and l >= reference - max_penetration
            )
            reclaimed = c > reference and c > o
        else:
            touched = (
                h >= reference - tolerance
                and h <= reference + max_penetration
            )
            reclaimed = c < reference and c < o

        if touched and reclaimed:
            qualifying.append((index, candle))

    if qualifying:
        candidate_index, candidate = qualifying[-1]
        candidate_close_time = int(candidate["close_time"])
        event_age_bars = len(post) - 1 - int(candidate_index)

        # A retest is not discarded merely because a later scan candle exists.
        # It is accepted for at most four 5m candles, provided the reclaim held.
        later = post.iloc[int(candidate_index) + 1:]

        if direction == "BUY":
            invalidated_after_retest = (
                not later.empty
                and (pd.to_numeric(later["close"], errors="coerce") <= reference).any()
            )
            still_on_valid_side = current_price > reference
        else:
            invalidated_after_retest = (
                not later.empty
                and (pd.to_numeric(later["close"], errors="coerce") >= reference).any()
            )
            still_on_valid_side = current_price < reference

        current_distance_atr = abs(current_price - reference) / current_atr

        if event_age_bars > RETEST_EVENT_MAX_AGE_BARS:
            pending.pop(symbol, None)
            DIAGNOSTICS["retest_rejected"] += 1
            DIAGNOSTICS["pending_retest_stale"] += 1
            DIAGNOSTICS["pending_retest_expired"] += 1
            reason = "QUALIFIED_RETEST_TOO_OLD"
        elif invalidated_after_retest:
            pending.pop(symbol, None)
            DIAGNOSTICS["retest_rejected"] += 1
            DIAGNOSTICS["pending_retest_expired"] += 1
            reason = "RECLAIM_INVALIDATED_AFTER_RETEST"
        elif (
            not still_on_valid_side
            or current_distance_atr > RETEST_CURRENT_PRICE_MAX_DISTANCE_ATR
        ):
            pending.pop(symbol, None)
            DIAGNOSTICS["retest_rejected"] += 1
            DIAGNOSTICS["pending_retest_stale"] += 1
            DIAGNOSTICS["pending_retest_expired"] += 1
            reason = "PRICE_MOVED_AWAY_FROM_RETEST_ZONE"
        else:
            DIAGNOSTICS["retest_confirmed"] += 1
            setup["last_checked_time"] = latest_close_time
            setup["status"] = "RETEST_CONFIRMED"
            setup["qualified_retest_time"] = candidate_close_time
            setup["qualified_retest_age_bars"] = int(event_age_bars)
            return {
                "time": candidate_close_time,
                "latest_time": latest_close_time,
                "open_time": int(candidate["time"]),
                "open": float(candidate["open"]),
                "high": float(candidate["high"]),
                "low": float(candidate["low"]),
                "close": float(candidate["close"]),
                "current_price": current_price,
                "current_distance_atr": float(current_distance_atr),
                "event_age_bars": int(event_age_bars),
                "bars_after_structure": int(len(post)),
                "tolerance": float(tolerance)
            }

        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "RETEST",
            "reason": reason,
            "retest_time": candidate_close_time,
            "latest_closed_time": latest_close_time,
            "event_age_bars": int(event_age_bars),
            "current_distance_atr": round(float(current_distance_atr), 3),
            "max_event_age_bars": RETEST_EVENT_MAX_AGE_BARS,
            "max_current_distance_atr": RETEST_CURRENT_PRICE_MAX_DISTANCE_ATR,
            "direction": direction
        })
        return None

    setup["last_checked_time"] = latest_close_time

    if len(post) >= RETEST_MAX_BARS_AFTER_STRUCTURE:
        pending.pop(symbol, None)
        DIAGNOSTICS["retest_expired"] += 1
        DIAGNOSTICS["pending_retest_expired"] += 1
        reason = "NO_RETEST_WITHIN_MAXIMUM_WINDOW"
    else:
        DIAGNOSTICS["retest_waiting"] += 1
        reason = "WAITING_FOR_FRESH_5M_RETEST"

    REJECTION_DEBUG.append({
        "symbol": symbol,
        "stage": "RETEST",
        "reason": reason,
        "bars_after_structure": int(len(post)),
        "reference": reference,
        "tolerance": round(float(tolerance), 8),
        "direction": direction
    })
    return None



def calculate_trade_targets(direction, entry, risk, atr):
    """Build wider volatility-aware targets without weakening structural stops."""
    if not np.isfinite([entry, risk, atr]).all() or entry <= 0 or risk <= 0 or atr <= 0:
        raise ValueError("Invalid entry/risk/ATR for target calculation")

    distances = [
        max(risk * float(r_multiple), atr * float(atr_multiple))
        for r_multiple, atr_multiple in zip((1.0, 2.0, 3.0), TP_ATR_MULTIPLIERS)
    ]
    sign = 1.0 if direction == "BUY" else -1.0
    targets = [entry + sign * distance for distance in distances]
    r_values = [distance / risk for distance in distances]
    atr_values = [distance / atr for distance in distances]
    return targets, r_values, atr_values, distances


def validate_target_room(symbol, direction, entry, risk, atr, df5, df15, model):
    """Require clear room to the volatility-aware TP1, not merely to a 1R target."""
    values = [entry, risk, atr]
    if not np.isfinite(values).all() or entry <= 0 or risk <= 0 or atr <= 0:
        DIAGNOSTICS["target_distance_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol, "stage": "TARGET_QUALITY",
            "reason": "INVALID_TARGET_INPUT", "model": model
        })
        return False

    tp1_distance = max(risk, atr * TP_ATR_MULTIPLIERS[0])

    # Use only fractal pivots with two subsequent candles available for confirmation.
    levels = []
    for frame, lookback in ((df5, TARGET_LOOKBACK_5M), (df15, TARGET_LOOKBACK_15M)):
        if frame is None or len(frame) < TARGET_PIVOT_LEFT + TARGET_PIVOT_RIGHT + 3:
            continue
        sample = frame.tail(lookback + TARGET_PIVOT_RIGHT + 2).reset_index(drop=True)
        highs = pd.to_numeric(sample["high"], errors="coerce").to_numpy(dtype=float)
        lows = pd.to_numeric(sample["low"], errors="coerce").to_numpy(dtype=float)
        last_confirmable = len(sample) - TARGET_PIVOT_RIGHT
        for i in range(TARGET_PIVOT_LEFT, last_confirmable):
            left_h = highs[i-TARGET_PIVOT_LEFT:i]
            right_h = highs[i+1:i+1+TARGET_PIVOT_RIGHT]
            left_l = lows[i-TARGET_PIVOT_LEFT:i]
            right_l = lows[i+1:i+1+TARGET_PIVOT_RIGHT]
            if not (np.isfinite(highs[i]) and np.isfinite(lows[i])):
                continue
            if direction == "BUY":
                if highs[i] > entry and highs[i] >= np.max(left_h) and highs[i] > np.max(right_h):
                    levels.append(float(highs[i]))
            else:
                if lows[i] < entry and lows[i] <= np.min(left_l) and lows[i] < np.min(right_l):
                    levels.append(float(lows[i]))

    if levels:
        nearest = min(levels, key=lambda x: abs(x - entry))
        free_distance = abs(nearest - entry)
        required_room = tp1_distance + atr * TARGET_STRUCTURE_BUFFER_ATR
        if free_distance < required_room:
            DIAGNOSTICS["target_structure_rejected"] += 1
            REJECTION_DEBUG.append({
                "symbol": symbol, "stage": "TARGET_QUALITY",
                "reason": "OPPOSING_STRUCTURE_BEFORE_TP1", "model": model,
                "nearest_structure": round(float(nearest), 10),
                "free_distance_atr": round(float(free_distance / atr), 3),
                "required_room_atr": round(float(required_room / atr), 3),
                "tp1_distance_atr": round(float(tp1_distance / atr), 3),
                "direction": direction
            })
            return False
    return True


def generate_swing_reversal_signal(symbol, df5, df15, df1h):
    """Catch fresh 5m rejection turns near 1h swing extremes without waiting for a full trend flip."""
    if len(df5) < SWING_REVERSAL_LOOKBACK_5M + 5 or len(df15) < 5 or len(df1h) < SWING_REVERSAL_LOOKBACK_1H + 3:
        return None

    row, prev, prev2 = df5.iloc[-1], df5.iloc[-2], df5.iloc[-3]
    trend, trend_prev = df15.iloc[-1], df15.iloc[-2]
    h1 = df1h.iloc[-1]
    price, atr = float(row["close"]), float(row["atr"])
    vol_avg = float(row["volume_avg"])
    if not np.isfinite([price, atr, vol_avg, float(row["rsi"]), float(h1["atr"]) ]).all() or price <= 0 or atr <= 0 or vol_avg <= 0 or float(h1["atr"]) <= 0:
        return None

    volume_ratio = float(row["volume"] / vol_avg)
    # Search only the most recent two closed 5m candles; no future-candle pivot confirmation.
    start = max(SWING_REVERSAL_LOOKBACK_5M, len(df5) - 1 - SWING_REVERSAL_MAX_EVENT_AGE)
    for idx in range(len(df5) - 1, start - 1, -1):
        candle = df5.iloc[idx]
        prior = df5.iloc[idx - SWING_REVERSAL_LOOKBACK_5M:idx]
        o, high, low, close = map(float, (candle["open"], candle["high"], candle["low"], candle["close"]))
        rng = high - low
        if rng <= 0:
            continue
        prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
        upper_wick = high - max(o, close)
        lower_wick = min(o, close) - low
        close_pos = (close - low) / rng

        if high > prior_high and close < prior_high:
            direction = "SELL"
            wick_ratio = upper_wick / rng
            rejection = wick_ratio >= SWING_REVERSAL_MIN_WICK_RATIO and close_pos <= 0.48 and close < o
            extreme, level = high, prior_high
            h1_extreme = float(df1h.tail(SWING_REVERSAL_LOOKBACK_1H + 1).iloc[:-1]["high"].max())
            near_hourly_extreme = (h1_extreme - high) <= float(h1["atr"]) * 1.25 and high >= h1_extreme - float(h1["atr"]) * 1.25
        elif low < prior_low and close > prior_low:
            direction = "BUY"
            wick_ratio = lower_wick / rng
            rejection = wick_ratio >= SWING_REVERSAL_MIN_WICK_RATIO and close_pos >= 0.52 and close > o
            extreme, level = low, prior_low
            h1_extreme = float(df1h.tail(SWING_REVERSAL_LOOKBACK_1H + 1).iloc[:-1]["low"].min())
            near_hourly_extreme = (low - h1_extreme) <= float(h1["atr"]) * 1.25 and low <= h1_extreme + float(h1["atr"]) * 1.25
        else:
            continue

        DIAGNOSTICS["swing_reversal_candidates"] += 1
        age = len(df5) - 1 - idx
        distance_atr = abs(price - close) / atr
        if not rejection or age > SWING_REVERSAL_MAX_EVENT_AGE or distance_atr > SWING_REVERSAL_MAX_ENTRY_DISTANCE_ATR:
            DIAGNOSTICS["swing_reversal_rejected"] += 1
            REJECTION_DEBUG.append({"symbol": symbol, "stage": "SWING_REVERSAL", "reason": "REJECTION_OR_FRESHNESS_FAILED", "direction": direction, "wick_ratio": round(float(wick_ratio), 3), "close_position": round(float(close_pos), 3), "event_age": int(age), "entry_distance_atr": round(float(distance_atr), 3)})
            continue

        rsi_now, rsi_prev, rsi_prev2 = float(row["rsi"]), float(prev["rsi"]), float(prev2["rsi"])
        rsi_turn = (rsi_now > rsi_prev and rsi_prev <= rsi_prev2) if direction == "BUY" else (rsi_now < rsi_prev and rsi_prev >= rsi_prev2)
        ema_reclaim = price > float(row["ema20"]) if direction == "BUY" else price < float(row["ema20"])
        rsi_15m_turn = float(trend["rsi"]) > float(trend_prev["rsi"]) if direction == "BUY" else float(trend["rsi"]) < float(trend_prev["rsi"])
        di_aligned = float(row["plus_di"]) > float(row["minus_di"]) if direction == "BUY" else float(row["minus_di"]) > float(row["plus_di"])
        hourly_location = bool(near_hourly_extreme)
        volume_ok = volume_ratio >= SWING_REVERSAL_MIN_VOLUME_RATIO
        adx_ok = float(row["adx"]) >= ADX_MINIMUM
        checks = [hourly_location, rsi_turn, ema_reclaim, rsi_15m_turn, di_aligned, volume_ok, adx_ok]
        score = sum(bool(x) for x in checks)
        # The rejection candle is mandatory; require 4/7 context checks, not a complete trend reversal.
        if score < SWING_REVERSAL_MIN_SCORE:
            DIAGNOSTICS["swing_reversal_rejected"] += 1
            REJECTION_DEBUG.append({"symbol": symbol, "stage": "SWING_REVERSAL", "reason": "CONTEXT_SCORE_TOO_LOW", "score": int(score), "required": SWING_REVERSAL_MIN_SCORE, "direction": direction, "hourly_location": hourly_location, "volume_ratio": round(float(volume_ratio), 3)})
            continue

        stop = extreme - atr * SWING_REVERSAL_STOP_BUFFER_ATR if direction == "BUY" else extreme + atr * SWING_REVERSAL_STOP_BUFFER_ATR
        risk = price - stop if direction == "BUY" else stop - price
        if not np.isfinite(risk) or risk < atr * 0.15 or risk > atr * MAX_RISK_ATR:
            DIAGNOSTICS["swing_reversal_rejected"] += 1
            continue
        if not validate_target_room(symbol, direction, price, risk, atr, df5, df15, "SWING_REVERSAL"):
            DIAGNOSTICS["swing_reversal_rejected"] += 1
            continue

        targets, target_r, target_atr, _ = calculate_trade_targets(direction, price, risk, atr)
        signal_time = int(row["close_time"])
        DIAGNOSTICS["swing_reversal_confirmed"] += 1
        return {
            "symbol": symbol, "direction": direction, "entry": price, "stop_loss": float(stop),
            "tp1": float(targets[0]), "tp2": float(targets[1]), "tp3": float(targets[2]),
            "tp1_r": float(target_r[0]), "tp2_r": float(target_r[1]), "tp3_r": float(target_r[2]),
            "tp1_atr": float(target_atr[0]), "tp2_atr": float(target_atr[1]), "tp3_atr": float(target_atr[2]),
            "risk": float(risk), "risk_atr": float(risk / atr), "high_risk": False, "risk_limit_atr": MAX_RISK_ATR,
            "confirmations": int(score), "confirmation_total": 7, "analysis_label": "SWING REVERSAL NEAR 1H EXTREME",
            "liquidity_sweep": True, "rsi": float(row["rsi"]), "adx": float(row["adx"]), "volume_ratio": volume_ratio, "atr": atr,
            "fvg_lower": None, "fvg_upper": None, "fvg_retest": False, "fvg_required": False,
            "entry_model": "SWING_REVERSAL_5M_REJECTION_1H_LOCATION", "retest_time": None,
            "retest_event_age_bars": int(age), "retest_current_distance_atr": float(distance_atr),
            "retest_bars_after_structure": int(age), "retest_tolerance": None, "bars_since_structure": int(age),
            "entry_extension_atr": float(distance_atr), "structure_reference": float(level),
            "order_block": False, "order_block_zone": None, "structure": "SWING EXTREME REJECTION",
            "structure_trend": "REVERSAL", "structure_time": int(candle["close_time"]), "sweep_time": int(candle["time"]),
            "signal_time": signal_time, "last_checked_time": signal_time, "tp1_hit": False, "tp2_hit": False,
            "tp3_hit": False, "status": "OPEN", "telegram_events": {}, "pending_events": [],
            "created_at": current_time_string(), "created_date": today_string()
        }
    return None


def generate_early_reversal_signal(symbol, df5, df15):
    """Fresh 5m liquidity-sweep reversal with wick, reclaim, RSI and volume checks."""
    if len(df5) < EARLY_REVERSAL_LOOKBACK + 5 or len(df15) < 3:
        return None

    row, prev, prev2 = df5.iloc[-1], df5.iloc[-2], df5.iloc[-3]
    trend, trend_prev = df15.iloc[-1], df15.iloc[-2]
    price, atr = float(row["close"]), float(row["atr"])
    volume_avg = float(row["volume_avg"])
    if not np.isfinite([price, atr, volume_avg, float(row["rsi"])]).all() or price <= 0 or atr <= 0 or volume_avg <= 0:
        return None
    volume_ratio = float(row["volume"] / volume_avg)
    current_index = len(df5) - 1
    first_candidate = max(EARLY_REVERSAL_LOOKBACK, current_index - EARLY_REVERSAL_MAX_AGE_BARS)

    for index in range(current_index, first_candidate - 1, -1):
        candle = df5.iloc[index]
        prior = df5.iloc[index - EARLY_REVERSAL_LOOKBACK:index]
        if len(prior) < EARLY_REVERSAL_LOOKBACK:
            continue
        o, h, l, c = map(float, (candle["open"], candle["high"], candle["low"], candle["close"]))
        candle_range = h - l
        if candle_range <= 0:
            continue
        liq_low, liq_high = float(prior["low"].min()), float(prior["high"].max())
        lower_wick, upper_wick = min(o, c) - l, h - max(o, c)
        close_position = (c - l) / candle_range

        if l < liq_low and c > liq_low:
            direction = "BUY"
            wick_ratio = lower_wick / candle_range
            rejection_ok = wick_ratio >= EARLY_REVERSAL_MIN_WICK_RATIO and close_position >= 0.60 and c > o
            sweep_extreme, liquidity_level = l, liq_low
        elif h > liq_high and c < liq_high:
            direction = "SELL"
            wick_ratio = upper_wick / candle_range
            rejection_ok = wick_ratio >= EARLY_REVERSAL_MIN_WICK_RATIO and close_position <= 0.40 and c < o
            sweep_extreme, liquidity_level = h, liq_high
        else:
            continue

        DIAGNOSTICS["early_reversal_candidates"] += 1
        if not rejection_ok:
            DIAGNOSTICS["early_reversal_rejected"] += 1
            if wick_ratio < EARLY_REVERSAL_MIN_WICK_RATIO:
                reason = "REVERSAL_WICK_TOO_SMALL"
            elif direction == "BUY" and close_position < 0.60:
                reason = "BULLISH_RECLAIM_CLOSE_TOO_WEAK"
            elif direction == "SELL" and close_position > 0.40:
                reason = "BEARISH_RECLAIM_CLOSE_TOO_WEAK"
            else:
                reason = "REVERSAL_CANDLE_BODY_NOT_CONFIRMED"
            REJECTION_DEBUG.append({
                "symbol": symbol, "stage": "EARLY_REVERSAL",
                "reason": reason, "direction": direction,
                "wick_ratio": round(float(wick_ratio), 3),
                "close_position": round(float(close_position), 3)
            })
            continue

        event_age = current_index - index
        current_distance_atr = abs(price - c) / atr
        current_side_valid = (
            price > liquidity_level if direction == "BUY"
            else price < liquidity_level
        )
        if (
            event_age > EARLY_REVERSAL_MAX_AGE_BARS
            or current_distance_atr > EARLY_REVERSAL_MAX_ENTRY_DISTANCE_ATR
            or not current_side_valid
        ):
            DIAGNOSTICS["early_reversal_rejected"] += 1
            REJECTION_DEBUG.append({
                "symbol": symbol,
                "stage": "EARLY_REVERSAL",
                "reason": "REVERSAL_STALE_OR_LEVEL_INVALIDATED",
                "event_age_bars": int(event_age),
                "current_distance_atr": round(float(current_distance_atr), 3),
                "current_side_valid": bool(current_side_valid)
            })
            continue

        rsi_now, rsi_prev, rsi_prev2 = float(row["rsi"]), float(prev["rsi"]), float(prev2["rsi"])
        rsi_turn = (
            rsi_now > rsi_prev and rsi_prev <= rsi_prev2
            if direction == "BUY"
            else rsi_now < rsi_prev and rsi_prev >= rsi_prev2
        )
        # RSI turn and above-average volume remain mandatory. The 7-point
        # score also checks 5m trend/DI/ADX and 15m RSI/EMA20 direction.
        if not rsi_turn or volume_ratio < EARLY_REVERSAL_MIN_VOLUME_RATIO:
            DIAGNOSTICS["early_reversal_rejected"] += 1
            REJECTION_DEBUG.append({
                "symbol": symbol,
                "stage": "EARLY_REVERSAL",
                "reason": "RSI_TURN_OR_VOLUME_CONFIRMATION_MISSING",
                "rsi_turn": bool(rsi_turn),
                "volume_ratio": round(float(volume_ratio), 3),
                "minimum_volume_ratio": EARLY_REVERSAL_MIN_VOLUME_RATIO
            })
            continue

        if direction == "BUY":
            checks = [
                rsi_turn,
                volume_ratio >= EARLY_REVERSAL_MIN_VOLUME_RATIO,
                price > float(row["ema20"]),
                float(trend["rsi"]) > float(trend_prev["rsi"]),
                float(row["plus_di"]) > float(row["minus_di"]),
                float(row["adx"]) >= ADX_MINIMUM,
                float(trend["ema20"]) > float(trend_prev["ema20"])
            ]
            stop_loss = sweep_extreme - atr * SWEEP_STOP_BUFFER
            risk = price - stop_loss
        else:
            checks = [
                rsi_turn,
                volume_ratio >= EARLY_REVERSAL_MIN_VOLUME_RATIO,
                price < float(row["ema20"]),
                float(trend["rsi"]) < float(trend_prev["rsi"]),
                float(row["minus_di"]) > float(row["plus_di"]),
                float(row["adx"]) >= ADX_MINIMUM,
                float(trend["ema20"]) < float(trend_prev["ema20"])
            ]
            stop_loss = sweep_extreme + atr * SWEEP_STOP_BUFFER
            risk = stop_loss - price

        score = sum(bool(x) for x in checks)
        if score < EARLY_REVERSAL_MIN_SCORE:
            DIAGNOSTICS["early_reversal_rejected"] += 1
            REJECTION_DEBUG.append({"symbol": symbol, "stage": "EARLY_REVERSAL", "reason": "REVERSAL_SCORE_TOO_LOW", "score": int(score), "required": EARLY_REVERSAL_MIN_SCORE, "direction": direction, "event_age_bars": int(event_age)})
            continue
        if not np.isfinite(risk) or risk < atr * EARLY_REVERSAL_MIN_RISK_ATR:
            DIAGNOSTICS["early_reversal_rejected"] += 1
            REJECTION_DEBUG.append({"symbol": symbol, "stage": "EARLY_REVERSAL", "reason": "STOP_DISTANCE_TOO_SMALL_OR_INVALID"})
            continue

        if not validate_target_room(symbol, direction, price, risk, atr, df5, df15, "EARLY_REVERSAL"):
            DIAGNOSTICS["early_reversal_rejected"] += 1
            continue

        targets, target_r, target_atr, target_distances = calculate_trade_targets(
            direction, price, risk, atr
        )
        tp1, tp2, tp3 = targets

        signal_time = int(row["close_time"])
        sweep_time = int(candle["time"])
        structure_time = int(candle["close_time"])
        risk_atr = float(risk / atr)
        DIAGNOSTICS["early_reversal_confirmed"] += 1
        return {
            "symbol": symbol, "direction": direction, "entry": price,
            "stop_loss": float(stop_loss), "tp1": float(tp1), "tp2": float(tp2), "tp3": float(tp3),
            "tp1_r": float(target_r[0]), "tp2_r": float(target_r[1]), "tp3_r": float(target_r[2]),
            "tp1_atr": float(target_atr[0]), "tp2_atr": float(target_atr[1]), "tp3_atr": float(target_atr[2]),
            "risk": float(risk), "risk_atr": risk_atr, "high_risk": risk_atr > MAX_RISK_ATR,
            "risk_limit_atr": MAX_RISK_ATR, "confirmations": int(score), "confirmation_total": 7,
            "analysis_label": "EARLY REVERSAL ANALYSIS", "liquidity_sweep": True,
            "rsi": float(row["rsi"]), "adx": float(row["adx"]), "volume_ratio": volume_ratio, "atr": atr,
            "fvg_lower": None, "fvg_upper": None, "fvg_retest": False, "fvg_required": False,
            "entry_model": "EARLY_REVERSAL_5M_SWEEP_REJECTION_RSI_VOLUME",
            "retest_time": None, "retest_event_age_bars": int(event_age),
            "retest_current_distance_atr": float(current_distance_atr), "retest_bars_after_structure": int(event_age),
            "retest_tolerance": None, "bars_since_structure": int(event_age),
            "entry_extension_atr": float(current_distance_atr), "structure_reference": float(liquidity_level),
            "order_block": False, "order_block_zone": None, "structure": "EARLY REVERSAL",
            "structure_trend": "REVERSAL", "structure_time": structure_time, "sweep_time": sweep_time,
            "signal_time": signal_time, "last_checked_time": signal_time,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "status": "OPEN",
            "telegram_events": {}, "pending_events": [],
            "created_at": current_time_string(), "created_date": today_string()
        }
    return None

def _generate_smc_signal(symbol, df5, df15, state=None):
    if len(df5) < 210 or len(df15) < 50:
        DIAGNOSTICS["insufficient_data"] += 1
        return None

    if state is None:
        state = {"pending_retests": {}}

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

    if price <= 0 or atr <= 0 or float(row["volume_avg"]) <= 0:
        DIAGNOSTICS["invalid_values"] += 1
        return None

    volume_ratio = float(row["volume"] / row["volume_avg"])

    # Discover and persist newly confirmed structures, but never discard an
    # existing persisted setup merely because the current scan cannot rediscover it.
    sweep, structure, sweep_count, paired = find_sweep_structure(df15, symbol)

    if not paired:
        resumed_sweep, resumed_structure = resume_pending_structure(df15, symbol, state)
        if resumed_structure is not None:
            sweep, structure, paired = resumed_sweep, resumed_structure, True
            sweep_count = max(sweep_count, 1)

    if sweep_count:
        DIAGNOSTICS["sweep_detected"] += 1
    else:
        DIAGNOSTICS["sweep_rejected"] += 1

    if paired and structure is not None:
        DIAGNOSTICS["structure_detected"] += 1
        register_pending_retest(
            state,
            symbol,
            sweep,
            structure,
            df15
        )
    elif not sweep_count:
        DIAGNOSTICS["structure_rejected"] += 1

    retest = evaluate_pending_retest(
        state,
        symbol,
        df5,
        df15
    )

    if retest is None:
        DIAGNOSTICS["pending_retest_active"] = len(
            state.get("pending_retests", {})
        )
        return None

    setup = state.get("pending_retests", {}).get(symbol)
    if not setup:
        # Defensive guard: a setup may have been pruned during evaluation.
        return None

    direction = setup["direction"]
    structure_reference = float(setup["structure_reference"])
    structure_time = int(setup["structure_time"])
    sweep_time = int(setup["sweep_time"])
    entry_extension_atr = abs(price - structure_reference) / atr

    bars_since_structure = int(
        (pd.to_numeric(df15["time"], errors="coerce") > structure_time).sum()
    )

    if bars_since_structure > MAX_BARS_SINCE_STRUCTURE:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["late_entry_rejected"] += 1
        DIAGNOSTICS["retest_expired"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "ENTRY_FRESHNESS",
            "reason": "STRUCTURE_TOO_OLD",
            "bars_since_structure": bars_since_structure,
            "max_bars": MAX_BARS_SINCE_STRUCTURE,
            "direction": direction
        })
        return None

    if entry_extension_atr > MAX_ENTRY_EXTENSION_ATR:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["entry_extension_rejected"] += 1
        DIAGNOSTICS["entry_distance_rejected"] += 1
        DIAGNOSTICS["retest_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "ENTRY_FRESHNESS",
            "reason": "PRICE_EXTENDED_FROM_STRUCTURE",
            "bars_since_structure": bars_since_structure,
            "entry_extension_atr": round(float(entry_extension_atr), 3),
            "max_extension_atr": MAX_ENTRY_EXTENSION_ATR,
            "direction": direction
        })
        return None

    # Enter at the latest closed candle price; the historical retest only validates setup.
    entry = float(price)

    if float(row["adx"]) < ADX_MINIMUM:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["adx_rejected"] += 1
        DIAGNOSTICS["retest_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "ADX",
            "reason": "ADX_BELOW_MINIMUM_ON_RETEST",
            "adx": float(row["adx"]),
            "minimum": ADX_MINIMUM,
            "direction": direction
        })
        return None

    if atr / price < MIN_ATR_RATIO:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["volatility_rejected"] += 1
        DIAGNOSTICS["retest_rejected"] += 1
        return None

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

    confirmations = sum(bool(value) for value in checks)

    if confirmations < MIN_CONFIRMATIONS:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["confirmations_rejected"] += 1
        DIAGNOSTICS["retest_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol,
            "stage": "CONFIRMATIONS",
            "reason": "CONFIRMATION_SCORE_LOW_ON_RETEST",
            "score": int(confirmations),
            "required": MIN_CONFIRMATIONS,
            "direction": direction
        })
        return None

    if direction == "BUY":
        structural_stop = (
            float(setup["sweep_low"]) - atr * SWEEP_STOP_BUFFER
        )
        stop_loss = (
            structural_stop
            if structural_stop < entry
            else entry - atr * FALLBACK_STOP_ATR
        )
        risk = entry - stop_loss
    else:
        structural_stop = (
            float(setup["sweep_high"]) + atr * SWEEP_STOP_BUFFER
        )
        stop_loss = (
            structural_stop
            if structural_stop > entry
            else entry + atr * FALLBACK_STOP_ATR
        )
        risk = stop_loss - entry

    if not np.isfinite(risk) or risk <= 0:
        state["pending_retests"].pop(symbol, None)
        DIAGNOSTICS["invalid_values"] += 1
        return None

    if not validate_target_room(symbol, direction, entry, risk, atr, df5, df15, "SMC"):
        state["pending_retests"].pop(symbol, None)
        return None

    targets, target_r, target_atr, target_distances = calculate_trade_targets(
        direction, entry, risk, atr
    )
    tp1, tp2, tp3 = targets
    risk_atr = float(risk / atr)
    high_risk = risk_atr > MAX_RISK_ATR

    fvg = detect_fvg_retest(
        df5,
        direction,
        structure_time,
        atr
    )

    order_block_zone = detect_order_block(
        df5,
        direction,
        structure_time,
        structure_reference
    )

    order_block = bool(order_block_zone)
    if order_block:
        DIAGNOSTICS["order_block_detected"] += 1

    signal_time = int(row["close_time"])

    signal = {
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry),
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "tp1_r": float(target_r[0]), "tp2_r": float(target_r[1]), "tp3_r": float(target_r[2]),
        "tp1_atr": float(target_atr[0]), "tp2_atr": float(target_atr[1]), "tp3_atr": float(target_atr[2]),
        "risk": float(risk),
        "risk_atr": risk_atr,
        "high_risk": high_risk,
        "risk_limit_atr": MAX_RISK_ATR,
        "confirmations": int(confirmations),
        "rsi": float(row["rsi"]),
        "adx": float(row["adx"]),
        "volume_ratio": volume_ratio,
        "atr": atr,
        "fvg_lower": fvg["lower"] if fvg else None,
        "fvg_upper": fvg["upper"] if fvg else None,
        "fvg_retest": bool(fvg),
        "fvg_required": False,
        "entry_model": "PERSISTENT_BOS_CHOCH_RECENT_RETEST_CURRENT_PRICE",
        "retest_time": int(retest["time"]),
        "retest_event_age_bars": int(retest["event_age_bars"]),
        "retest_current_distance_atr": float(retest["current_distance_atr"]),
        "retest_bars_after_structure": int(retest["bars_after_structure"]),
        "retest_tolerance": float(retest["tolerance"]),
        "bars_since_structure": int(bars_since_structure),
        "entry_extension_atr": float(entry_extension_atr),
        "structure_reference": float(structure_reference),
        "order_block": order_block,
        "order_block_zone": order_block_zone,
        "structure": setup["structure"],
        "structure_trend": setup.get("structure_trend", "NEUTRAL"),
        "structure_time": structure_time,
        "sweep_time": sweep_time,
        "signal_time": signal_time,
        "last_checked_time": signal_time,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "status": "OPEN",
        "telegram_events": {},
        "pending_events": [],
        "created_at": current_time_string(),
        "created_date": today_string()
    }

    # Remove only after a complete, valid signal has been assembled.
    state["pending_retests"].pop(symbol, None)
    save_state(state)
    return signal


def generate_momentum_signal(symbol, df5, df15):
    """Momentum continuation model for BUY and SELL.

    Evaluate every recent breakout from newest to oldest. A rejected newest
    candidate must not hide an otherwise valid opposite-direction candidate.
    All existing quality, trend, extension and stop-distance filters remain
    mandatory; this change fixes candidate selection rather than loosening them.
    """
    if len(df5) < 30 or len(df15) < 50:
        return None

    current = df5.iloc[-1]
    trend = df15.iloc[-1]
    trend_prev = df15.iloc[-4]

    required = [
        current["close"], current["atr"], current["ema20"],
        trend["close"], trend["ema20"], trend["ema50"],
        trend["plus_di"], trend["minus_di"], trend_prev["ema20"]
    ]
    if not np.isfinite(required).all():
        DIAGNOSTICS["momentum_rejected"] += 1
        REJECTION_DEBUG.append({
            "symbol": symbol, "stage": "MOMENTUM",
            "reason": "INVALID_CURRENT_OR_15M_TREND_VALUES"
        })
        return None

    price, atr = float(current["close"]), float(current["atr"])
    if price <= 0 or atr <= 0:
        DIAGNOSTICS["momentum_rejected"] += 1
        return None

    trend_close = float(trend["close"])
    trend_ema20 = float(trend["ema20"])
    trend_ema20_prev = float(trend_prev["ema20"])
    trend_plus_di = float(trend["plus_di"])
    trend_minus_di = float(trend["minus_di"])

    bullish_trend = (
        trend_close > trend_ema20
        and (trend_ema20 > trend_ema20_prev or trend_plus_di > trend_minus_di)
    )
    bearish_trend = (
        trend_close < trend_ema20
        and (trend_ema20 < trend_ema20_prev or trend_minus_di > trend_plus_di)
    )

    candidates = []
    for idx in range(max(12, len(df5) - 3), len(df5)):
        candle = df5.iloc[idx]
        prior = df5.iloc[idx - 12:idx]
        if len(prior) < 12:
            continue

        vals = [
            candle["open"], candle["high"], candle["low"], candle["close"],
            candle["atr"], candle["adx"], candle["rsi"],
            candle["volume"], candle["volume_avg"],
            candle["plus_di"], candle["minus_di"]
        ]
        if not np.isfinite(vals).all():
            continue

        opn, high = float(candle["open"]), float(candle["high"])
        low, close = float(candle["low"]), float(candle["close"])
        candle_atr = float(candle["atr"])
        candle_range, vol_avg = high - low, float(candle["volume_avg"])
        if candle_atr <= 0 or candle_range <= 0 or vol_avg <= 0:
            continue

        prior_high = float(prior["high"].max())
        prior_low = float(prior["low"].min())
        close_position = (close - low) / candle_range
        body_ratio = abs(close - opn) / candle_range

        common = {
            "idx": idx,
            "body_ratio": body_ratio,
            "candle_range_atr": candle_range / candle_atr,
            "volume_ratio": float(candle["volume"] / vol_avg),
            "adx": float(candle["adx"]),
            "rsi": float(candle["rsi"]),
            "plus_di": float(candle["plus_di"]),
            "minus_di": float(candle["minus_di"])
        }
        if (
            close > prior_high + 0.05 * candle_atr
            and close > opn and close_position >= 0.72
        ):
            candidates.append({
                **common, "direction": "BUY", "level": prior_high
            })
        elif (
            close < prior_low - 0.05 * candle_atr
            and close < opn and close_position <= 0.28
        ):
            candidates.append({
                **common, "direction": "SELL", "level": prior_low
            })

    if not candidates:
        DIAGNOSTICS["momentum_reject_breakout"] += 1
        return None

    # Count all found setups, not just the last candidate in the list.
    DIAGNOSTICS["momentum_candidates"] += len(candidates)
    stop_window = df5.iloc[max(0, len(df5) - 6):]

    # Newest candidate gets priority, but a failed candidate no longer masks
    # an earlier candidate in the opposite direction.
    for candidate in reversed(candidates):
        direction = candidate["direction"]
        breakout_level = float(candidate["level"])
        breakout_idx = int(candidate["idx"])
        breakout_age = len(df5) - 1 - breakout_idx

        if direction == "BUY":
            trend_ok = bullish_trend
            di_ok = candidate["plus_di"] > candidate["minus_di"]
            rsi_ok = 52 <= candidate["rsi"] <= 76
            still_holding = price > breakout_level
            breakout_extension = (price - breakout_level) / atr
            ema_extension = (price - float(current["ema20"])) / atr
            stop_loss = min(float(stop_window["low"].min()), breakout_level - 0.25 * atr)
            risk = price - stop_loss
        else:
            trend_ok = bearish_trend
            di_ok = candidate["minus_di"] > candidate["plus_di"]
            rsi_ok = 24 <= candidate["rsi"] <= 48
            still_holding = price < breakout_level
            breakout_extension = (breakout_level - price) / atr
            ema_extension = (float(current["ema20"]) - price) / atr
            stop_loss = max(float(stop_window["high"].max()), breakout_level + 0.25 * atr)
            risk = stop_loss - price

        def reject(counter, reason, **details):
            DIAGNOSTICS[counter] += 1
            DIAGNOSTICS["momentum_rejected"] += 1
            REJECTION_DEBUG.append({
                "symbol": symbol, "stage": "MOMENTUM", "reason": reason,
                "direction": direction, "breakout_age_bars": breakout_age,
                **details
            })

        if not still_holding:
            reject("momentum_reject_extension", "BREAKOUT_LEVEL_RECLAIMED")
            continue

        if not trend_ok:
            reject(
                "momentum_reject_trend", "15M_TREND_NOT_ALIGNED",
                trend_close_vs_ema20=round(trend_close - trend_ema20, 6),
                ema20_slope=round(trend_ema20 - trend_ema20_prev, 6),
                **{"15m_di_aligned": bool(
                    trend_plus_di > trend_minus_di if direction == "BUY"
                    else trend_minus_di > trend_plus_di
                )}
            )
            continue

        if candidate["volume_ratio"] < 1.15 or candidate["adx"] < 17 or not di_ok:
            reject(
                "momentum_reject_volume_adx", "BREAKOUT_VOLUME_ADX_OR_DI_WEAK",
                volume_ratio=round(candidate["volume_ratio"], 3),
                adx=round(candidate["adx"], 2), di_aligned=bool(di_ok)
            )
            continue

        if (
            candidate["body_ratio"] < 0.55
            or candidate["candle_range_atr"] > 2.20
            or breakout_extension > 0.65 or ema_extension > 1.50
            or not rsi_ok or breakout_age > 2
        ):
            reject(
                "momentum_reject_extension", "WEAK_OR_LATE_BREAKOUT",
                body_ratio=round(candidate["body_ratio"], 3),
                breakout_extension_atr=round(breakout_extension, 3),
                ema_extension_atr=round(ema_extension, 3),
                breakout_range_atr=round(candidate["candle_range_atr"], 3),
                rsi=round(candidate["rsi"], 2)
            )
            continue

        risk_atr = risk / atr
        if not np.isfinite(risk) or risk <= 0 or risk_atr < 0.35 or risk_atr > 1.80:
            reject(
                "momentum_reject_risk", "STOP_DISTANCE_OUTSIDE_VALID_RANGE",
                risk_atr=round(float(risk_atr), 3)
            )
            continue

        if not validate_target_room(symbol, direction, price, risk, atr, df5, df15, "MOMENTUM"):
            DIAGNOSTICS["momentum_rejected"] += 1
            continue

        targets, target_r, target_atr, target_distances = calculate_trade_targets(
            direction, price, risk, atr
        )
        tp1, tp2, tp3 = targets

        signal_time = int(current["close_time"])
        DIAGNOSTICS["momentum_confirmed"] += 1
        return {
            "symbol": symbol, "direction": direction, "entry": price,
            "stop_loss": float(stop_loss), "tp1": float(tp1),
            "tp2": float(tp2), "tp3": float(tp3),
            "tp1_r": float(target_r[0]), "tp2_r": float(target_r[1]), "tp3_r": float(target_r[2]),
            "tp1_atr": float(target_atr[0]), "tp2_atr": float(target_atr[1]), "tp3_atr": float(target_atr[2]),
            "risk": float(risk),
            "risk_atr": float(risk_atr), "high_risk": risk_atr > MAX_RISK_ATR,
            "risk_limit_atr": MAX_RISK_ATR, "confirmations": 6,
            "confirmation_total": 7, "analysis_label": "MOMENTUM CONTINUATION ANALYSIS",
            "liquidity_sweep": False, "rsi": float(current["rsi"]),
            "adx": float(current["adx"]), "volume_ratio": candidate["volume_ratio"],
            "atr": atr, "fvg_lower": None, "fvg_upper": None,
            "fvg_retest": False, "fvg_required": False,
            "entry_model": "MOMENTUM_CONTINUATION_RECENT_CLOSED_5M_BREAKOUT",
            "retest_time": None, "retest_event_age_bars": breakout_age,
            "retest_current_distance_atr": float(breakout_extension),
            "retest_bars_after_structure": 0, "retest_tolerance": None,
            "bars_since_structure": 0, "entry_extension_atr": float(breakout_extension),
            "structure_reference": float(breakout_level), "order_block": False,
            "order_block_zone": None, "structure": "MOMENTUM BREAKOUT",
            "structure_trend": "CONTINUATION",
            "structure_time": int(df5.iloc[breakout_idx]["close_time"]),
            "sweep_time": int(df5.iloc[breakout_idx]["close_time"]),
            "signal_time": signal_time, "last_checked_time": signal_time,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "status": "OPEN",
            "telegram_events": {}, "pending_events": [],
            "created_at": current_time_string(),
            "created_date": today_string()
        }

    return None


def generate_signal(symbol, df5, df15, df1h=None, state=None):
    """Swing reversals first, then SMC, stricter early reversal and momentum continuation."""
    if df1h is not None and len(df1h) >= SWING_REVERSAL_LOOKBACK_1H + 3:
        df5_ind = calculate_indicators(df5)
        df15_ind = calculate_indicators(df15)
        df1h_ind = calculate_indicators(df1h)
        swing_signal = generate_swing_reversal_signal(symbol, df5_ind, df15_ind, df1h_ind)
        if swing_signal is not None:
            return swing_signal
    smc_signal = _generate_smc_signal(symbol, df5, df15, state)
    if smc_signal is not None:
        return smc_signal
    if len(df5) < 210 or len(df15) < 50:
        return None
    df5_ind = calculate_indicators(df5)
    df15_ind = calculate_indicators(df15)
    early_signal = generate_early_reversal_signal(symbol, df5_ind, df15_ind)
    if early_signal is not None:
        return early_signal
    return generate_momentum_signal(symbol, df5_ind, df15_ind)



# =========================================================
# TELEGRAM EVENT QUEUE
# =========================================================

def queue_event(signal, event):
    """Queue each TP, stop-loss, or trailing-stop event once."""
    if event not in ("ENTRY", "TP1", "TP2", "TP3", "SL", "TRAILING_STOP"):
        return

    sent = signal.setdefault("telegram_events", {})
    if sent.get(event):
        return

    pending = signal.setdefault("pending_events", [])
    if event not in pending:
        pending.append(event)


def process_pending_events(state):
    """Send ENTRY once, then reply once for each trade-management milestone."""
    changed = False

    for signal in state.get("signals", []):
        pending = signal.setdefault("pending_events", [])
        sent = signal.setdefault("telegram_events", {})

        while pending:
            event = pending[0]

            if sent.get(event):
                pending.pop(0)
                changed = True
                continue

            if event == "ENTRY":
                if not send_signal(signal):
                    break
                sent["ENTRY"] = True
                pending.pop(0)
                changed = True
                save_state(state)
                continue

            # Legacy signals without an original Telegram message cannot receive
            # reply notifications. Drop that one queued event silently so it
            # cannot print the same warning or block later signals on every scan.
            if not signal.get("telegram_message_id"):
                pending.pop(0)
                sent[event] = "SKIPPED_NO_ORIGINAL_MESSAGE"
                changed = True
                save_state(state)
                continue

            if not send_event_message(signal, event):
                break

            sent[event] = True
            pending.pop(0)
            changed = True
            save_state(state)

    return changed


# =========================================================
# SIGNAL MILESTONE MONITOR
# =========================================================

def update_signal_results(state):
    """Track TP milestones, protect gains, and trail beyond TP3 for up to 48h."""
    changed = False
    now_ms = int(time.time() * 1000)

    for signal in state.get("signals", []):
        if signal.get("status") != "OPEN":
            continue

        created_ms = signal_created_timestamp_ms(signal)
        if created_ms is not None and now_ms - created_ms >= MAX_SIGNAL_TRACKING_AGE_MS:
            continue

        symbol = signal.get("symbol")
        if not symbol:
            continue

        last_checked = int(signal.get("last_checked_time", signal.get("signal_time", 0)))
        try:
            candles = get_tracking_candles(symbol, last_checked + 1)
        except Exception as error:
            print(f"Tracking error {symbol}: {error}")
            continue
        if candles.empty:
            continue

        if created_ms is not None:
            expires_at = created_ms + MAX_SIGNAL_TRACKING_AGE_MS
            candles = candles[candles["close_time"] <= expires_at].reset_index(drop=True)
            if candles.empty:
                continue

        direction = signal["direction"]
        entry = float(signal["entry"])
        initial_stop = float(signal["stop_loss"])
        tp1, tp2, tp3 = float(signal["tp1"]), float(signal["tp2"]), float(signal["tp3"])
        atr = float(signal.get("atr", 0) or 0)

        # Restore the protected stop correctly after a workflow restart.
        if "active_stop" not in signal:
            if signal.get("tp2_hit"):
                signal["active_stop"] = tp1
            elif signal.get("tp1_hit"):
                signal["active_stop"] = entry
            else:
                signal["active_stop"] = initial_stop
            changed = True

        if signal.get("trailing_active") and "trailing_stop" in signal:
            signal["active_stop"] = float(signal["trailing_stop"])

        for _, candle in candles.iterrows():
            high, low = float(candle["high"]), float(candle["low"])
            close_time = int(candle["close_time"])
            trailing_active = bool(signal.get("trailing_active", False))
            active_stop = float(signal.get("active_stop", initial_stop))

            if direction == "BUY":
                stop_hit = low <= active_stop
                hit1, hit2, hit3 = high >= tp1, high >= tp2, high >= tp3
            else:
                stop_hit = high >= active_stop
                hit1, hit2, hit3 = low <= tp1, low <= tp2, low <= tp3

            # Conservative candle ordering: an already-active stop takes priority.
            if stop_hit:
                signal["status"] = "TRAILING_STOP" if trailing_active else "SL"
                signal["stop_hit_time"] = close_time
                if trailing_active:
                    queue_event(signal, "TRAILING_STOP")
                elif not signal.get("tp1_hit"):
                    queue_event(signal, "SL")
                changed = True
                break

            if hit1 and not signal.get("tp1_hit"):
                signal["tp1_hit"] = True
                signal["tp1_hit_time"] = close_time
                signal["active_stop"] = entry  # Protect the entry after TP1.
                queue_event(signal, "TP1")
                changed = True

            if hit2 and not signal.get("tp2_hit"):
                signal["tp2_hit"] = True
                signal["tp2_hit_time"] = close_time
                signal["active_stop"] = tp1  # Lock the first target after TP2.
                queue_event(signal, "TP2")
                changed = True

            if hit3 and not signal.get("tp3_hit"):
                signal["tp3_hit"] = True
                signal["tp3_hit_time"] = close_time
                signal["trailing_active"] = True
                signal["best_price"] = high if direction == "BUY" else low
                if atr > 0:
                    candidate = (
                        signal["best_price"] - atr * TRAILING_STOP_ATR_MULTIPLIER
                        if direction == "BUY"
                        else signal["best_price"] + atr * TRAILING_STOP_ATR_MULTIPLIER
                    )
                    old_stop = float(signal.get("active_stop", tp1))
                    signal["trailing_stop"] = (
                        max(old_stop, candidate) if direction == "BUY"
                        else min(old_stop, candidate)
                    )
                    signal["active_stop"] = signal["trailing_stop"]
                queue_event(signal, "TP3")
                changed = True

            # Once trailing is active, ratchet it only in the profitable direction.
            # The updated stop becomes active from the next candle, avoiding
            # assumptions about intrabar ordering in this candle.
            if signal.get("trailing_active") and signal.get("tp3_hit"):
                best = float(signal.get("best_price", high if direction == "BUY" else low))
                best = max(best, high) if direction == "BUY" else min(best, low)
                signal["best_price"] = best
                if atr > 0:
                    candidate = (
                        best - atr * TRAILING_STOP_ATR_MULTIPLIER
                        if direction == "BUY"
                        else best + atr * TRAILING_STOP_ATR_MULTIPLIER
                    )
                    old_stop = float(signal.get("trailing_stop", signal.get("active_stop", tp1)))
                    new_stop = max(old_stop, candidate) if direction == "BUY" else min(old_stop, candidate)
                    if new_stop != old_stop:
                        signal["trailing_stop"] = new_stop
                        signal["active_stop"] = new_stop
                        changed = True

        signal["last_checked_time"] = int(candles.iloc[-1]["close_time"])
        changed = True

    return changed


def prune_pending_setups(state):
    """Expire persisted waiting setups after their 60-minute confirmation window."""
    pending = state.setdefault("pending_setups", {})
    now_ms = int(time.time() * 1000)
    expired = 0

    for symbol, setup in list(pending.items()):
        try:
            expires_at = int(setup.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0

        if not expires_at or now_ms >= expires_at:
            pending.pop(symbol, None)
            expired += 1

    DIAGNOSTICS["waiting_queue_expired"] += expired
    DIAGNOSTICS["waiting_queue_active"] = len(pending)
    return expired


def sync_pending_setups(state):
    """Apply only the newest structure event per symbol to persisted setup state."""
    pending = state.setdefault("pending_setups", {})
    now_ms = int(time.time() * 1000)
    replaced = 0
    newest_by_symbol = {}

    terminal_states = {
        "CONFIRMED",
        "EXPIRED_NO_BREAK",
        "NO_FRESH_UNBROKEN_PIVOT"
    }

    for item in STRUCTURE_DEBUG:
        symbol = item.get("symbol")
        if not symbol:
            continue
        try:
            event_time = int(item.get("sweep_time", 0) or 0)
        except (TypeError, ValueError):
            continue

        previous = newest_by_symbol.get(symbol)
        if previous is None:
            newest_by_symbol[symbol] = item
            continue

        try:
            previous_time = int(previous.get("sweep_time", 0) or 0)
        except (TypeError, ValueError):
            previous_time = 0

        if event_time > previous_time:
            newest_by_symbol[symbol] = item
        elif event_time == previous_time:
            # At equal timestamps, a terminal event wins over a waiting event.
            if (
                item.get("state") in terminal_states
                and previous.get("state") == "WAITING_FOR_CONFIRMATION"
            ):
                newest_by_symbol[symbol] = item

    for symbol, item in newest_by_symbol.items():
        direction = item.get("direction")
        state_name = item.get("state")
        try:
            sweep_time = int(item.get("sweep_time", 0) or 0)
        except (TypeError, ValueError):
            sweep_time = 0

        old = pending.get(symbol)
        old_time = 0
        if isinstance(old, dict):
            try:
                old_time = int(old.get("sweep_time", 0) or 0)
            except (TypeError, ValueError):
                old_time = 0

        if old and old_time > sweep_time:
            continue

        if state_name in terminal_states:
            if old and (
                not direction
                or old.get("direction") == direction
            ) and sweep_time >= old_time:
                pending.pop(symbol, None)
            continue

        if state_name != "WAITING_FOR_CONFIRMATION":
            continue

        expires_at = sweep_time + WAITING_SETUP_MAX_AGE_MS
        if not sweep_time or now_ms >= expires_at:
            continue

        new_setup = {
            "symbol": symbol,
            "direction": direction,
            "sweep_time": sweep_time,
            "reference": item.get("reference"),
            "reference_type": item.get("reference_type"),
            "bars_checked": item.get("bars_checked"),
            "sweep_low": item.get("sweep_low"),
            "sweep_high": item.get("sweep_high"),
            "liquidity_level": item.get("liquidity_level", item.get("reference")),
            "bars_required": MAX_STRUCTURE_BARS,
            "expires_at": expires_at,
            "updated_at": current_time_string()
        }

        if old and (
            old.get("direction") != direction
            or old_time != sweep_time
        ):
            replaced += 1

        pending[symbol] = new_setup

    DIAGNOSTICS["waiting_queue_replaced"] += replaced
    DIAGNOSTICS["waiting_queue_active"] = len(pending)
    return len(pending)


# =========================================================
# SCANNER
# =========================================================

def run_scanner():
    global DIAGNOSTICS
    global STRUCTURE_DEBUG
    global REJECTION_DEBUG

    DIAGNOSTICS = {
        key: 0 for key in DIAGNOSTICS
    }

    STRUCTURE_DEBUG = []
    REJECTION_DEBUG = []

    print("=" * 48)

    print(
        f"SMART MONEY CRYPTO SIGNAL BOT v{VERSION}"
    )

    print("Iran time:", current_time_string())
    print("Entry models: Early Swing Reversal (5m rejection + 1h location) + SMC + Early Reversal + Momentum Continuation.")

    print("=" * 48)

    state = load_state()
    prune_expired_signals(state)
    prune_pending_setups(state)
    prune_pending_retests(state)
    save_state(state)

    update_signal_results(state)

    save_state(state)

    process_pending_events(state)

    news_active, news_event, calendar_ok = (
        active_high_impact_news()
    )

    news_warning = (
        format_news_event(news_event)
        if news_active
        else None
    )

    if not calendar_ok:
        DIAGNOSTICS["news_calendar_errors"] += 1

        print(
            "NEWS WARNING: calendar unavailable; "
            "signals will continue normally."
        )

    elif news_active:
        DIAGNOSTICS["news_warning_active"] += 1

        print(
            "HIGH-IMPACT NEWS WARNING "
            "(signals are NOT blocked):",
            news_warning
        )

        news_state = state.setdefault(
            "news_filter",
            {}
        )

        event_key = str(
            news_event.get("id")
            or news_event.get("ts")
        )

        if news_state.get("last_alert_key") != event_key:
            send_telegram_text(
                "📰 HIGH-IMPACT ECONOMIC NEWS WARNING\n"
                "⚠️ The bot WILL continue sending signals.\n"
                f"{news_warning}\n"
                f"Warning window: "
                f"{NEWS_PAUSE_BEFORE_MINUTES} min before / "
                f"{NEWS_PAUSE_AFTER_MINUTES} min after.\n"
                "Please review the news and decide manually "
                "whether to enter.\n"
                "Open trades continue to be monitored."
            )

            news_state["last_alert_key"] = event_key

        save_state(state)

    if not is_trading_time():
        print(
            "Outside entry hours. "
            "Open-trade tracking completed."
        )
        save_state(state)
        return

    try:
        coins = get_market_coins()

    except Exception as error:
        DIAGNOSTICS["errors"] += 1

        print("MARKET UNIVERSE ERROR:", error)

        coins = []

    print(
        f"Market universe: {len(coins)} active Nobitex "
        f"assets with liquid Binance USDT markets "
        f"(max {TOP_COINS})."
    )

    new_signals = 0

    coins_to_scan = coins

    print(
        f"Scanning {len(coins_to_scan)} coins "
        "for new entries..."
    )

    for symbol in coins_to_scan:
        try:
            if has_open_trade_for_symbol(state, symbol):
                DIAGNOSTICS["open_trade_locked"] += 1
                continue

            df5 = get_candles(
                symbol,
                TIMEFRAME
            )

            df15 = get_candles(
                symbol,
                TREND_TIMEFRAME
            )

            try:
                df1h = get_candles(symbol, "1h")
            except Exception as context_error:
                # The 1h context improves reversal location, but its temporary
                # unavailability must not disable the established signal models.
                df1h = None
                print(f"1H CONTEXT UNAVAILABLE {symbol}: {context_error}")

            signal = generate_signal(
                symbol,
                df5,
                df15,
                df1h,
                state
            )

            if signal is None:
                continue

            if news_warning:
                signal["news_warning"] = news_warning

            if has_open_trade_for_symbol(state, symbol):
                DIAGNOSTICS["duplicate_signals"] += 1
                continue

            same_signal = any(
                old.get("symbol") == symbol
                and (
                    int(old.get("signal_time", 0))
                    == int(signal["signal_time"])
                    or (
                        old.get("sweep_time")
                        == signal.get("sweep_time")
                        and old.get("structure_time")
                        == signal.get("structure_time")
                    )
                )
                for old in state["signals"]
            )

            if same_signal:
                DIAGNOSTICS["duplicate_signals"] += 1
                continue

            state["signals"].append(signal)

            queue_event(
                signal,
                "ENTRY"
            )

            new_signals += 1

            risk_tag = (
                " ⚠️ HIGH RISK"
                if signal["risk_atr"] > MAX_RISK_ATR
                else ""
            )

            print(
                f"NEW SIGNAL: {symbol} "
                f"{signal['direction']} "
                f"{signal['confirmations']}/7 "
                f"RISK={signal['risk_atr']:.2f} ATR"
                f"{risk_tag}"
            )

            save_state(state)

        except Exception as error:
            DIAGNOSTICS["errors"] += 1

            print(f"ERROR {symbol}: {error}")

    sync_pending_setups(state)

    save_state(state)

    process_pending_events(state)

    save_state(state)

    print("=" * 48)
    print("SCAN FINISHED")
    print("Coins in market universe:", len(coins))
    print("Coins scanned for entries:", len(coins_to_scan))
    print("Signals found:", new_signals)
    print(
        "Retest diagnostics:",
        {
            "waiting": DIAGNOSTICS.get("retest_waiting", 0),
            "confirmed": DIAGNOSTICS.get("retest_confirmed", 0),
            "rejected": DIAGNOSTICS.get("retest_rejected", 0),
            "expired": DIAGNOSTICS.get("retest_expired", 0)
        }
    )
    print("Pending structure setups:", len(state.get("pending_setups", {})))
    print("Pending retest setups:", len(state.get("pending_retests", {})))
    print("Diagnostics:", DIAGNOSTICS)
    print("=" * 48)

    print("STRUCTURE DEBUG DETAILS")

    for item in STRUCTURE_DEBUG:
        print(item)

    print("=" * 48)
    print("REJECTION DEBUG DETAILS")

    for item in REJECTION_DEBUG:
        print(item)

    print("=" * 48)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    try:
        run_scanner()

    except Exception as error:
        print("FATAL ERROR:", error)
        raise
