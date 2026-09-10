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
    val_pct: float = 0.70
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculates Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL)
    using volume-weighted price binning.
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

    while accum < target_vol and (up_idx < num_bins - 1 or dn_idx > 0):
        next_up = hist[up_idx + 1] if up_idx < num_bins - 1 else -1
        next_dn = hist[dn_idx - 1] if dn_idx > 0 else -1

        if next_up >= next_dn and next_up != -1:
            up_idx += 1
            accum += next_up
        elif next_dn != -1:
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
    
    Returns:
        dict with keys 'ny' and 'asia', each containing:
            {'name': str, 'date': str, 'vah': float, 'val': float, 'poc': float}
    """
    if df.empty or len(df) < 50:
        return {}

    profiles = {}

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
        elif latest_bar_ny.time() < t_ny_close:
            # Today's NY session still developing (or not started yet), use previous completed
            target_ny = ny_days[-2] if len(ny_days) >= 2 else ny_days[-1]
        else:
            # Today's NY session has closed, use today's completed session
            target_ny = ny_days[-1]

        ny_slice = df_ny[ny_mask & (df_ny["ny_date"] == target_ny)]
        if len(ny_slice) >= 15:
            vah, val, poc = compute_vp_levels(ny_slice, num_bins, val_pct)
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
        if latest_bar_utc.time() < t_asia_close:
            # Today's Asia session still developing, use previous completed
            target_asia = asia_days[-2] if len(asia_days) >= 2 else asia_days[-1]
        else:
            # Today's Asia session has closed, use today's completed session
            target_asia = asia_days[-1]

        asia_slice = df_asia[asia_mask & (df_asia["utc_date"] == target_asia)]
        if len(asia_slice) >= 15:
            vah, val, poc = compute_vp_levels(asia_slice, num_bins, val_pct)
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
    c_rsi: float,
    rsi_long_max: float = 46.0,
    rsi_short_min: float = 54.0
) -> Optional[str]:
    """
    Evaluates whether the candle sequence confirms a Value Area trap reclaim.
    Returns 'LONG', 'SHORT', or None.
    """
    # LONG Reclaim: price dipped/swept below VAL and closed back inside Value Area
    swept_below_val = (prev_price <= val) or (curr_low <= val) or (prev_low <= val)
    if swept_below_val and (c_price > val) and (c_price < vah):
        if c_rsi <= rsi_long_max:
            return "LONG"

    # SHORT Reclaim: price pushed/swept above VAH and closed back inside Value Area
    swept_above_vah = (prev_price >= vah) or (curr_high >= vah) or (prev_high >= vah)
    if swept_above_vah and (c_price < vah) and (c_price > val):
        if c_rsi >= rsi_short_min:
            return "SHORT"

    return None

