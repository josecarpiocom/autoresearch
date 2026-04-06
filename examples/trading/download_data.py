#!/usr/bin/env python3
"""
Download BTC 5m candles from Binance and save to data/btc_5m.csv.

Uses monthly archives from data.binance.vision (fast) and the REST API
for the current month. No API key required.

Usage:
    python download_data.py           # download if not cached
    python download_data.py --force   # re-download
"""

import io
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
START_YEAR = 2017
OUTPUT = Path("data/btc_5m.csv")

ARCHIVE_BASE = "https://data.binance.vision/data/spot/monthly/klines"
REST_URL = "https://api.binance.com/api/v3/klines"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
]


def fetch_month_archive(session, year, month):
    """Download one month of candles from Binance Vision archives."""
    tag = f"{year}-{month:02d}"
    url = f"{ARCHIVE_BASE}/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{tag}.zip"
    resp = session.get(url, timeout=120)
    if resp.status_code == 404:
        return pd.DataFrame()
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        if not names:
            return pd.DataFrame()
        with zf.open(names[0]) as f:
            raw = pd.read_csv(f, header=None)
    raw.columns = KLINE_COLUMNS
    return raw


def fetch_recent_rest(session, start_ms, end_ms):
    """Fetch current month candles via REST API (paginated)."""
    rows = []
    current = start_ms
    while current < end_ms:
        params = {
            "symbol": SYMBOL, "interval": INTERVAL,
            "startTime": current, "endTime": end_ms, "limit": 1000,
        }
        resp = session.get(REST_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        rows.extend(data)
        current = int(data[-1][6]) + 1
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    return raw


def download():
    now = pd.Timestamp.now(tz="UTC")
    current_month = (now.year, now.month)
    frames = []

    print(f"Downloading {SYMBOL} {INTERVAL} candles from Binance...")
    with requests.Session() as session:
        for year in range(START_YEAR, now.year + 1):
            start_m = 8 if year == 2017 else 1
            end_m = 12 if year < now.year else now.month
            for month in range(start_m, end_m + 1):
                if (year, month) >= current_month:
                    break
                frame = fetch_month_archive(session, year, month)
                if not frame.empty:
                    frames.append(frame)
                    print(f"  {year}-{month:02d}: {len(frame):,} rows", flush=True)
                else:
                    print(f"  {year}-{month:02d}: missing")

        # Current month via REST
        month_start = pd.Timestamp(year=now.year, month=now.month, day=1, tz="UTC")
        recent = fetch_recent_rest(
            session,
            int(month_start.timestamp() * 1000),
            int(now.timestamp() * 1000),
        )
        if not recent.empty:
            frames.append(recent)
            print(f"  REST (current month): {len(recent):,} rows")

    if not frames:
        sys.exit("No data downloaded")

    candles = pd.concat(frames, ignore_index=True)

    # Normalize
    candles["open_time"] = pd.to_datetime(
        pd.to_numeric(candles["open_time"], errors="coerce"), unit="ms", utc=True
    )
    for col in ["open", "high", "low", "close", "volume"]:
        candles[col] = pd.to_numeric(candles[col], errors="coerce")

    candles = (
        candles.drop_duplicates(subset=["open_time"])
        .sort_values("open_time")
        .reset_index(drop=True)
    )

    # Save only OHLCV
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    candles[["open_time", "open", "high", "low", "close", "volume"]].to_csv(
        OUTPUT, index=False
    )
    print(f"\nSaved {len(candles):,} candles to {OUTPUT}")
    print(f"Range: {candles['open_time'].min()} → {candles['open_time'].max()}")


if __name__ == "__main__":
    if OUTPUT.exists() and "--force" not in sys.argv:
        print(f"Data already cached at {OUTPUT}. Use --force to re-download.")
    else:
        download()
