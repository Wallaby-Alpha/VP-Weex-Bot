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
        self.api_trading_symbols = set()
        self.load_metadata()

    def load_metadata(self):
        try:
            self.symbol_metadata = self.weex.get_exchange_info()
            logger.info(f"Loaded precision specifications for {len(self.symbol_metadata)} symbols.")
        except Exception as e:
            logger.warning(f"Could not load WEEX exchangeInfo: {e}")

        try:
            self.api_trading_symbols = self.weex.get_api_trading_symbols()
            logger.info(f"Loaded {len(self.api_trading_symbols)} API-permitted trading symbols.")
        except Exception as e:
            logger.warning(f"Could not load WEEX apiTradingSymbols: {e}")

    def execute_signal(self, signal: Dict[str, Any]) -> bool:
        symbol = signal["symbol"]
        weex_symbol = signal.get("weex_symbol", symbol)
        multiplier = float(signal.get("multiplier", 1.0))
        side = signal["side"]

        # 0. API Trading Permission guard
        if self.api_trading_symbols and weex_symbol not in self.api_trading_symbols:
            logger.warning(f"Skipping {symbol} ({weex_symbol}): Token is listed on WEEX web/app but disabled for API trading by WEEX.")
            return False

        # 1. Concurrent positions limit guard
        active_count = self.state_mgr.get_active_positions_count()
        if active_count >= config.MAX_CONCURRENT_TRADES:
            logger.info(f"Skipping {symbol} ({weex_symbol}): Max concurrent positions limit ({config.MAX_CONCURRENT_TRADES}) reached ({active_count} active).")
            return False

        # 2. Circuit breaker guard
        if self.state_mgr.is_coin_frozen(symbol) or self.state_mgr.is_coin_frozen(weex_symbol):
            logger.info(f"Skipping {symbol} ({weex_symbol}): Currently frozen by circuit breaker.")
            return False

        # 3. Duplicate position guard
        if self.state_mgr.has_open_position(symbol, weex_symbol):
            logger.info(f"Skipping {symbol} ({weex_symbol}): Already holding an open position.")
            return False

        # 3. Retrieve symbol precision specs using weex_symbol
        meta = self.symbol_metadata.get(weex_symbol, self.symbol_metadata.get(symbol, {
            "pricePrecision": 4,
            "quantityPrecision": 2,
            "minOrderSize": 0.001
        }))
        price_prec = int(meta.get("pricePrecision", 4))
        qty_prec = int(meta.get("quantityPrecision", 2))
        min_qty = float(meta.get("minOrderSize", 0.001))

        # 4. Position Sizing & Price Scaling
        mexc_entry = float(signal["entry_price"])
        try:
            weex_mark = self.weex.get_mark_price(weex_symbol)
        except Exception:
            weex_mark = None

        if weex_mark and weex_mark > 0 and mexc_entry > 0:
            scale_ratio = weex_mark / mexc_entry
            entry_price = weex_mark
        else:
            scale_ratio = multiplier
            entry_price = mexc_entry * multiplier

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
        if qty_prec == 0 or quantity.is_integer():
            quantity = int(quantity)

        # Format TP & SL to price precision using the scale ratio
        tp_raw = signal.get("take_profit")
        sl_raw = signal.get("stop_loss")
        tp_price = round(float(tp_raw) * scale_ratio, price_prec) if tp_raw is not None else None
        sl_price = round(float(sl_raw) * scale_ratio, price_prec) if sl_raw is not None else None
        if price_prec == 0:
            if tp_price is not None:
                tp_price = int(tp_price)
            if sl_price is not None:
                sl_price = int(sl_price)

        trade_record = {
            "symbol": symbol,
            "weex_symbol": weex_symbol,
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
            logger.info(f"[DRY-RUN] Simulating {side} order on {weex_symbol}: Qty={quantity}, TP={tp_price}, SL={sl_price}")
            self.state_mgr.record_position_open(symbol, trade_record)
            self.notifier.notify_trade_signal(trade_record)
            return True

        # LIVE EXECUTION ON WEEX
        try:
            # Configure leverage on WEEX using weex_symbol
            self.weex.set_leverage(weex_symbol, config.DEFAULT_LEVERAGE)

            # WEEX order side: BUY for LONG, SELL for SHORT
            order_side = "BUY" if side == "LONG" else "SELL"
            pos_side = "LONG" if side == "LONG" else "SHORT"

            res = self.weex.place_order_with_tpsl(
                symbol=weex_symbol,
                side=order_side,
                position_side=pos_side,
                quantity=quantity,
                tp_price=tp_price,
                sl_price=sl_price
            )

            if res.get("success"):
                trade_record["order_id"] = res.get("orderId")
                logger.info(f"LIVE WEEX ORDER PLACED for {weex_symbol}: OrderId={res.get('orderId')}")
                self.state_mgr.record_position_open(symbol, trade_record)
                self.notifier.notify_trade_signal(trade_record)
                return True
            else:
                raw = res.get("raw", {})
                code = raw.get("code") if isinstance(raw, dict) else None
                if code in (-1058, "-1058"):
                    logger.warning(f"WEEX order skipped: {weex_symbol} is not supported for API trading on WEEX.")
                else:
                    err_msg = f"WEEX order submission failed for {weex_symbol}: {raw}"
                    logger.error(err_msg)
                    self.notifier.notify_error(err_msg)
                return False

        except Exception as e:
            err_msg = f"Fatal execution exception on {weex_symbol}: {e}"
            logger.error(err_msg, exc_info=True)
            self.notifier.notify_error(err_msg)
            return False
