#!/usr/bin/env python3
"""
analyze.py — post-run analysis for autoresearch.

Reads autoresearch-results.tsv and prints summary statistics.

Usage:
    python analyze.py                    # default results file
    python analyze.py results.tsv        # custom file
"""

from __future__ import annotations

import sys
from pathlib import Path

RESULTS_FILE = "autoresearch-results.tsv"


def load_results(path: str) -> tuple[str, str, list[dict]]:
    """Parse the results TSV. Returns (metric_name, direction, rows)."""
    text = Path(path).read_text().strip()
    lines = text.splitlines()

    metric_name, direction = "metric", "higher"
    rows: list[dict] = []

    for line in lines:
        if line.startswith("# metric:"):
            parts = line.split()
            # "# metric: name  direction: dir"
            metric_name = parts[2] if len(parts) > 2 else "metric"
            direction = parts[4] if len(parts) > 4 else "higher"
            continue
        if line.startswith("iteration") or line.startswith("#"):
            continue

        fields = line.split("\t")
        if len(fields) < 6:
            continue

        rows.append({
            "iteration": int(fields[0]),
            "commit": fields[1],
            "metric": float(fields[2]),
            "delta": float(fields[3]),
            "status": fields[4],
            "description": fields[5],
        })

    return metric_name, direction, rows


def analyze(path: str) -> None:
    metric_name, direction, rows = load_results(path)

    if not rows:
        print("No results found.")
        return

    # Basic counts
    total = len(rows)
    keeps = [r for r in rows if r["status"] == "keep"]
    discards = [r for r in rows if r["status"] == "discard"]
    crashes = [r for r in rows if r["status"] == "crash"]
    baseline_rows = [r for r in rows if r["status"] == "baseline"]

    baseline = baseline_rows[0]["metric"] if baseline_rows else rows[0]["metric"]

    # Best metric
    if direction == "lower":
        best_row = min(rows, key=lambda r: r["metric"])
    else:
        best_row = max(rows, key=lambda r: r["metric"])

    best = best_row["metric"]
    improvement = best - baseline

    # Keep rate (excluding baseline)
    iterations = [r for r in rows if r["status"] != "baseline"]
    keep_rate = len(keeps) / len(iterations) * 100 if iterations else 0

    # Running best (frontier)
    running_best = baseline
    frontier: list[tuple[int, float]] = []
    for r in rows:
        if direction == "lower":
            if r["metric"] < running_best:
                running_best = r["metric"]
                frontier.append((r["iteration"], r["metric"]))
        else:
            if r["metric"] > running_best:
                running_best = r["metric"]
                frontier.append((r["iteration"], r["metric"]))

    # Print report
    print(f"{'='*60}")
    print(f"  autoresearch analysis")
    print(f"  metric: {metric_name} ({direction} is better)")
    print(f"{'='*60}")
    print()

    print(f"  Baseline:      {baseline:.6f}")
    print(f"  Best:          {best:.6f}  (iteration {best_row['iteration']})")
    print(f"  Improvement:   {improvement:+.6f}", end="")
    if baseline != 0:
        pct = improvement / abs(baseline) * 100
        print(f"  ({pct:+.1f}%)")
    else:
        print()
    print()

    print(f"  Total iterations:  {len(iterations)}")
    print(f"  Kept:              {len(keeps)}")
    print(f"  Discarded:         {len(discards)}")
    print(f"  Crashed:           {len(crashes)}")
    print(f"  Keep rate:         {keep_rate:.1f}%")
    print()

    # Top improvements
    if keeps:
        print("  Top improvements:")
        ranked = sorted(keeps, key=lambda r: abs(r["delta"]), reverse=True)
        for r in ranked[:10]:
            print(f"    #{r['iteration']:>3}  {r['delta']:+.6f}  {r['commit']}  {r['description']}")
        print()

    # Frontier (new records)
    if frontier:
        print("  Frontier (new records):")
        for it, val in frontier:
            print(f"    iteration {it:>3}  {metric_name}={val:.6f}")
        print()

    # Ascii progress chart
    if len(rows) > 1:
        print("  Progress:")
        _ascii_chart(rows, metric_name, direction)


def _ascii_chart(rows: list[dict], metric_name: str, direction: str) -> None:
    """Simple ascii bar chart of metric over iterations."""
    metrics = [r["metric"] for r in rows]
    lo, hi = min(metrics), max(metrics)
    span = hi - lo if hi != lo else 1.0
    width = 40

    for r in rows:
        bar_len = int((r["metric"] - lo) / span * width)
        bar = "#" * bar_len
        status_char = {"keep": "+", "discard": "-", "crash": "!", "baseline": "=", "no-op": "."}
        char = status_char.get(r["status"], "?")
        print(f"    {r['iteration']:>3} {char} {bar:<{width}} {r['metric']:.4f}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else RESULTS_FILE
    if not Path(path).exists():
        sys.exit(f"[!] Results file not found: {path}")
    analyze(path)


if __name__ == "__main__":
    main()
