"""
Standalone diagnostic tool to verify WEEX API authentication, Telegram alerts,
and Volume Profile calculation before going live.
Run with: python test_connection.py
"""

import sys
import config
from weex_client import WeexClient
from telegram_notifier import TelegramNotifier
from scanner import MarketScanner


def test_telegram(notifier: TelegramNotifier):
    print("\n--- 1. Testing Telegram Alert Dispatcher ---")
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("  [!] Telegram credentials missing in .env (Skipping live alert)")
        return

    success = notifier.send_message("<b>[DIAGNOSTIC TEST]</b>\nVolume Profile bot successfully connected to Telegram! 🚀")
    if success:
        print("  [+] Telegram message delivered successfully! Check your Telegram app.")
    else:
        print("  [-] Telegram delivery failed. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")


def test_weex(weex: WeexClient):
    print("\n--- 2. Testing WEEX V3 Contract API ---")
    if not config.WEEX_API_KEY or not config.WEEX_API_SECRET or not config.WEEX_PASSPHRASE:
        print("  [!] WEEX credentials missing or placeholder in .env")
        print("      (Bot can still run in DRY_RUN mode using simulated balances)")
        return

    try:
        balance = weex.get_available_margin()
        print(f"  [+] WEEX Authentication Successful!")
        print(f"      Available USDT Balance: ${balance:,.2f}")

        positions = weex.get_active_positions()
        print(f"      Active Positions Count: {len(positions)}")
        for p in positions:
            print(f"      - {p['symbol']} ({p['side']}): Size={p['size']}, Entry=${p['entryPrice']}")
    except Exception as e:
        print(f"  [-] WEEX connection error: {e}")


def test_scanner(scanner: MarketScanner):
    print("\n--- 3. Testing 5-Minute Volume Profile Scanner ---")
    universe = scanner.get_target_universe(max_pairs=5)
    print(f"  [+] Screener found candidate pairs: {universe}")

    if universe:
        test_sym = universe[0]
        print(f"  [*] Evaluating live 5m candles on {test_sym}...")
        df = scanner.fetch_recent_klines(test_sym, limit=100)
        print(f"  [+] Downloaded {len(df)} 5m candles for {test_sym}")

        signal = scanner.evaluate_signal(test_sym)
        if signal:
            print(f"  [!] Signal Triggered right now on {test_sym}: {signal['side']}")
        else:
            print(f"  [+] Signal engine ran successfully (No entry trigger on current bar).")


def main():
    print("=" * 60)
    print(" VOLUME PROFILE WEEX BOT - PRE-FLIGHT DIAGNOSTIC")
    print("=" * 60)

    notifier = TelegramNotifier()
    weex = WeexClient()
    scanner = MarketScanner()

    test_telegram(notifier)
    test_weex(weex)
    test_scanner(scanner)

    print("\n" + "=" * 60)
    print("Pre-flight check completed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
