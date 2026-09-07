import time
import logging
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
import numpy as np

import config
from vp_engine import calculate_atr, calculate_rsi, compute_vp_levels

logger = logging.getLogger("SCANNER")
MEXC_BASE_URL = "https://api.mexc.com"


class MarketScanner:
    """
    Screens the altcoin universe and evaluates live 5-minute candle closes for
    high-conviction Volume Profile mean-reversion setups.
    """

    def __init__(self):
        self.session = requests.Session()
        self.weex_api_symbols = set()
        self.load_weex_api_symbols()

    def load_weex_api_symbols(self):
        """
        Fetches the official list of supported WEEX contract API trading symbols.
        """
        try:
            url = f"{config.WEEX_BASE_URL}/capi/v3/market/apiTradingSymbols"
            r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                symbols = data if isinstance(data, list) else data.get("data", [])
                self.weex_api_symbols = set(symbols)
                logger.info(f"Loaded {len(self.weex_api_symbols)} verified WEEX API trading symbols.")
        except Exception as e:
            logger.warning(f"Could not load WEEX apiTradingSymbols: {e}")

    def resolve_symbol(self, mexc_sym: str) -> Optional[tuple]:
        """
        Resolves a MEXC symbol (e.g., BONKUSDT, BTCUSDT) to a supported WEEX contract symbol.
        Handles multiplier-prefixed meme contracts (e.g. BONKUSDT -> 1000BONKUSDT).
        Returns (weex_symbol, multiplier) or None if unlisted on WEEX API.
        """
        if not self.weex_api_symbols:
            # Fallback if endpoint unreachable
            return mexc_sym, 1.0

        if mexc_sym in self.weex_api_symbols:
            return mexc_sym, 1.0

        clean = mexc_sym[:-4] if mexc_sym.endswith("USDT") else mexc_sym
        for prefix, mult in [("1000", 1000.0), ("10000", 10000.0), ("1000000", 1000000.0)]:
            cand = f"{prefix}{clean}USDT"
            if cand in self.weex_api_symbols:
                return cand, mult

        return None

    def get_target_universe(self, max_pairs: int = config.MAX_PAIRS) -> List[str]:
        """
        Screens for liquid, pure-crypto altcoins that are actively supported on WEEX API,
        while blacklisting non-ASCII meme tokens and BTC-slaved dinosaur assets.
        """
        self.load_weex_api_symbols()

        try:
            r = self.session.get(f"{MEXC_BASE_URL}/api/v3/ticker/24hr", timeout=12)
            tickers = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.error(f"Failed to fetch 24hr tickers: {e}")
            return []

        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue

            # Automatically purge non-ASCII symbols (e.g. 龙虾USDT)
            if not sym.isascii():
                continue

            base = sym[:-4]
            if base in config.FIAT_BASES or base in config.STABLECOIN_BASES:
                continue

            if any(sub in sym for sub in config.EXCLUDED_SUBSTRINGS):
                continue

            # Ensure symbol is tradable via WEEX API (or maps to 1000X)
            if not self.resolve_symbol(sym):
                continue

            try:
                quote_vol = float(t.get("quoteVolume", 0.0))
                last_price = float(t.get("lastPrice", 0.0))
            except (ValueError, TypeError):
                continue

            if config.MIN_24H_VOLUME_USD <= quote_vol <= config.MAX_24H_VOLUME_USD and last_price > 0:
                candidates.append({"symbol": sym, "quoteVolume": quote_vol})

        candidates.sort(key=lambda x: x["quoteVolume"], reverse=True)
        selected = [c["symbol"] for c in candidates[:max_pairs]]
        logger.info(f"Target Universe selected ({len(selected)} pairs supported on WEEX API): {selected}")
        return selected

    def fetch_recent_klines(self, symbol: str, limit: int = 120) -> pd.DataFrame:
        """
        Fetches the most recent 5-minute bars to compute indicators and Volume Profile.
        """
        params = {
            "symbol": symbol,
            "interval": config.TIMEFRAME,
            "limit": limit
        }
        try:
            r = self.session.get(f"{MEXC_BASE_URL}/api/v3/klines", params=params, timeout=10)
            if r.status_code != 200:
                return pd.DataFrame()
            raw = r.json()
        except Exception as e:
            logger.warning(f"Error fetching klines for {symbol}: {e}")
            return pd.DataFrame()

        if not raw:
            return pd.DataFrame()

        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume"
        ])

        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    def evaluate_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Evaluates the latest completed 5-minute candle against the strategy logic:
        1. Confirmed trap re-entry above VAL (Long) or below VAH (Short)
        2. RSI exhaustion check
        3. Minimum 1.00% target move to POC
        4. Risk / Reward >= 1.40
        """
        df = self.fetch_recent_klines(symbol, limit=config.LOOKBACK_BARS + 30)
        if len(df) < config.LOOKBACK_BARS + 15:
            return None

        df["atr"] = calculate_atr(df, config.ATR_PERIOD)
        df["rsi"] = calculate_rsi(df["close"], config.RSI_PERIOD)

        # Look at the most recently CLOSED candle (index -2) and previous (index -3)
        # (index -1 is the currently open/unclosed bar)
        curr_bar = df.iloc[-2]
        prev_bar = df.iloc[-3]

        c_price = float(curr_bar["close"])
        prev_price = float(prev_bar["close"])
        c_atr = float(curr_bar["atr"])
        c_rsi = float(curr_bar["rsi"])

        if np.isnan(c_atr) or np.isnan(c_rsi) or c_atr <= 0:
            return None

        # Slice rolling lookback window up to current closed candle
        sub_df = df.iloc[-config.LOOKBACK_BARS - 2:-2]
        vah, val, poc = compute_vp_levels(sub_df, config.NUM_BINS, config.VAL_PCT)

        if vah is None or val is None or poc is None:
            return None

        # Resolve mapping to WEEX API symbol
        resolved = self.resolve_symbol(symbol)
        weex_symbol, multiplier = resolved if resolved else (symbol, 1.0)

        # --- LONG TRIGGER ---
        if prev_price <= val and c_price > val and c_rsi <= config.RSI_LONG_MAX:
            reward = poc - c_price
            reward_pct = (reward / c_price) * 100

            if (reward / c_price) >= config.MIN_TARGET_PCT:
                sl_buffer = max(c_atr * config.ATR_MULT_STOP, c_price * 0.002)
                stop_loss = c_price - sl_buffer
                risk = c_price - stop_loss
                risk_pct = (risk / c_price) * 100
                rr = reward / risk if risk > 0 else 0

                if risk > 0 and rr >= config.MIN_RR:
                    return {
                        "symbol": symbol,
                        "weex_symbol": weex_symbol,
                        "multiplier": multiplier,
                        "side": "LONG",
                        "timeframe": config.TIMEFRAME,
                        "entry_price": c_price,
                        "stop_loss": stop_loss,
                        "take_profit": poc,
                        "risk_pct": risk_pct,
                        "reward_pct": reward_pct,
                        "rr": rr,
                        "rsi": c_rsi,
                        "val": val,
                        "vah": vah,
                        "poc": poc
                    }

        # --- SHORT TRIGGER ---
        elif prev_price >= vah and c_price < vah and c_rsi >= config.RSI_SHORT_MIN:
            reward = c_price - poc
            reward_pct = (reward / c_price) * 100

            if (reward / c_price) >= config.MIN_TARGET_PCT:
                sl_buffer = max(c_atr * config.ATR_MULT_STOP, c_price * 0.002)
                stop_loss = c_price + sl_buffer
                risk = stop_loss - c_price
                risk_pct = (risk / c_price) * 100
                rr = reward / risk if risk > 0 else 0

                if risk > 0 and rr >= config.MIN_RR:
                    return {
                        "symbol": symbol,
                        "weex_symbol": weex_symbol,
                        "multiplier": multiplier,
                        "side": "SHORT",
                        "timeframe": config.TIMEFRAME,
                        "entry_price": c_price,
                        "stop_loss": stop_loss,
                        "take_profit": poc,
                        "risk_pct": risk_pct,
                        "reward_pct": reward_pct,
                        "rr": rr,
                        "rsi": c_rsi,
                        "val": val,
                        "vah": vah,
                        "poc": poc
                    }

        return None
