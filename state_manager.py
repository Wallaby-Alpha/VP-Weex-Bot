import os
import json
import time
import logging
from typing import Dict, Any, Optional

import config

logger = logging.getLogger("STATE_MANAGER")
STATE_FILE = "bot_state.json"


class StateManager:
    """
    Persists active positions, circuit-breaker freeze timestamps, and trade history
    to local disk so restarts on the droplet maintain uninterrupted state.
    """

    def __init__(self, file_path: str = STATE_FILE):
        self.file_path = file_path
        self.state: Dict[str, Any] = {
            "active_positions": {},
            "circuit_breaker": {},
            "trade_history": []
        }
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
                logger.info(f"Loaded persistent state: {len(self.state.get('active_positions', {}))} active positions.")
            except Exception as e:
                logger.error(f"Failed to load {self.file_path}, initializing clean state: {e}")

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state to {self.file_path}: {e}")

    def is_coin_frozen(self, symbol: str) -> bool:
        """
        Checks whether a coin is currently under a circuit-breaker freeze.
        """
        cb_info = self.state.get("circuit_breaker", {}).get(symbol)
        if not cb_info:
            return False

        frozen_until = cb_info.get("frozen_until", 0)
        if time.time() < frozen_until:
            remaining_mins = int((frozen_until - time.time()) / 60)
            logger.info(f"Symbol {symbol} is frozen by circuit-breaker ({remaining_mins}m remaining).")
            return True
        return False

    def record_loss(self, symbol: str) -> bool:
        """
        Increments consecutive loss counter.
        Triggers a 2-hour freeze if losses reach MAX_CONSECUTIVE_LOSSES (2).
        Returns True if circuit breaker was triggered.
        """
        if "circuit_breaker" not in self.state:
            self.state["circuit_breaker"] = {}

        cb = self.state["circuit_breaker"].get(symbol, {"consecutive_losses": 0, "frozen_until": 0})
        cb["consecutive_losses"] = cb.get("consecutive_losses", 0) + 1

        triggered = False
        if cb["consecutive_losses"] >= config.MAX_CONSECUTIVE_LOSSES:
            freeze_duration = config.CIRCUIT_BREAKER_FREEZE_BARS * 300  # 24 bars * 300s = 2 hours
            cb["frozen_until"] = time.time() + freeze_duration
            cb["consecutive_losses"] = 0
            triggered = True
            logger.warning(f"CIRCUIT BREAKER TRIGGERED for {symbol}: Frozen for {freeze_duration // 3600} hours.")

        self.state["circuit_breaker"][symbol] = cb
        self.save()
        return triggered

    def record_win(self, symbol: str):
        """
        Resets consecutive loss counter on a winning trade.
        """
        if "circuit_breaker" in self.state and symbol in self.state["circuit_breaker"]:
            self.state["circuit_breaker"][symbol]["consecutive_losses"] = 0
            self.save()

    def record_position_open(self, symbol: str, trade_data: dict):
        if "active_positions" not in self.state:
            self.state["active_positions"] = {}
        self.state["active_positions"][symbol] = trade_data
        self.save()

    def record_position_close(self, symbol: str, exit_reason: str = "CLOSED"):
        if "active_positions" in self.state and symbol in self.state["active_positions"]:
            pos = self.state["active_positions"].pop(symbol)
            pos["closed_at"] = time.time()
            pos["exit_reason"] = exit_reason
            if "trade_history" not in self.state:
                self.state["trade_history"] = []
            self.state["trade_history"].append(pos)
            self.save()

    def has_open_position(self, symbol: str, weex_symbol: Optional[str] = None) -> bool:
        active = self.state.get("active_positions", {})
        if symbol in active or (weex_symbol and weex_symbol in active):
            return True
        for pos_data in active.values():
            pos_sym = pos_data.get("symbol")
            pos_weex_sym = pos_data.get("weex_symbol")
            if symbol in (pos_sym, pos_weex_sym) or (weex_symbol and weex_symbol in (pos_sym, pos_weex_sym)):
                return True
        return False

    def get_active_positions_count(self) -> int:
        return len(self.state.get("active_positions", {}))

    def sync_with_exchange(self, weex_client, notifier=None):
        """
        Synchronizes state with actual open contract positions on WEEX.
        Detects closed positions (TP/SL hits on exchange) and frees slots.
        Also evaluates active positions for Break-Even SL adjustment and Trailing Profit Retention
        to prevent trades with high unrealized gains (e.g. +5% to +28%) from roundtripping into losses.
        """
        try:
            live_positions = weex_client.get_active_positions()
            live_map = {}
            live_symbols = set()

            for p in live_positions:
                sym = p.get("symbol", "")
                if sym:
                    live_symbols.add(sym)
                    live_map[sym] = p
                    if sym.startswith("1000"):
                        clean_sym = sym[4:]
                        live_symbols.add(clean_sym)
                        live_map[clean_sym] = p
                    else:
                        mult_sym = f"1000{sym}"
                        live_symbols.add(mult_sym)
                        live_map[mult_sym] = p

            current_local = dict(self.state.get("active_positions", {}))
            for sym, pos_data in current_local.items():
                weex_sym = pos_data.get("weex_symbol", sym)
                side = pos_data.get("side", "LONG").upper()
                entry_price = float(pos_data.get("entry_price", 0.0))
                quantity = float(pos_data.get("quantity", 0.0))

                # 1. Closed Position Detection
                if sym not in live_symbols and weex_sym not in live_symbols:
                    logger.info(f"Position {sym} ({weex_sym}) closed on WEEX (TP/SL triggered). Clearing slot.")
                    self.record_position_close(sym, exit_reason="TP/SL_TRIGGERED_ON_EXCHANGE")
                    if notifier:
                        notifier.notify_trade_closed(sym, exit_reason="TP/SL Hit on WEEX")
                    continue

                # 2. Profit Protection & Dynamic Break-Even Monitoring
                live_p = live_map.get(weex_sym, live_map.get(sym, {}))
                mark_price = float(live_p.get("markPrice", 0.0))
                if mark_price <= 0 and entry_price > 0:
                    try:
                        mark_price = weex_client.get_mark_price(weex_sym)
                    except Exception:
                        mark_price = entry_price

                if entry_price > 0 and mark_price > 0:
                    # Calculate current unrealized gain %
                    if side == "LONG":
                        gain_pct = (mark_price - entry_price) / entry_price
                    else:  # SHORT
                        gain_pct = (entry_price - mark_price) / entry_price

                    peak_gain = max(pos_data.get("peak_gain_pct", 0.0), gain_pct)
                    pos_data["peak_gain_pct"] = peak_gain

                    # --- A. Break-Even Stop Loss Guard (+1.5% Gain) ---
                    if peak_gain >= config.BREAKEVEN_TRIGGER_PCT and not pos_data.get("break_even_triggered"):
                        pos_data["break_even_triggered"] = True
                        if side == "LONG":
                            be_sl = entry_price * (1.0 + config.BREAKEVEN_BUFFER_PCT)
                        else:  # SHORT
                            be_sl = entry_price * (1.0 - config.BREAKEVEN_BUFFER_PCT)

                        pos_data["stop_loss"] = be_sl
                        pos_data["stop_loss_type"] = "BREAK_EVEN"
                        self.save()

                        # Attempt updating native position SL on WEEX API
                        weex_client.set_position_tpsl(weex_sym, side, sl_price=be_sl)

                        logger.info(f"🛡️ BREAK-EVEN ACTIVATED for {sym} ({side}): Peak gain +{peak_gain*100:.2f}%. New SL=${be_sl:.4f}")
                        if notifier:
                            notifier.notify_profit_protection(sym, side, peak_gain * 100, be_sl)

                    # --- B. Dynamic Trailing Profit Lock (+3.0%+ Gain) ---
                    if peak_gain >= config.TRAILING_PROFIT_TRIGGER_PCT:
                        retained_gain = peak_gain * config.TRAILING_PROFIT_RETENTION
                        if side == "LONG":
                            trailing_sl = entry_price * (1.0 + retained_gain)
                            curr_sl = float(pos_data.get("stop_loss", 0.0))
                            if trailing_sl > curr_sl:
                                pos_data["stop_loss"] = trailing_sl
                                pos_data["stop_loss_type"] = "TRAILING_LOCK"
                                self.save()
                                weex_client.set_position_tpsl(weex_sym, side, sl_price=trailing_sl)
                                logger.info(f"💰 TRAILING PROFIT FLOOR RAISED for {sym} ({side}): Peak +{peak_gain*100:.2f}%, Lock Floor +{retained_gain*100:.2f}% (${trailing_sl:.4f})")
                        else:  # SHORT
                            trailing_sl = entry_price * (1.0 - retained_gain)
                            curr_sl = float(pos_data.get("stop_loss", float("inf")))
                            if trailing_sl < curr_sl:
                                pos_data["stop_loss"] = trailing_sl
                                pos_data["stop_loss_type"] = "TRAILING_LOCK"
                                self.save()
                                weex_client.set_position_tpsl(weex_sym, side, sl_price=trailing_sl)
                                logger.info(f"💰 TRAILING PROFIT FLOOR LOWERED for {sym} ({side}): Peak +{peak_gain*100:.2f}%, Lock Floor +{retained_gain*100:.2f}% (${trailing_sl:.4f})")

                    # --- C. Automated Software-Side Exit Protection ---
                    stop_floor = float(pos_data.get("stop_loss", 0.0))
                    is_be = pos_data.get("break_even_triggered", False)

                    if is_be and stop_floor > 0:
                        should_close = False
                        if side == "LONG" and mark_price <= stop_floor:
                            should_close = True
                        elif side == "SHORT" and mark_price >= stop_floor:
                            should_close = True

                        if should_close:
                            sl_type = pos_data.get("stop_loss_type", "BREAK_EVEN")
                            logger.info(f"🛡️ EXECUTING PROFIT-LOCK MARKET CLOSE for {sym} ({side}) at ${mark_price:.4f} (Floor ${stop_floor:.4f}).")
                            weex_client.close_position(weex_sym, side, quantity)
                            self.record_position_close(sym, exit_reason=f"PROFIT_PROTECTION_{sl_type}")
                            if notifier:
                                notifier.notify_profit_locked(sym, side, gain_pct * 100, mark_price)

        except Exception as e:
            logger.warning(f"Could not synchronize positions with WEEX: {e}")

    def is_daily_loss_limit_reached(self, current_balance: float) -> bool:
        """
        Checks if realized + open P&L for today exceeds MAX_DAILY_LOSS_PCT (3%).
        Resets at 00:00 UTC each day.
        """
        now_utc = time.gmtime()
        today_str = f"{now_utc.tm_year}-{now_utc.tm_mon:02d}-{now_utc.tm_mday:02d}"

        if "daily_equity" not in self.state:
            self.state["daily_equity"] = {}

        daily_info = self.state["daily_equity"].get(today_str)
        if not daily_info or daily_info.get("start_balance", 0) <= 0:
            if current_balance > 0:
                self.state["daily_equity"][today_str] = {
                    "start_balance": current_balance,
                    "date": today_str
                }
                self.save()
            return False

        start_bal = float(daily_info["start_balance"])
        if start_bal <= 0:
            return False

        drawdown_pct = (start_bal - current_balance) / start_bal
        if drawdown_pct >= config.MAX_DAILY_LOSS_PCT:
            logger.warning(
                f"DAILY LOSS LIMIT REACHED: Account drawdown is {drawdown_pct*100:.2f}% "
                f"(Limit: {config.MAX_DAILY_LOSS_PCT*100:.2f}%). Halting new entries for today ({today_str})."
            )
            return True

        return False

    def is_position_correlated(self, candidate_symbol: str, candidate_df: Any) -> bool:
        """
        Calculates 30-bar price return correlation between candidate symbol and active positions.
        Returns True if correlation exceeds MAX_OPEN_CORR (0.85).
        """
        active_positions = self.state.get("active_positions", {})
        if not active_positions or candidate_df is None or len(candidate_df) < 30:
            return False

        try:
            cand_rets = candidate_df["close"].pct_change().iloc[-30:]

            for sym, pos_data in active_positions.items():
                pos_df = pos_data.get("df_recent")
                if pos_df is not None and len(pos_df) >= 30:
                    pos_rets = pos_df["close"].pct_change().iloc[-30:]
                    corr = cand_rets.corr(pos_rets)
                    if not np.isnan(corr) and abs(corr) >= config.MAX_OPEN_CORR:
                        logger.info(
                            f"Correlation Guard Triggered: {candidate_symbol} correlation with active position "
                            f"{sym} is {corr:.2f} (Limit: {config.MAX_OPEN_CORR}). Skipping."
                        )
                        return True
        except Exception as e:
            logger.debug(f"Correlation calculation skipped: {e}")

        return False
