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

        # BUY signal with ADX direction filter

        if (
            buy_score >= MIN_CONFIRMATIONS
            and buy_score > sell_score
        ):

            if plus_di <= minus_di:
                return None

            direction = "BUY"
            score = buy_score

        # SELL signal with ADX direction filter

        elif (
            sell_score >= MIN_CONFIRMATIONS
            and sell_score > buy_score
        ):

            if minus_di <= plus_di:
                return None

            direction = "SELL"
            score = sell_score

        else:
            return None

        # ATR risk management

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

        print(f"{symbol} ERROR: {error}")
        return None


# ---------------- MAIN ----------------

def main():

    now = get_iran_time()

    print("=" * 45)
    print("CRYPTO SIGNAL BOT - ADX VERSION")
    print("Iran time:", now.strftime("%Y-%m-%d %H:%M"))
    print("=" * 45)

    # Active hours: 09:00 to 21:00 Iran time

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

    print("\nSCAN FINISHED")
    print("Signals found:", len(signals))

    for signal in signals:

        print("\n-----------------------------")

        print(signal["symbol"], signal["direction"])

        print(
            "Confirmations:",
            signal["confirmations"],
            "/ 7"
        )

        print("Entry:", signal["entry"])
        print("Stop Loss:", signal["stop_loss"])
        print("TP1:", signal["tp1"])
        print("TP2:", signal["tp2"])

        print("RSI:", round(signal["rsi"], 2))
        print("ADX:", round(signal["adx"], 2))

        print(
            "Volume ratio:",
            round(signal["volume_ratio"], 2)
        )

        send_telegram_message(signal)

    print("\nBOT SCAN COMPLETED")


if name == "main":
    main()
