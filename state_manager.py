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
        Increments consecutive loss counter and freezes the symbol for POST_LOSS_COOLDOWN_HOURS
        to prevent immediate repeat knife-catching entries.
        """
        if "circuit_breaker" not in self.state:
            self.state["circuit_breaker"] = {}

        cb = self.state["circuit_breaker"].get(symbol, {"consecutive_losses": 0, "frozen_until": 0})
        cb["consecutive_losses"] = cb.get("consecutive_losses", 0) + 1

        cooldown_secs = int(config.POST_LOSS_COOLDOWN_HOURS * 3600)
        curr_frozen = cb.get("frozen_until", 0)
        new_frozen = max(curr_frozen, time.time() + cooldown_secs)
        cb["frozen_until"] = new_frozen

        triggered = False
        if cb["consecutive_losses"] >= config.MAX_CONSECUTIVE_LOSSES:
            freeze_duration = config.CIRCUIT_BREAKER_FREEZE_BARS * 300  # 24 bars * 300s = 2 hours
            cb["frozen_until"] = max(new_frozen, time.time() + freeze_duration)
            cb["consecutive_losses"] = 0
            triggered = True
            logger.warning(f"CIRCUIT BREAKER TRIGGERED for {symbol}: Frozen for {freeze_duration // 3600} hours.")
        else:
            logger.info(f"Post-loss cooldown applied to {symbol}: Frozen for {config.POST_LOSS_COOLDOWN_HOURS} hours.")

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

    def monitor_and_update_positions(self, weex_client, notifier=None):
        """
        1. Checks whether active positions were closed on WEEX and frees their slot.
        2. Monitors mark prices to raise Stop Loss to Break-Even at +0.90% gain or POC hit.
        3. Dynamically trails Stop Loss when peak profit reaches +2.5%+.
        4. Enforces software-side market close if mark price triggers the local floor.
        """
        if not self.state.get("active_positions"):
            return

        try:
            live_positions = weex_client.get_positions()
            live_symbols = set()
            live_map = {}
            if isinstance(live_positions, list):
                for p in live_positions:
                    hold_qty = float(p.get("holdAmount", 0.0))
                    total_qty = float(p.get("total", 0.0))
                    if hold_qty > 0 or total_qty > 0:
                        sym_name = p.get("symbol", "")
                        live_symbols.add(sym_name)
                        live_map[sym_name] = p
                        clean_sym = sym_name.replace("_A", "")
                        live_symbols.add(clean_sym)
                        live_map[clean_sym] = p
                        mult_sym = clean_sym.replace("1000", "")
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
                    target_tp = float(pos_data.get("take_profit", 0.0))
                    peak_gain = float(pos_data.get("peak_gain_pct", 0.0))
                    reward_pct = float(pos_data.get("reward_pct", 0.0)) / 100.0
                    was_be = pos_data.get("break_even_triggered", False)

                    # If peak gain never reached near TP and Break-Even was not active, treat as Stop Loss
                    is_loss = not was_be and (peak_gain < (reward_pct * 0.80 if reward_pct > 0 else 0.015))
                    if is_loss:
                        self.record_loss(sym)
                        exit_status = "Stop Loss Hit on WEEX"
                    else:
                        self.record_win(sym)
                        exit_status = "Take Profit / Profit Protection Hit on WEEX"

                    logger.info(f"Position {sym} ({weex_sym}) closed on WEEX ({exit_status}). Clearing slot.")
                    self.record_position_close(sym, exit_reason=exit_status)
                    if notifier:
                        notifier.notify_trade_closed(sym, exit_reason=exit_status)
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

                    # Point of Control (POC) check
                    poc_price = float(pos_data.get("poc_price", 0.0))
                    poc_hit = False
                    if poc_price > 0:
                        if side == "LONG" and mark_price >= poc_price:
                            poc_hit = True
                        elif side == "SHORT" and mark_price <= poc_price:
                            poc_hit = True

                    # --- A. Break-Even Stop Loss Guard (+0.90% Gain or POC Hit) ---
                    if (peak_gain >= config.BREAKEVEN_TRIGGER_PCT or poc_hit) and not pos_data.get("break_even_triggered"):
                        pos_data["break_even_triggered"] = True
                        trigger_reason = "Session POC Reached" if poc_hit else f"Peak gain +{peak_gain*100:.2f}%"
                        if side == "LONG":
                            be_sl = entry_price * (1.0 + config.BREAKEVEN_BUFFER_PCT)
                        else:  # SHORT
                            be_sl = entry_price * (1.0 - config.BREAKEVEN_BUFFER_PCT)

                        pos_data["stop_loss"] = be_sl
                        pos_data["stop_loss_type"] = "BREAK_EVEN"
                        self.save()

                        # Attempt updating native position SL on WEEX API
                        weex_client.set_position_tpsl(weex_sym, side, sl_price=be_sl)

                        logger.info(f"🛡️ BREAK-EVEN ACTIVATED for {sym} ({side}): {trigger_reason}. New SL=${be_sl:.4f}")
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

    # Backward-compatible alias used by main daemon loop
    sync_with_exchange = monitor_and_update_positions

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
