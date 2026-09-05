import numpy as np
import pandas as pd
from typing import Tuple, Optional


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
