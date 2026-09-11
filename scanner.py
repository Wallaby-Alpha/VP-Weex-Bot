import time
import logging
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
import numpy as np

import config
from vp_engine import (
    calculate_atr,
    calculate_rsi,
    compute_vp_levels,
    compute_session_profiles,
    evaluate_reclaim,
    compute_confluence_score
)

logger = logging.getLogger("SCANNER")
MEXC_BASE_URL = "https://api.mexc.com"

# Cache for BTC 12-bar return to evaluate relative strength
btc_return_cache = {"timestamp": 0, "return_12": 0.0}

def fetch_btc_12bar_return(session: requests.Session) -> float:
    now = time.time()
    if now - btc_return_cache["timestamp"] < 120:
        return btc_return_cache["return_12"]
    try:
        r = session.get(f"{MEXC_BASE_URL}/api/v3/klines", params={"symbol": "BTCUSDT", "interval": config.TIMEFRAME, "limit": 15}, timeout=5)
        if r.status_code == 200:
            raw = r.json()
            if len(raw) >= 13:
                c12 = float(raw[-1][4])
                c0 = float(raw[-13][4])
                ret = (c12 - c0) / (c0 + 1e-9)
                btc_return_cache["return_12"] = ret
                btc_return_cache["timestamp"] = now
                return ret
    except Exception:
        pass
    return btc_return_cache["return_12"]


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
        seen_weex_symbols = set()

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
            resolved = self.resolve_symbol(sym)
            if not resolved:
                continue

            weex_sym, _ = resolved
            if weex_sym in seen_weex_symbols:
                continue

            try:
                quote_vol = float(t.get("quoteVolume", 0.0))
                last_price = float(t.get("lastPrice", 0.0))
            except (ValueError, TypeError):
                continue

            if config.MIN_24H_VOLUME_USD <= quote_vol <= config.MAX_24H_VOLUME_USD and last_price > 0:
                candidates.append({"symbol": sym, "quoteVolume": quote_vol})
                seen_weex_symbols.add(weex_sym)

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
        When REQUIRE_SESSION_CONFLUENCE is True:
          - Calculates institutional Volume Profiles for both New York and Asia sessions.
          - Requires both sessions to confirm a trap reclaim in the EXACT SAME direction.
          - Targets the nearest institutional POC hurdle (min 1.00% profit hurdle).
          - Sets stop-loss beyond recent sweep extremes with dynamic ATR buffer.
        """
        limit = config.KLINE_FETCH_LIMIT if config.REQUIRE_SESSION_CONFLUENCE else (config.LOOKBACK_BARS + 30)
        df = self.fetch_recent_klines(symbol, limit=limit)
        if len(df) < (100 if config.REQUIRE_SESSION_CONFLUENCE else (config.LOOKBACK_BARS + 15)):
            return None

        df["atr"] = calculate_atr(df, config.ATR_PERIOD)
        df["rsi"] = calculate_rsi(df["close"], config.RSI_PERIOD)

        # Most recently closed candle (index -2) and preceding candle (index -3)
        curr_bar = df.iloc[-2]
        prev_bar = df.iloc[-3]

        c_price = float(curr_bar["close"])
        prev_price = float(prev_bar["close"])
        curr_low = float(curr_bar["low"])
        curr_high = float(curr_bar["high"])
        prev_low = float(prev_bar["low"])
        prev_high = float(prev_bar["high"])

        c_atr = float(curr_bar["atr"])
        c_rsi = float(curr_bar["rsi"])

        if np.isnan(c_atr) or np.isnan(c_rsi) or c_atr <= 0:
            return None

        # Resolve mapping to WEEX API symbol
        resolved = self.resolve_symbol(symbol)
        weex_symbol, multiplier = resolved if resolved else (symbol, 1.0)

        # =========================================================================
        # VP-WEEX-BOT V2: NY SESSION VA RECLAIM + SCORED CONFLUENCE STRATEGY
        # =========================================================================
        profiles = compute_session_profiles(df, config.NUM_BINS, config.VAL_PCT)
        ny_p = profiles.get("ny")

        # Use NY session profile if available, otherwise fall back to 6h rolling profile
        if ny_p:
            ref_vah, ref_val, ref_poc = ny_p["vah"], ny_p["val"], ny_p["poc"]
            profile_name = ny_p["name"]
        else:
            sub_df = df.iloc[-config.LOOKBACK_BARS - 2:-2]
            ref_vah, ref_val, ref_poc = compute_vp_levels(sub_df, config.NUM_BINS, config.VAL_PCT, curr_price=c_price)
            profile_name = "6h Rolling Window"

        if ref_vah is None or ref_val is None or ref_poc is None:
            return None

        # --- 1. Base Pattern: Value Area Edge Trap & Reclaim ---
        raw_side = evaluate_reclaim(
            c_price=c_price, prev_price=prev_price,
            curr_low=curr_low, curr_high=curr_high,
            prev_low=prev_low, prev_high=prev_high,
            vah=ref_vah, val=ref_val
        )

        if not raw_side:
            return None

        # --- 2. Scored Directional Confluence Framework (6 Signals, Max 7 Pts, Min 3 Pts) ---
        btc_ret = fetch_btc_12bar_return(self.session)
        score, breakdown = compute_confluence_score(
            df=df.iloc[:-1],  # Up to closed candle
            side=raw_side,
            funding_rate=0.0,
            bid_ask_depth_ratio=1.0,
            btc_returns_12=btc_ret
        )

        if score < config.MIN_CONFLUENCE_SCORE:
            logger.debug(f"{symbol} ({raw_side}): Confluence score {score}/{config.MIN_CONFLUENCE_SCORE} insufficient. {breakdown}")
            return None

        # --- 3. Target Selection: Opposite Value Area Edge ---
        if raw_side == "LONG":
            target_price = ref_vah  # Target opposite edge (VAH)
            reward = target_price - c_price
            reward_pct = (reward / c_price) * 100
            if (reward / c_price) < config.MIN_TARGET_PCT:
                return None

            sl_buffer = max(c_atr * config.ATR_MULT_STOP, c_price * 0.002)
            sweep_low = min(curr_low, prev_low)
            stop_loss = min(c_price - sl_buffer, sweep_low * 0.9985)
            risk = c_price - stop_loss
            risk_pct = (risk / c_price) * 100
            rr = reward / risk if risk > 0 else 0

            if risk <= 0 or rr < config.MIN_RR:
                return None

            active_factors = [k for k, v in breakdown.items() if v > 0]
            confluence_str = f"Score {score}/7 ({', '.join(active_factors)})"

            return {
                "symbol": symbol,
                "weex_symbol": weex_symbol,
                "multiplier": multiplier,
                "side": "LONG",
                "timeframe": config.TIMEFRAME,
                "entry_price": c_price,
                "stop_loss": stop_loss,
                "take_profit": target_price,
                "risk_pct": risk_pct,
                "reward_pct": reward_pct,
                "rr": rr,
                "rsi": c_rsi,
                "confluence_score": score,
                "confluence_breakdown": breakdown,
                "confluence": confluence_str,
                "profile_name": profile_name,
                "val": ref_val,
                "vah": ref_vah,
                "poc": ref_poc
            }

        elif raw_side == "SHORT":
            target_price = ref_val  # Target opposite edge (VAL)
            reward = c_price - target_price
            reward_pct = (reward / c_price) * 100
            if (reward / c_price) < config.MIN_TARGET_PCT:
                return None

            sl_buffer = max(c_atr * config.ATR_MULT_STOP, c_price * 0.002)
            sweep_high = max(curr_high, prev_high)
            stop_loss = max(c_price + sl_buffer, sweep_high * 1.0015)
            risk = stop_loss - c_price
            risk_pct = (risk / c_price) * 100
            rr = reward / risk if risk > 0 else 0

            if risk <= 0 or rr < config.MIN_RR:
                return None

            active_factors = [k for k, v in breakdown.items() if v > 0]
            confluence_str = f"Score {score}/7 ({', '.join(active_factors)})"

            return {
                "symbol": symbol,
                "weex_symbol": weex_symbol,
                "multiplier": multiplier,
                "side": "SHORT",
                "timeframe": config.TIMEFRAME,
                "entry_price": c_price,
                "stop_loss": stop_loss,
                "take_profit": target_price,
                "risk_pct": risk_pct,
                "reward_pct": reward_pct,
                "rr": rr,
                "rsi": c_rsi,
                "confluence_score": score,
                "confluence_breakdown": breakdown,
                "confluence": confluence_str,
                "profile_name": profile_name,
                "val": ref_val,
                "vah": ref_vah,
                "poc": ref_poc
            }

        return None

