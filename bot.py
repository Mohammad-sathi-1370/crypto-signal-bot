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


if name == "main":
    main()
