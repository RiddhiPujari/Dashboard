# X-ray Operations Dashboard

A working Streamlit dashboard for incoming X-ray AI prediction logs. It updates instantly when a user uploads a file or selects dashboard filters.

## Dataset mapping

| Dashboard field | Source column |
|---|---|
| Date/time | `timestamp` (also merges `timestamp.1` if it exists) |
| Clinic | `user_id` |
| Customer / image name | `image_name` |
| Image category | `image_category` |
| Normal / Abnormal | `flag_abnormal` when present; otherwise inferred from `pred_summary` |
| AI finding | `pred_summary` |

## Features

- Automatically loads the latest CSV, XLSX, or XLS prediction log from the data/incoming folder. No dashboard upload is required.
- Clinic, customer/image-name, date range, image category, and Normal/Abnormal filters.
- KPI cards: total X-rays, Normal, Abnormal, abnormality percentage, clinics processed.
- Normal vs Abnormal chart, daily trend, disease distribution, image category distribution, clinic-wise volume.
- Operational statistics by clinic.
- Detailed filtered study records, including clinic and customer/image name.
- Management reports based on active filters: Excel, PDF, CSV summary ZIP, and filtered-record CSV.
- Scheduled daily reporting using either Windows Task Scheduler or the included optional Python scheduler.

## Folder structure

```text
xray_dashboard_project_clinic/
├── app.py
├── xray_pipeline.py
├── generate_daily_report.py
├── python_scheduler.py
├── requirements.txt
├── run_dashboard.bat
├── run_daily_report.bat
├── .streamlit/
│   └── config.toml
├── data/
│   └── incoming/
└── reports/
```

## Installation (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the `Local URL` shown by Streamlit, commonly `http://localhost:8501`.

## Management reports

All dashboard downloads use the active filters.

- **Excel summary:** Report scope, Daily KPIs, Disease Statistics, Clinic Statistics, Image Categories, Data Quality, and Filtered Records.
- **PDF report:** Daily KPIs, disease statistics, image categories, top clinics by study count, and data-quality information.
- **CSV summary ZIP:** separate CSV files for every summary table and filtered records.
- **Filtered records CSV:** row-level X-ray records remaining after filters.

## Daily report generation

1. Put incoming CSV/XLS/XLSX files inside `data/incoming`.
2. Run:

```powershell
.\.venv\Scripts\python.exe generate_daily_report.py --input-dir data/incoming --output-dir reports
```

The script uses the newest file in `data/incoming`, finds its latest study date, and saves daily Excel, PDF, CSV ZIP, and individual CSV reports to `reports`.

## Windows Task Scheduler

1. Open **Task Scheduler** and choose **Create Task**.
2. Create a daily trigger at the required time.
3. Select **Start a program**.
4. Configure:
   - **Program/script:** full path to `.venv\Scripts\python.exe`
   - **Add arguments:** `generate_daily_report.py --input-dir data\incoming --output-dir reports`
   - **Start in:** full path to this project folder
5. Run the task once to test; reports should appear in `reports`.

## Optional Python scheduler

```powershell
.\.venv\Scripts\python.exe python_scheduler.py --time 20:00 --run-now
```

Keep that terminal open. Windows Task Scheduler is normally preferable because it starts automatically at the scheduled time.


## Automatic data refresh (no dashboard upload needed)

The dashboard reads the newest `.xlsx`, `.xls`, or `.csv` file from:

```text
data/incoming/
```

To update the dashboard, replace or save the latest prediction log in that folder. You do **not** upload it in the dashboard. The browser dashboard checks the folder every 60 seconds and reloads the file when its saved/modified time changes.

Recommended routine:

1. Save the incoming file as `data/incoming/latest_prediction_log.xlsx`.
2. Each day, replace that file with the refreshed log (or overwrite and save it).
3. Keep the Streamlit dashboard running. Within about one minute, the KPIs, filters, charts, and management-download files will use the new data.

The app ignores Excel lock files that begin with `~$`.

After downloading this version, install the additional refresh package once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
