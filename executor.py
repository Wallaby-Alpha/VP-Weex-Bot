import time
import math
import logging
from typing import Dict, Any, Optional

import config
from weex_client import WeexClient
from telegram_notifier import TelegramNotifier
from state_manager import StateManager

logger = logging.getLogger("EXECUTOR")


class TradeExecutor:
    """
    Manages risk sizing, contract precision formatting, WEEX order placement,
    and Telegram notification dispatch.
    """

    def __init__(
        self,
        weex_client: WeexClient,
        notifier: TelegramNotifier,
        state_mgr: StateManager
    ):
        self.weex = weex_client
        self.notifier = notifier
        self.state_mgr = state_mgr
        self.symbol_metadata = {}
        self.load_metadata()

    def load_metadata(self):
        try:
            self.symbol_metadata = self.weex.get_exchange_info()
            logger.info(f"Loaded precision specifications for {len(self.symbol_metadata)} symbols.")
        except Exception as e:
            logger.warning(f"Could not load WEEX exchangeInfo: {e}")

    def execute_signal(self, signal: Dict[str, Any]) -> bool:
        symbol = signal["symbol"]
        side = signal["side"]

        # 1. Circuit breaker guard
        if self.state_mgr.is_coin_frozen(symbol):
            logger.info(f"Skipping {symbol}: Currently frozen by circuit breaker.")
            return False

        # 2. Duplicate position guard
        if self.state_mgr.has_open_position(symbol):
            logger.info(f"Skipping {symbol}: Already holding an open position.")
            return False

        # 3. Retrieve symbol precision specs
        meta = self.symbol_metadata.get(symbol, {
            "pricePrecision": 4,
            "quantityPrecision": 2,
            "minOrderSize": 0.001
        })
        price_prec = meta["pricePrecision"]
        qty_prec = meta["quantityPrecision"]
        min_qty = meta["minOrderSize"]

        # 4. Position Sizing
        entry_price = signal["entry_price"]
        available_balance = self.weex.get_available_margin()
        if available_balance <= 0:
            available_balance = 1000.0  # Safe simulation baseline for dry-run

        # Capital allocation = 10% of portfolio * leverage
        allocated_capital = available_balance * config.POSITION_SIZE_PCT
        notional = allocated_capital * config.DEFAULT_LEVERAGE
        raw_qty = notional / entry_price

        # Round down to exchange quantity precision
        factor = 10 ** qty_prec
        quantity = math.floor(raw_qty * factor) / factor
        if quantity < min_qty:
            quantity = min_qty

        # Format TP & SL to price precision
        tp_price = round(signal["take_profit"], price_prec)
        sl_price = round(signal["stop_loss"], price_prec)

        trade_record = {
            "symbol": symbol,
            "side": side,
            "timeframe": config.TIMEFRAME,
            "entry_price": entry_price,
            "take_profit": tp_price,
            "stop_loss": sl_price,
            "quantity": quantity,
            "risk_pct": signal["risk_pct"],
            "reward_pct": signal["reward_pct"],
            "rr": signal["rr"],
            "rsi": signal["rsi"],
            "timestamp": time.time(),
            "dry_run": config.DRY_RUN
        }

        # 5. Execution
        if config.DRY_RUN:
            logger.info(f"[DRY-RUN] Simulating {side} order on {symbol}: Qty={quantity}, TP={tp_price}, SL={sl_price}")
            self.state_mgr.record_position_open(symbol, trade_record)
            self.notifier.notify_trade_signal(trade_record)
            return True

        # LIVE EXECUTION ON WEEX
        try:
            # Configure leverage
            self.weex.set_leverage(symbol, config.DEFAULT_LEVERAGE)

            # WEEX order side: BUY for LONG, SELL for SHORT
            order_side = "BUY" if side == "LONG" else "SELL"
            pos_side = "LONG" if side == "LONG" else "SHORT"

            res = self.weex.place_order_with_tpsl(
                symbol=symbol,
                side=order_side,
                position_side=pos_side,
                quantity=quantity,
                tp_price=tp_price,
                sl_price=sl_price
            )

            if res.get("success"):
                trade_record["order_id"] = res.get("orderId")
                logger.info(f"LIVE WEEX ORDER PLACED for {symbol}: OrderId={res.get('orderId')}")
                self.state_mgr.record_position_open(symbol, trade_record)
                self.notifier.notify_trade_signal(trade_record)
                return True
            else:
                err_msg = f"WEEX order submission failed for {symbol}: {res.get('raw')}"
                logger.error(err_msg)
                self.notifier.notify_error(err_msg)
                return False

        except Exception as e:
            err_msg = f"Fatal execution exception on {symbol}: {e}"
            logger.error(err_msg)
            self.notifier.notify_error(err_msg)
            return False
