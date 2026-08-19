"""Collect long-running Sage process stability metrics on Windows.

Example:
    python tools/windows_soak.py --process ValorantTranslator --minutes 60 \
        --output dist/soak-metrics.json

The output contains raw samples plus p50/p95/p99 summaries for RSS, handle
count and thread count.  This is intentionally a measurement tool, not a
pass/fail test; use it while exercising game restart, widget reopen, device
changes and other real Windows scenarios.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path


def _powershell_json(script: str) -> dict:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


def resolve_pid(pid: int | None, process_name: str | None) -> int:
    if pid is not None:
        return pid
    if not process_name:
        raise ValueError("provide --pid or --process")
    safe_name = process_name.replace("'", "''")
    payload = _powershell_json(
        f"$p=Get-Process -Name '{safe_name}' -ErrorAction Stop | "
        "Sort-Object StartTime -Descending | Select-Object -First 1; "
        "@{pid=$p.Id} | ConvertTo-Json -Compress"
    )
    return int(payload["pid"])


def sample_process(pid: int) -> dict:
    return _powershell_json(
        f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; "
        "@{"
        "timestamp=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();"
        "rss_bytes=[int64]$p.WorkingSet64;"
        "private_bytes=[int64]$p.PrivateMemorySize64;"
        "handles=[int]$p.HandleCount;"
        "threads=[int]$p.Threads.Count;"
        "cpu_seconds=[double]$p.CPU"
        "} | ConvertTo-Json -Compress"
    )


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def summarize(samples: list[dict]) -> dict:
    summary: dict[str, dict[str, float]] = {}
    for key in ("rss_bytes", "private_bytes", "handles", "threads"):
        values = [float(sample[key]) for sample in samples]
        summary[key] = {
            "min": min(values) if values else 0.0,
            "max": max(values) if values else 0.0,
            "mean": statistics.fmean(values) if values else 0.0,
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
        }
    if samples:
        summary["cpu_seconds"] = {
            "start": float(samples[0]["cpu_seconds"]),
            "end": float(samples[-1]["cpu_seconds"]),
            "delta": float(samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]),
        }
    return summary


def run(pid: int, duration_seconds: float, interval_seconds: float) -> list[dict]:
    samples: list[dict] = []
    deadline = time.monotonic() + duration_seconds
    while True:
        sample = sample_process(pid)
        samples.append(sample)
        if time.monotonic() >= deadline:
            break
        time.sleep(interval_seconds)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Sage Windows soak metrics")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--process", default="ValorantTranslator")
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=Path("soak-metrics.json"))
    args = parser.parse_args()

    pid = resolve_pid(args.pid, args.process)
    samples = run(pid, max(0.0, args.minutes * 60.0), max(0.2, args.interval))
    payload = {
        "pid": pid,
        "duration_minutes": args.minutes,
        "interval_seconds": args.interval,
        "sample_count": len(samples),
        "summary": summarize(samples),
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
