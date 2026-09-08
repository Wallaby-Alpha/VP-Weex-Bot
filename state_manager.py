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

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self.state.get("active_positions", {})

    def get_active_positions_count(self) -> int:
        return len(self.state.get("active_positions", {}))

    def sync_with_exchange(self, weex_client, notifier=None):
        """
        Synchronizes state with actual open contract positions on WEEX.
        When a position hits TP or SL on WEEX, it automatically closes on the exchange.
        This detects the closure, archives the trade, notifies Telegram, and frees the concurrent trade slot.
        """
        try:
            live_positions = weex_client.get_active_positions()
            live_symbols = set()
            for p in live_positions:
                sym = p.get("symbol", "")
                if sym:
                    live_symbols.add(sym)
                    # Support matching with/without 1000 prefix
                    if sym.startswith("1000"):
                        live_symbols.add(sym[4:])
                    else:
                        live_symbols.add(f"1000{sym}")

            current_local = dict(self.state.get("active_positions", {}))
            for sym, pos_data in current_local.items():
                weex_sym = pos_data.get("weex_symbol", sym)
                # If neither the base symbol nor weex_symbol exists in active positions on WEEX:
                if sym not in live_symbols and weex_sym not in live_symbols:
                    logger.info(f"Position {sym} ({weex_sym}) closed on WEEX (TP/SL triggered). Clearing slot.")
                    self.record_position_close(sym, exit_reason="TP/SL_TRIGGERED_ON_EXCHANGE")
                    if notifier:
                        notifier.notify_trade_closed(sym, exit_reason="TP/SL Hit on WEEX")

        except Exception as e:
            logger.warning(f"Could not synchronize positions with WEEX: {e}")

    def sync_dry_run_positions(self, notifier=None):
        """
        In dry-run mode, expires simulated positions that exceed MAX_HOLDING_BARS (4 hours).
        """
        current_local = dict(self.state.get("active_positions", {}))
        max_duration = config.MAX_HOLDING_BARS * 300  # 4 hours
        now = time.time()
        for sym, pos_data in current_local.items():
            opened_at = pos_data.get("timestamp", now)
            if now - opened_at > max_duration:
                logger.info(f"[DRY-RUN] Holding duration exceeded for {sym}. Closing simulated position.")
                self.record_position_close(sym, exit_reason="MAX_HOLDING_EXPIRED")
                if notifier:
                    notifier.notify_trade_closed(sym, exit_reason="Max Holding Expired (Dry-Run)")
