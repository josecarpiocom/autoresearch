"""
Trading strategy — the agent optimizes this file.

Implements a simple signal-based strategy on BTC 5m candles.
The evaluate() function is called by prepare.py with train/test DataFrames.
It must return a Series of positions: 1 (long), 0 (flat), -1 (short).

Rules:
  - You receive OHLCV columns: open, high, low, close, volume
  - Return a position Series aligned with the input index
  - Positions are applied at the NEXT bar's open (no look-ahead)
  - Keep it simple: indicators derived from past data only
"""

import pandas as pd


def evaluate(df: pd.DataFrame) -> pd.Series:
    """Generate position signals from OHLCV data.

    Args:
        df: DataFrame with columns [open, high, low, close, volume]

    Returns:
        Series of positions: 1 (long), 0 (flat), -1 (short)
    """
    close = df["close"]

    # Slower crossover with hysteresis to reduce noise-driven flips and turnover.
    fast = close.rolling(window=96).mean()    # 8 hours  (96 x 5min)
    slow = close.rolling(window=288).mean()   # 24 hours (288 x 5min)
    spread = fast / slow - 1

    entry_band = 0.0010
    exit_band = 0.0002

    position = pd.Series(0, index=df.index, dtype=int)
    current = 0
    for i, value in enumerate(spread):
        if pd.isna(value):
            position.iloc[i] = 0
            continue
        if current == 0:
            if value > entry_band:
                current = 1
            elif value < -entry_band:
                current = -1
        elif current == 1:
            if value < -entry_band:
                current = -1
            elif value < exit_band:
                current = 0
        else:
            if value > entry_band:
                current = 1
            elif value > -exit_band:
                current = 0
        position.iloc[i] = current

    return position
