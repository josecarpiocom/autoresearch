# Trading strategy optimization

You are optimizing a BTC trading strategy on 5-minute candles.

## Objective

Maximize the out-of-sample Sharpe ratio. The data is split into train and test
sets by `prepare.py`. You do NOT know the split boundary or the test period dates
— this is intentional to prevent you from exploiting known market regimes.

## What you can change

Edit `train.py`. The `evaluate(df)` function receives an OHLCV DataFrame
and must return a Series of positions: 1 (long), 0 (flat), -1 (short).

## Constraints

- **No look-ahead**: signals at bar `t` can only use data from bars `<= t`.
  The framework shifts positions by 1 bar automatically.
- **No external data**: only use the OHLCV columns provided.
- **No hardcoded directional bets**: strategies like `always long` or `always short`
  are invalid — they exploit knowledge of the market direction in a specific period.
  A valid strategy must derive its signals purely from price/volume patterns.
- **Transaction costs**: 5 bps per trade are applied. Avoid strategies that trade every bar.
- **Only pandas and numpy** — no other libraries.

## What makes a valid strategy

A valid strategy generates signals from observable price/volume data using rules
that would be plausible *before* knowing the outcome. The signal logic must work
because of a market microstructure reason, not because you know what period is
being tested.

**Valid**: "go long when 12-bar MA crosses above 48-bar MA" (price pattern)
**Invalid**: "always long" (exploits known bull run)
**Invalid**: "go long after 2024" (hardcodes a date)
**Invalid**: "go long when price > 30000" (arbitrary price threshold tuned to a period)

## Ideas to explore

- Trend following: moving average crossovers, breakouts, momentum
- Mean reversion: RSI extremes, Bollinger band bounces
- Volatility filters: reduce exposure in high-volatility regimes
- Volume confirmation: require volume spikes to confirm signals
- Regime detection: different rules for trending vs ranging markets

## Anti-overfitting guidelines

- Fewer parameters is better. A 2-parameter strategy is better than a 10-parameter one.
- The strategy should show positive Sharpe on the TRAIN set too — if train Sharpe
  is near zero but test Sharpe is high, it's likely overfit to the test period.
- Prefer round, interpretable lookback periods (12, 24, 48, 96 bars).
- Avoid optimizing parameter values to hit a specific number — use values that make
  structural sense (e.g. 1-hour MA = 12 bars, 1-day MA = 288 bars).
