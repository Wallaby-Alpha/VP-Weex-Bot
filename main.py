import time
import sys
import logging
from datetime import datetime, timezone

import config
from weex_client import WeexClient
from telegram_notifier import TelegramNotifier
from state_manager import StateManager
from scanner import MarketScanner
from executor import TradeExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("MAIN_DAEMON")


def wait_until_next_bar(timeframe_sec: int = 300, offset_sec: int = 3):
    """
    Sleeps accurately until offset_sec seconds past the close of the next 5-minute candle.
    For example: 12:05:03, 12:10:03, etc.
    """
    now = time.time()
    time_to_wait = timeframe_sec - (now % timeframe_sec) + offset_sec
    next_bar_time = datetime.fromtimestamp(now + time_to_wait, tz=timezone.utc).strftime("%H:%M:%S")
    logger.info(f"Sleeping {time_to_wait:.1f}s until next candle close ({next_bar_time} UTC)...")
    time.sleep(time_to_wait)


def main():
    logger.info("=" * 60)
    logger.info("STARTING VOLUME PROFILE WEEX TRADING BOT (5m Timeframe)")
    logger.info(f"MODE: {'DRY-RUN (Simulated)' if config.DRY_RUN else 'LIVE WEEX CONTRACT EXECUTION'}")
    logger.info("=" * 60)

    weex_client = WeexClient()
    notifier = TelegramNotifier()
    state_mgr = StateManager()
    scanner = MarketScanner()
    executor = TradeExecutor(weex_client, notifier, state_mgr)

    # Initial universe and balance check
    universe = scanner.get_target_universe(max_pairs=config.MAX_PAIRS)
    balance = weex_client.get_available_margin()

    logger.info(f"Initial USDT Balance: ${balance:,.2f}")
    logger.info(f"Loaded Universe of {len(universe)} symbols.")
    notifier.notify_startup(universe, config.DRY_RUN, balance)

    last_universe_refresh = time.time()

    while True:
        try:
            # Synchronize to the close of each 5m candle
            wait_until_next_bar(timeframe_sec=300, offset_sec=3)

            scan_start = time.time()
            logger.info("--- Starting 5m Candle Scan Cycle ---")

            # Refresh universe every 4 hours
            if time.time() - last_universe_refresh > 14400:
                logger.info("Refreshing target universe...")
                universe = scanner.get_target_universe(max_pairs=config.MAX_PAIRS)
                last_universe_refresh = time.time()

            signals_detected = 0
            for symbol in universe:
                try:
                    signal = scanner.evaluate_signal(symbol)
                    if signal:
                        signals_detected += 1
                        logger.info(f"SIGNAL DETECTED on {symbol}: {signal['side']} (R:R={signal['rr']:.2f}, Reward={signal['reward_pct']:.2f}%)")
                        executor.execute_signal(signal)
                except Exception as e:
                    logger.error(f"Error evaluating {symbol}: {e}")

            logger.info(f"Scan complete in {time.time() - scan_start:.2f}s. Signals triggered: {signals_detected}")

        except KeyboardInterrupt:
            logger.info("Bot shutting down gracefully via user interrupt.")
            break
        except Exception as e:
            logger.error(f"Unexpected loop exception: {e}", exc_info=True)
            notifier.notify_error(f"Daemon Loop Exception: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
