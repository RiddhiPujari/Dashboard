"""Optional Python-based scheduler for the daily X-ray report.

Keep this script running in a terminal or as a Windows service/task.
Windows Task Scheduler is usually the simpler deployment option on Windows.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time

import schedule

ROOT = Path(__file__).resolve().parent


def run_daily_report(input_dir: str, output_dir: str) -> None:
    command = [
        sys.executable,
        str(ROOT / "generate_daily_report.py"),
        "--input-dir", input_dir,
        "--output-dir", output_dir,
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    print(completed.stdout)
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily X-ray management report at a fixed time.")
    parser.add_argument("--time", default="20:00", help="24-hour time, for example 20:00")
    parser.add_argument("--input-dir", default="data/incoming")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--run-now", action="store_true", help="Generate one report immediately before waiting for the scheduled time.")
    args = parser.parse_args()

    job = lambda: run_daily_report(args.input_dir, args.output_dir)
    if args.run_now:
        job()
    schedule.every().day.at(args.time).do(job)
    print(f"Python scheduler started. Daily report time: {args.time}. Keep this terminal running.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
