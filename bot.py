core = sum(buy_checks)
        sell_score = sum(sell_checks)

        # ==================================
        # SIGNAL DECISION
        # ==================================

        # Avoid conflicting signals

        if (
            buy_score >= MIN_CONFIRMATIONS
            and sell_score >= MIN_CONFIRMATIONS
        ):
            return None

        if buy_score >= MIN_CONFIRMATIONS:

            direction = "BUY"
            score = buy_score

        elif sell_score >= MIN_CONFIRMATIONS:

            direction = "SELL"
            score = sell_score

        else:
            return None

        # ==================================
        # STOP LOSS AND TARGETS
        # ==================================

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

            "score": score,

            "price": price,

            "stop_loss": stop_loss,

            "target1": target1,

            "target2": target2,

            "rsi": current["rsi"],

            "volume_ratio": volume_ratio

        }

    except Exception as error:

        print(
            f"{symbol} ERROR: {error}"
        )

        return None


# ==========================================
# MAIN
# ==========================================

def main():

    now = get_iran_time()

    print("=" * 45)

    print("CRYPTO SIGNAL BOT V2")

    print(
        "Iran time:",
        now.strftime("%Y-%m-%d %H:%M")
    )

    print("=" * 45)

    # Active hours: 09:00 to 21:00 Iran time

    if not (9 <= now.hour < 21):

        print(
            "Outside active hours. No scanning."
        )

        return

    # Get top coins

    coins = get_market_coins()

    print(
        f"Scanning {len(coins)} coins..."
    )

    signals = []

    for symbol in coins:

        result = analyze_coin(symbol)

        if result:

            signals.append(result)

        time.sleep(0.15)

    # ==================================
    # PRINT RESULTS
    # ==================================

    print("\nSCAN FINISHED")

    print(
        "Coins scanned:",
        len(coins)
    )

    print(
        "Signals found:",
        len(signals)
    )

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

        print(
            "Entry:",
            format(signal["price"], ".10g")
        )

        print(
            "Stop Loss:",
            format(signal["stop_loss"], ".10g")
        )

        print(
            "Target 1:",
            format(signal["target1"], ".10g")
        )

        print(
            "Target 2:",
            format(signal["target2"], ".10g")
        )

        print(
            "RSI:",
            round(signal["rsi"], 2)
        )

        print(
            "Volume ratio:",
            round(signal["volume_ratio"], 2)
        )

    print("\nBOT SCAN COMPLETED")


# ==========================================
# START BOT
# ==========================================

if __name__ == "__main__":

    main()
