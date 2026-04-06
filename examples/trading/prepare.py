"""
prepare.py — fixed infrastructure. Not modified by the agent.

Evaluates train.py's evaluate(df) function on a hidden train/test split
and reports the out-of-sample Sharpe ratio.

The split boundary and test period dates are intentionally not exposed
to the agent to prevent period-specific overfitting (e.g. always-long
during a known bull run).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from train import evaluate

# ── Config ──────────────────────────────────────────────────────────────────

DATA_PATH = Path("data/btc_5m.csv")
TRAIN_RATIO = 0.75               # 75% train, 25% test — dates not exposed to agent
ANNUAL_FACTOR = np.sqrt(365.25 * 24 * 12)  # annualize 5min bars
COMMISSION_BPS = 5               # 5 bps round-trip per trade


# ── Helpers ─────────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH, parse_dates=["open_time"])
    df = df.set_index("open_time").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def compute_sharpe(df: pd.DataFrame, positions: pd.Series) -> tuple[float, float, int]:
    """Annualized Sharpe with transaction costs. Positions shifted +1 bar (no look-ahead)."""
    pos = positions.shift(1).fillna(0)
    returns = df["close"].pct_change().fillna(0)
    strat_returns = pos * returns

    trades = int((pos.diff().fillna(0).abs() > 0).sum())
    total_cost = trades * (COMMISSION_BPS / 10_000)
    strat_returns = strat_returns - (total_cost / len(strat_returns) if len(strat_returns) > 0 else 0)

    mean = strat_returns.mean()
    std = strat_returns.std()
    sharpe = (mean / std) * ANNUAL_FACTOR if (std > 0 and not np.isnan(std)) else 0.0
    total_return = (1 + strat_returns).prod() - 1

    return sharpe, total_return, trades


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load_data()

    # Split by position, not date — boundary intentionally hidden from agent
    split_idx = int(len(df) * TRAIN_RATIO)
    train = df.iloc[:split_idx]
    test = df.iloc[split_idx:]

    if len(train) < 1000 or len(test) < 1000:
        print("ERROR: insufficient data", file=sys.stderr)
        sys.exit(1)

    train_pos = evaluate(train)
    test_pos = evaluate(test)

    assert len(train_pos) == len(train), "train positions length mismatch"
    assert len(test_pos) == len(test), "test positions length mismatch"

    valid_vals = {-1, 0, 1}
    for split_name, pos in [("train", train_pos), ("test", test_pos)]:
        invalid = set(pos.dropna().unique()) - valid_vals
        assert not invalid, f"invalid {split_name} positions: {invalid}"

    train_sharpe, train_ret, train_trades = compute_sharpe(train, train_pos)
    test_sharpe, test_ret, test_trades = compute_sharpe(test, test_pos)

    # Agent sees train stats for context — not the metric
    print(f"train_sharpe={train_sharpe:.6f}")
    print(f"train_return={train_ret:.4f}")
    print(f"train_trades={train_trades}")
    print(f"test_return={test_ret:.4f}")
    print(f"test_trades={test_trades}")

    # The metric — out-of-sample Sharpe
    print(f"sharpe={test_sharpe:.6f}")


if __name__ == "__main__":
    main()
