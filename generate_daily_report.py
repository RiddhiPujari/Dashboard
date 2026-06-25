"""Generate daily management reports from the newest log in data/incoming.

Example:
    python generate_daily_report.py --input-dir data/incoming --output-dir reports
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from xray_pipeline import clean_and_validate, find_latest_input, read_prediction_file, write_daily_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily X-ray management reports from the latest prediction log.")
    parser.add_argument("--input-dir", default="data/incoming", help="Folder containing incoming CSV/XLS/XLSX logs")
    parser.add_argument("--output-dir", default="reports", help="Folder for generated reports")
    parser.add_argument("--date", default=None, help="Optional report date in YYYY-MM-DD. Default: latest exam date in the log.")
    args = parser.parse_args()

    try:
        source = find_latest_input(args.input_dir)
        raw = read_prediction_file(source, source.name)
        result = clean_and_validate(raw)
        if result.missing_required:
            raise ValueError("Missing required column(s): " + ", ".join(result.missing_required))
        if result.data.empty:
            raise ValueError("No valid rows are available after data validation.")

        target_date = pd.to_datetime(args.date).date() if args.date else result.data["exam_date"].max()
        daily_data = result.data.loc[result.data["exam_date"] == target_date].copy()
        if daily_data.empty:
            raise ValueError(f"No records were found for {target_date}.")

        paths = write_daily_report(daily_data, result.quality, args.output_dir, target_date)
        print(f"Source file: {source}")
        print(f"Report date: {target_date}")
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0
    except Exception as exc:
        print(f"Daily report failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
