import numpy as np
import pandas as pd
from typing import Tuple, Optional, Dict, Any


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculates True Range and Average True Range (ATR).
    """
    h = df["high"]
    l = df["low"]
    c = df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculates Relative Strength Index (RSI).
    """
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def compute_vp_levels(
    sub_df: pd.DataFrame,
    num_bins: int = 30,
    val_pct: float = 0.70,
    curr_price: Optional[float] = None
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculates Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL)
    using volume-weighted price binning with current-price tie-breaking.
    """
    prices = sub_df["close"].values
    volumes = sub_df["volume"].values

    if len(prices) == 0 or np.sum(volumes) == 0:
        return None, None, None

    min_p, max_p = prices.min(), prices.max()
    if min_p == max_p:
        return min_p, min_p, min_p

    bins = np.linspace(min_p, max_p, num_bins + 1)
    bin_indices = np.digitize(prices, bins) - 1
    bin_indices = np.clip(bin_indices, 0, num_bins - 1)

    hist = np.zeros(num_bins)
    np.add.at(hist, bin_indices, volumes)

    poc_idx = int(np.argmax(hist))
    poc_price = float((bins[poc_idx] + bins[poc_idx + 1]) / 2.0)

    total_vol = hist.sum()
    target_vol = total_vol * val_pct

    accum = hist[poc_idx]
    up_idx, dn_idx = poc_idx, poc_idx

    c_price_ref = curr_price if curr_price is not None else poc_price

    while accum < target_vol and (up_idx < num_bins - 1 or dn_idx > 0):
        next_up = hist[up_idx + 1] if up_idx < num_bins - 1 else -1
        next_dn = hist[dn_idx - 1] if dn_idx > 0 else -1

        if next_up > next_dn and next_up != -1:
            up_idx += 1
            accum += next_up
        elif next_dn > next_up and next_dn != -1:
            dn_idx -= 1
            accum += next_dn
        elif next_up == next_dn and next_up != -1:
            # Tie-break rule: expand toward the bin closer to current price
            up_mid = (bins[up_idx + 1] + bins[up_idx + 2]) / 2.0 if up_idx < num_bins - 1 else float("inf")
            dn_mid = (bins[dn_idx - 1] + bins[dn_idx]) / 2.0 if dn_idx > 0 else float("inf")
            
            if abs(up_mid - c_price_ref) <= abs(dn_mid - c_price_ref):
                up_idx += 1
                accum += next_up
            else:
                dn_idx -= 1
                accum += next_dn
        else:
            break

    vah_price = float(bins[up_idx + 1])
    val_price = float(bins[dn_idx])

    return vah_price, val_price, poc_price


def compute_session_profiles(
    df: pd.DataFrame,
    num_bins: int = 30,
    val_pct: float = 0.70
) -> Dict[str, Any]:
    """
    Extracts the most recently completed New York and Asia sessions from the 5m klines
    dataframe and calculates the Volume Profile levels (VAH, VAL, POC) for each.
    """
    if df.empty or len(df) < 50:
        return {}

    profiles = {}
    c_price_ref = float(df["close"].iloc[-1])

    # Ensure timestamp column is datetime with UTC tz
    if "timestamp" not in df.columns:
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    # --- 1. NY SESSION (09:30 - 16:00 US/Eastern, Mon-Fri) ---
    df_ny = df.copy()
    df_ny["ny_time"] = df_ny["timestamp"].dt.tz_convert("America/New_York")
    df_ny["ny_date"] = df_ny["ny_time"].dt.date
    df_ny["ny_dow"] = df_ny["ny_time"].dt.dayofweek

    t_ny_open = pd.to_datetime("09:30:00").time()
    t_ny_close = pd.to_datetime("16:00:00").time()

    ny_mask = (df_ny["ny_dow"] < 5) & (df_ny["ny_time"].dt.time >= t_ny_open) & (df_ny["ny_time"].dt.time < t_ny_close)
    ny_days = sorted(df_ny[ny_mask]["ny_date"].unique())

    if ny_days:
        latest_bar_ny = df_ny["ny_time"].iloc[-1]
        if latest_bar_ny.dayofweek >= 5:  # Weekend: locks to Friday's completed NY
            target_ny = ny_days[-1]
        elif ny_days[-1] == latest_bar_ny.date() and latest_bar_ny.time() < t_ny_close:
            target_ny = ny_days[-2] if len(ny_days) >= 2 else ny_days[-1]
        else:
            target_ny = ny_days[-1]

        ny_slice = df_ny[ny_mask & (df_ny["ny_date"] == target_ny)]
        if len(ny_slice) >= 15:
            vah, val, poc = compute_vp_levels(ny_slice, num_bins, val_pct, curr_price=c_price_ref)
            if vah is not None and val is not None and poc is not None:
                profiles["ny"] = {
                    "name": f"NY Session ({target_ny})",
                    "date": str(target_ny),
                    "vah": vah,
                    "val": val,
                    "poc": poc
                }

    # --- 2. ASIA SESSION (00:00 - 08:00 UTC, 7 days/week) ---
    t_asia_open = pd.to_datetime("00:00:00").time()
    t_asia_close = pd.to_datetime("08:00:00").time()
    asia_mask = (df["timestamp"].dt.time >= t_asia_open) & (df["timestamp"].dt.time < t_asia_close)

    df_asia = df.copy()
    df_asia["utc_date"] = df_asia["timestamp"].dt.date
    asia_days = sorted(df_asia[asia_mask]["utc_date"].unique())

    if asia_days:
        latest_bar_utc = df["timestamp"].iloc[-1]
        if asia_days[-1] == latest_bar_utc.date() and latest_bar_utc.time() < t_asia_close:
            target_asia = asia_days[-2] if len(asia_days) >= 2 else asia_days[-1]
        else:
            target_asia = asia_days[-1]

        asia_slice = df_asia[asia_mask & (df_asia["utc_date"] == target_asia)]
        if len(asia_slice) >= 15:
            vah, val, poc = compute_vp_levels(asia_slice, num_bins, val_pct, curr_price=c_price_ref)
            if vah is not None and val is not None and poc is not None:
                profiles["asia"] = {
                    "name": f"Asia Session ({target_asia})",
                    "date": str(target_asia),
                    "vah": vah,
                    "val": val,
                    "poc": poc
                }

    return profiles


def evaluate_reclaim(
    c_price: float,
    prev_price: float,
    curr_low: float,
    curr_high: float,
    prev_low: float,
    prev_high: float,
    vah: float,
    val: float,
    c_rsi: float = 50.0,
    rsi_long_max: float = 100.0,
    rsi_short_min: float = 0.0
) -> Optional[str]:
    """
    Evaluates whether the candle sequence confirms a Value Area trap reclaim.
    Returns 'LONG', 'SHORT', or None.
    """
    # LONG Reclaim: price dipped/swept below VAL and closed back inside Value Area
    swept_below_val = (prev_price <= val) or (curr_low <= val) or (prev_low <= val)
    if swept_below_val and (c_price > val) and (c_price < vah):
        return "LONG"

    # SHORT Reclaim: price pushed/swept above VAH and closed back inside Value Area
    swept_above_vah = (prev_price >= vah) or (curr_high >= vah) or (prev_high >= vah)
    if swept_above_vah and (c_price < vah) and (c_price > val):
        return "SHORT"

    return None


def compute_confluence_score(
    df: pd.DataFrame,
    side: str,                  # "LONG" or "SHORT"
    funding_rate: float = 0.0,
    bid_ask_depth_ratio: float = 1.0,
    btc_returns_12: Optional[float] = None
) -> Tuple[int, Dict[str, int]]:
    """
    Evaluates 6 directional confluence signals and returns (total_score, breakdown_dict).
    
    1. CVD Absorption (2 pts): Volume delta divergence during sweep bar.
    2. Reclaim Bar Volume (1 pt): Volume on reclaim bar >= 1.5x 20-bar avg volume.
    3. HTF Trend Alignment (1 pt): 1h EMA20 > EMA50 for LONG, opposite for SHORT.
    4. Relative Strength vs BTC (1 pt): Coin's 12-bar return vs BTC 12-bar return.
    5. Funding Rate Skew (1 pt): Funding <= 0 for LONG, Funding >= 0 for SHORT.
    6. Order Book Depth Imbalance (1 pt): Bid depth > Ask depth for LONG, opposite for SHORT.
    """
    score = 0
    breakdown = {}

    curr_bar = df.iloc[-1]
    prev_bar = df.iloc[-2]

    # --- 1. CVD Absorption (2 pts) ---
    # Reclaim bar (curr) should show delta divergence from the sweep direction.
    # The sweep bar (prev) made the extreme; the reclaim bar absorbs and reverses.
    curr_delta = (curr_bar["close"] - curr_bar["open"]) / (curr_bar["high"] - curr_bar["low"] + 1e-9) * curr_bar["volume"]
    prev_delta = (prev_bar["close"] - prev_bar["open"]) / (prev_bar["high"] - prev_bar["low"] + 1e-9) * prev_bar["volume"]

    if side == "LONG":
        # Sweep bar went down (negative delta or made low), reclaim bar shows buying (positive delta)
        if curr_delta > 0 or (curr_delta + prev_delta) > 0:
            score += 2
            breakdown["CVD Absorption"] = 2
        else:
            breakdown["CVD Absorption"] = 0
    else:  # SHORT
        # Sweep bar went up (positive delta or made high), reclaim bar shows selling (negative delta)
        if curr_delta < 0 or (curr_delta + prev_delta) < 0:
            score += 2
            breakdown["CVD Absorption"] = 2
        else:
            breakdown["CVD Absorption"] = 0

    # --- 2. Reclaim Candle Volume (1 pt) ---
    vol_20_avg = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else df["volume"].mean()
    if curr_bar["volume"] >= 1.5 * vol_20_avg:
        score += 1
        breakdown["Reclaim Volume"] = 1
    else:
        breakdown["Reclaim Volume"] = 0

    # --- 3. HTF Trend Alignment (1 pt) ---
    ema20 = df["close"].ewm(span=240, adjust=False).mean().iloc[-1]
    ema50 = df["close"].ewm(span=600, adjust=False).mean().iloc[-1]
    c_price = curr_bar["close"]

    if side == "LONG" and (ema20 > ema50 or c_price > ema50):
        score += 1
        breakdown["HTF Trend"] = 1
    elif side == "SHORT" and (ema20 < ema50 or c_price < ema50):
        score += 1
        breakdown["HTF Trend"] = 1
    else:
        breakdown["HTF Trend"] = 0

    # --- 4. Relative Strength vs BTC (1 pt) ---
    if len(df) >= 13:
        coin_ret = (df["close"].iloc[-1] - df["close"].iloc[-13]) / (df["close"].iloc[-13] + 1e-9)
        if btc_returns_12 is not None:
            if side == "LONG" and coin_ret > btc_returns_12:
                score += 1
                breakdown["BTC Rel Strength"] = 1
            elif side == "SHORT" and coin_ret < btc_returns_12:
                score += 1
                breakdown["BTC Rel Strength"] = 1
            else:
                breakdown["BTC Rel Strength"] = 0
        else:
            if (side == "LONG" and coin_ret > 0) or (side == "SHORT" and coin_ret < 0):
                score += 1
                breakdown["BTC Rel Strength"] = 1
            else:
                breakdown["BTC Rel Strength"] = 0
    else:
        breakdown["BTC Rel Strength"] = 0

    # --- 5. Funding Rate Skew (1 pt) ---
    if side == "LONG" and funding_rate <= 0:
        score += 1
        breakdown["Funding Skew"] = 1
    elif side == "SHORT" and funding_rate >= 0:
        score += 1
        breakdown["Funding Skew"] = 1
    else:
        breakdown["Funding Skew"] = 0

    # --- 6. Order Book Depth Imbalance (1 pt) ---
    if side == "LONG" and bid_ask_depth_ratio > 1.05:
        score += 1
        breakdown["Order Book Imbalance"] = 1
    elif side == "SHORT" and bid_ask_depth_ratio < 0.95:
        score += 1
        breakdown["Order Book Imbalance"] = 1
    else:
        breakdown["Order Book Imbalance"] = 0

    return score, breakdown


