"""Data loading, validation, dashboard summaries, and reports for X-ray prediction logs.

Project mapping
---------------
- timestamp        -> X-ray study date/time
- user_id          -> Clinic name
- image_category   -> Image category
- pred_summary     -> AI finding
- flag_abnormal    -> Normal/Abnormal outcome (when present)
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
import re
import zipfile

import pandas as pd

CLINIC_COLUMN = "user_id"
CUSTOMER_COLUMN = "image_name"
REQUIRED_COLUMNS = {"timestamp", CLINIC_COLUMN, "image_category", "pred_summary"}
DISEASE_ORDER = [
    "Normal",
    "Cardiomegaly",
    "Atelectasis",
    "Nodule",
    "COPD",
    "Pneumothorax",
    "Pleural Effusion",
    "Others",
]


@dataclass
class LoadResult:
    """Validated dashboard records plus source-quality information."""

    data: pd.DataFrame
    quality: dict[str, Any]
    missing_required: list[str]


def _normalise_column_names(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardise source column headers while retaining pandas suffixes such as '.1'."""
    frame = frame.copy()
    frame.columns = [str(column).strip().lower().replace(" ", "_") for column in frame.columns]
    return frame


def _coalesce_duplicate_column(frame: pd.DataFrame, base_name: str) -> tuple[pd.DataFrame, list[str]]:
    """Combine base column and Excel duplicate columns such as ``timestamp.1``.

    Pandas names duplicate headers ``timestamp`` and ``timestamp.1``. The dashboard
    takes the first nonblank value across the matching columns.
    """
    pattern = re.compile(rf"^{re.escape(base_name)}(?:\.\d+)?$")
    candidates = [column for column in frame.columns if pattern.fullmatch(column)]
    if not candidates:
        return frame, []

    values = frame[candidates].copy()
    for column in candidates:
        values[column] = values[column].replace(r"^\s*$", pd.NA, regex=True)
    frame[base_name] = values.bfill(axis=1).iloc[:, 0]
    return frame, candidates


def read_prediction_file(file_or_path: Any, file_name: str | None = None) -> pd.DataFrame:
    """Read a CSV, XLSX, or XLS prediction log."""
    name = (file_name or getattr(file_or_path, "name", "")).lower()
    if name.endswith(".csv"):
        try:
            return pd.read_csv(file_or_path)
        except UnicodeDecodeError:
            if hasattr(file_or_path, "seek"):
                file_or_path.seek(0)
            return pd.read_csv(file_or_path, encoding="latin-1")
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_or_path)
    raise ValueError("Unsupported file type. Upload a .csv, .xlsx, or .xls prediction log.")


def _valid_text(series: pd.Series) -> pd.Series:
    """Return stripped strings; null-like values become blank strings."""
    return series.fillna("").astype(str).str.strip()


def _is_abnormal(frame: pd.DataFrame) -> pd.Series:
    """Use ``flag_abnormal`` when present; otherwise infer from ``pred_summary``."""
    summary = _valid_text(frame["pred_summary"]).str.lower()
    inferred = ~summary.isin({"", "normal", "normal study", "no abnormality", "no abnormalities"})

    if "flag_abnormal" not in frame.columns:
        return inferred

    flag = _valid_text(frame["flag_abnormal"]).str.lower()
    yes_values = {"yes", "y", "true", "1", "abnormal", "positive"}
    no_values = {"no", "n", "false", "0", "normal", "negative"}

    result = inferred.copy()
    result.loc[flag.isin(yes_values)] = True
    result.loc[flag.isin(no_values)] = False
    return result


def _disease_group(summary: str, outcome: str) -> str:
    """Assign every study to one primary reporting finding group."""
    if outcome == "Normal":
        return "Normal"

    text = str(summary).lower()
    rules: list[tuple[str, Iterable[str]]] = [
        ("Cardiomegaly", ("cardiomegaly", "cardiac enlargement", "enlarged cardiac", "aortic_enlargement")),
        ("Atelectasis", ("atelectasis",)),
        ("Nodule", ("nodule", "nodular")),
        ("COPD", ("copd", "emphysema", "chronic obstructive")),
        ("Pneumothorax", ("pneumothorax",)),
        ("Pleural Effusion", ("pleural effusion", "pleural_effusion")),
    ]
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return label
    return "Others"


def clean_and_validate(raw: pd.DataFrame) -> LoadResult:
    """Validate, clean, deduplicate, and enrich one source prediction log."""
    frame = _normalise_column_names(raw)
    raw_rows = len(frame)

    frame, timestamp_source_columns = _coalesce_duplicate_column(frame, "timestamp")
    missing_required = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing_required:
        return LoadResult(
            data=pd.DataFrame(),
            quality={
                "source_rows": raw_rows,
                "valid_rows": 0,
                "duplicates_removed": 0,
                "invalid_timestamps": 0,
                "timestamp_source_columns": ", ".join(timestamp_source_columns) or "Not available",
            },
            missing_required=missing_required,
        )

    frame = frame.dropna(how="all").copy()
    timestamp_text = _valid_text(frame["timestamp"]).str.replace("_", " ", regex=False)
    frame["exam_timestamp"] = pd.to_datetime(timestamp_text, errors="coerce", format="mixed")
    invalid_timestamp_rows = int(frame["exam_timestamp"].isna().sum())
    frame = frame.loc[frame["exam_timestamp"].notna()].copy()
    frame["exam_date"] = frame["exam_timestamp"].dt.date

    duplicates_removed = 0
    if "request_id" in frame.columns and _valid_text(frame["request_id"]).ne("").any():
        before = len(frame)
        frame = frame.drop_duplicates(subset=["request_id"], keep="last")
        duplicates_removed = before - len(frame)
    else:
        before = len(frame)
        fallback = [column for column in ["timestamp", "token", "image_name", CLINIC_COLUMN] if column in frame.columns]
        frame = frame.drop_duplicates(subset=fallback, keep="last")
        duplicates_removed = before - len(frame)

    frame["clinic"] = _valid_text(frame[CLINIC_COLUMN]).replace("", "Unknown clinic")
    if CUSTOMER_COLUMN in frame.columns:
        frame["customer"] = _valid_text(frame[CUSTOMER_COLUMN]).replace("", "Unknown customer")
    else:
        frame["customer"] = "Unknown customer"
    frame["image_category_clean"] = _valid_text(frame["image_category"]).replace("", "Unknown")
    frame["prediction_clean"] = _valid_text(frame["pred_summary"]).replace("", "Not reported")
    frame["outcome"] = _is_abnormal(frame).map({True: "Abnormal", False: "Normal"})
    frame["disease_group"] = [
        _disease_group(summary, outcome)
        for summary, outcome in zip(frame["prediction_clean"], frame["outcome"])
    ]

    frame = frame.sort_values("exam_timestamp", ascending=False).reset_index(drop=True)
    quality = {
        "source_rows": raw_rows,
        "valid_rows": len(frame),
        "duplicates_removed": duplicates_removed,
        "invalid_timestamps": invalid_timestamp_rows,
        "timestamp_source_columns": ", ".join(timestamp_source_columns) or "timestamp",
        "date_min": frame["exam_date"].min() if not frame.empty else None,
        "date_max": frame["exam_date"].max() if not frame.empty else None,
    }
    return LoadResult(data=frame, quality=quality, missing_required=[])


def filter_records(
    data: pd.DataFrame,
    start_date: Any,
    end_date: Any,
    clinics: list[str] | None = None,
    customers: list[str] | None = None,
    image_categories: list[str] | None = None,
    outcomes: list[str] | None = None,
) -> pd.DataFrame:
    """Apply the selected date, clinic, customer, category, and outcome filters."""
    if data.empty:
        return data.copy()

    result = data.copy()
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    result = result.loc[result["exam_date"].between(start, end)]
    if clinics:
        result = result.loc[result["clinic"].isin(clinics)]
    if customers:
        result = result.loc[result["customer"].isin(customers)]
    if image_categories:
        result = result.loc[result["image_category_clean"].isin(image_categories)]
    if outcomes:
        result = result.loc[result["outcome"].isin(outcomes)]
    return result.copy()


def kpi_summary(data: pd.DataFrame) -> dict[str, float | int]:
    """Calculate the five dashboard KPIs for the current filtered records."""
    total = len(data)
    normal = int((data["outcome"] == "Normal").sum()) if total else 0
    abnormal = int((data["outcome"] == "Abnormal").sum()) if total else 0
    return {
        "Total X-rays": total,
        "Normal cases": normal,
        "Abnormal cases": abnormal,
        "Abnormality %": round((abnormal / total * 100) if total else 0, 1),
        "Clinics processed": int(data["clinic"].nunique()) if total else 0,
    }


def disease_summary(data: pd.DataFrame) -> pd.DataFrame:
    """Count the specified disease/finding categories."""
    counts = data["disease_group"].value_counts() if not data.empty else pd.Series(dtype="int64")
    return pd.DataFrame({"Finding": DISEASE_ORDER, "Cases": [int(counts.get(name, 0)) for name in DISEASE_ORDER]})


def daily_summary(data: pd.DataFrame) -> pd.DataFrame:
    """Return daily Normal/Abnormal counts for the trend chart."""
    if data.empty:
        return pd.DataFrame(columns=["exam_date", "Normal", "Abnormal"])
    summary = data.groupby(["exam_date", "outcome"]).size().unstack(fill_value=0).reset_index()
    for outcome in ["Normal", "Abnormal"]:
        if outcome not in summary.columns:
            summary[outcome] = 0
    return summary[["exam_date", "Normal", "Abnormal"]].sort_values("exam_date")


def clinic_summary(data: pd.DataFrame) -> pd.DataFrame:
    """Return operational statistics for each clinic."""
    columns = ["Clinic", "Studies", "Normal cases", "Abnormal cases", "Abnormality %"]
    if data.empty:
        return pd.DataFrame(columns=columns)

    summary = data.groupby(["clinic", "outcome"]).size().unstack(fill_value=0)
    for outcome in ["Normal", "Abnormal"]:
        if outcome not in summary.columns:
            summary[outcome] = 0

    summary = summary.rename(columns={"Normal": "Normal cases", "Abnormal": "Abnormal cases"})
    summary["Studies"] = summary["Normal cases"] + summary["Abnormal cases"]
    summary["Abnormality %"] = (summary["Abnormal cases"] / summary["Studies"] * 100).round(1)
    summary = summary.reset_index().rename(columns={"clinic": "Clinic"})
    return summary[columns].sort_values(["Studies", "Abnormal cases", "Clinic"], ascending=[False, False, True])


def image_category_summary(data: pd.DataFrame) -> pd.DataFrame:
    """Count filtered studies by image category."""
    if data.empty:
        return pd.DataFrame(columns=["Image category", "Studies"])
    return (
        data.groupby("image_category_clean").size().reset_index(name="Studies")
        .rename(columns={"image_category_clean": "Image category"})
        .sort_values("Studies", ascending=False)
    )


def data_quality_table(quality: dict[str, Any]) -> pd.DataFrame:
    """Format validation information for the dashboard and reports."""
    rows = [
        ("Rows read from source", quality.get("source_rows", 0)),
        ("Valid rows used", quality.get("valid_rows", 0)),
        ("Duplicate studies removed", quality.get("duplicates_removed", 0)),
        ("Rows excluded: invalid timestamp", quality.get("invalid_timestamps", 0)),
        ("Timestamp column(s) combined", quality.get("timestamp_source_columns", "timestamp")),
        ("Earliest exam date", quality.get("date_min", "")),
        ("Latest exam date", quality.get("date_max", "")),
    ]
    return pd.DataFrame(rows, columns=["Check", "Value"])


def _report_tables(data: pd.DataFrame, quality: dict[str, Any], scope_text: str | None = None) -> dict[str, pd.DataFrame]:
    """Build the reusable report tables for Excel, PDF, and CSV downloads."""
    scope = pd.DataFrame(
        [("Report scope", scope_text or "Current filtered dashboard records"), ("Studies included", len(data))],
        columns=["Report detail", "Value"],
    )
    return {
        "Report Scope": scope,
        "Daily KPIs": pd.DataFrame(list(kpi_summary(data).items()), columns=["Metric", "Value"]),
        "Disease Statistics": disease_summary(data),
        "Clinic Statistics": clinic_summary(data),
        "Image Categories": image_category_summary(data),
        "Data Quality": data_quality_table(quality),
        "Filtered Records": data,
    }


def _safe_display_value(value: Any) -> str:
    """Create a printable cell value without raising for numbers, NaN, or dates."""
    if value is None or (not isinstance(value, (list, tuple, dict, set)) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def report_excel_bytes(data: pd.DataFrame, quality: dict[str, Any], report_title: str, scope_text: str | None = None) -> bytes:
    """Create an Excel management report with KPI, disease, clinic, and detail sheets."""
    output = BytesIO()
    sheets = _report_tables(data, quality, scope_text)
    with pd.ExcelWriter(output, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
        workbook = writer.book
        title_fmt = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#FFFFFF", "bg_color": "#1F4E78"})
        header_fmt = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#4472C4"})
        percent_fmt = workbook.add_format({"num_format": '0.0"%"'})

        for sheet_name, table in sheets.items():
            table.to_excel(writer, sheet_name=sheet_name, startrow=2, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.merge_range(0, 0, 0, max(0, len(table.columns) - 1), report_title, title_fmt)

            # Select by position, not column label. This remains safe even when
            # source data contains duplicate labels such as timestamp/timestamp.1.
            for col_num, column in enumerate(table.columns):
                worksheet.write(2, col_num, str(column), header_fmt)
                values = table.iloc[:, col_num].tolist() if not table.empty else []
                display_lengths = [len(str(column))] + [len(_safe_display_value(value)) for value in values]
                width = min(max(display_lengths) + 2, 45)
                worksheet.set_column(col_num, col_num, width)

            worksheet.freeze_panes(3, 0)
            if "Abnormality %" in table.columns:
                percentage_index = table.columns.get_loc("Abnormality %")
                worksheet.set_column(percentage_index, percentage_index, 16, percent_fmt)
    return output.getvalue()


def _table_for_pdf(table: pd.DataFrame, max_rows: int | None = None) -> list[list[str]]:
    """Convert a DataFrame into printable ReportLab table rows."""
    view = table.head(max_rows).copy() if max_rows is not None else table.copy()
    rows = [[str(column) for column in view.columns]]
    for _, row in view.iterrows():
        formatted: list[str] = []
        for column, value in row.items():
            if column == "Abnormality %" and pd.notna(value):
                formatted.append(f"{float(value):.1f}%")
            else:
                formatted.append(_safe_display_value(value))
        rows.append(formatted)
    return rows


def report_pdf_bytes(data: pd.DataFrame, quality: dict[str, Any], report_title: str, scope_text: str | None = None) -> bytes:
    """Create a printable PDF management report from the currently filtered data."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output = BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, leading=22, textColor=colors.HexColor("#17365D"))
    section_style = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=12, leading=15, textColor=colors.HexColor("#1F4E78"), spaceBefore=8, spaceAfter=5)
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=12)
    footer_style = ParagraphStyle("Footer", parent=styles["BodyText"], fontSize=7, leading=9, alignment=TA_CENTER, textColor=colors.HexColor("#666666"))
    table_header_style = ParagraphStyle("TableHeader", parent=body_style, textColor=colors.white, fontName="Helvetica-Bold")

    def escape_pdf_text(text: Any) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def pdf_table(rows: list[list[str]], col_widths: list[float]) -> Table:
        paragraph_rows = []
        for row_index, row in enumerate(rows):
            style = table_header_style if row_index == 0 else body_style
            paragraph_rows.append([Paragraph(escape_pdf_text(cell), style) for cell in row])
        table = Table(paragraph_rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D6E3")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    tables = _report_tables(data, quality, scope_text)
    story = [
        Paragraph(report_title, title_style),
        Spacer(1, 4 * mm),
        Paragraph(escape_pdf_text(scope_text or "Scope: current filtered dashboard records"), body_style),
        Spacer(1, 5 * mm),
        Paragraph("Daily KPIs", section_style),
        pdf_table(_table_for_pdf(tables["Daily KPIs"]), [90 * mm, 45 * mm]),
        Spacer(1, 7 * mm),
        Paragraph("Disease Statistics", section_style),
        pdf_table(_table_for_pdf(tables["Disease Statistics"]), [90 * mm, 45 * mm]),
        Spacer(1, 7 * mm),
        Paragraph("Image Category Distribution", section_style),
        pdf_table(_table_for_pdf(tables["Image Categories"]), [90 * mm, 45 * mm]),
        PageBreak(),
        Paragraph("Clinic Statistics", section_style),
        Paragraph("The PDF displays the top 25 clinics ranked by number of filtered X-ray studies. The Excel and CSV reports contain the full clinic table.", body_style),
        Spacer(1, 3 * mm),
        pdf_table(_table_for_pdf(tables["Clinic Statistics"], max_rows=25), [80 * mm, 30 * mm, 30 * mm, 34 * mm, 30 * mm]),
        Spacer(1, 7 * mm),
        Paragraph("Data Quality", section_style),
        pdf_table(_table_for_pdf(tables["Data Quality"]), [100 * mm, 125 * mm]),
        Spacer(1, 6 * mm),
        Paragraph("Clinic-level summaries are based on the source column user_id.", footer_style),
    ]

    def add_page_number(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawRightString(page_size[0] - 14 * mm, 8 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return output.getvalue()


def report_csv_zip_bytes(data: pd.DataFrame, quality: dict[str, Any], scope_text: str | None = None) -> bytes:
    """Create a ZIP with CSV tables for management and data-team use."""
    output = BytesIO()
    tables = _report_tables(data, quality, scope_text)
    names = {
        "Report Scope": "report_scope.csv",
        "Daily KPIs": "daily_kpis.csv",
        "Disease Statistics": "disease_statistics.csv",
        "Clinic Statistics": "clinic_statistics.csv",
        "Image Categories": "image_category_distribution.csv",
        "Data Quality": "data_quality.csv",
        "Filtered Records": "filtered_study_records.csv",
    }
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table_name, filename in names.items():
            archive.writestr(filename, tables[table_name].to_csv(index=False))
    return output.getvalue()


def write_daily_report(data: pd.DataFrame, quality: dict[str, Any], output_dir: str | Path, report_date: Any) -> dict[str, Path]:
    """Write scheduled daily Excel, PDF, CSV ZIP, and individual CSV reports."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    date_label = pd.to_datetime(report_date).strftime("%Y%m%d")
    title = f"X-ray Operational Report - {pd.to_datetime(report_date).strftime('%d %b %Y')}"
    scope = f"Daily report for {pd.to_datetime(report_date).strftime('%d %b %Y')}; studies included: {len(data)}"

    excel_path = output / f"daily_xray_management_report_{date_label}.xlsx"
    pdf_path = output / f"daily_xray_management_report_{date_label}.pdf"
    csv_zip_path = output / f"daily_xray_csv_summaries_{date_label}.zip"
    excel_path.write_bytes(report_excel_bytes(data, quality, title, scope))
    pdf_path.write_bytes(report_pdf_bytes(data, quality, title, scope))
    csv_zip_path.write_bytes(report_csv_zip_bytes(data, quality, scope))

    paths: dict[str, Path] = {"excel": excel_path, "pdf": pdf_path, "csv_summary_zip": csv_zip_path}
    for table_name, table in _report_tables(data, quality, scope).items():
        filename = table_name.lower().replace(" ", "_")
        path = output / f"daily_xray_{filename}_{date_label}.csv"
        table.to_csv(path, index=False)
        paths[filename] = path
    return paths


def find_latest_input(input_dir: str | Path) -> Path:
    """Return the newest CSV/XLS/XLSX log in the incoming-data folder."""
    folder = Path(input_dir)
    candidates = [
        path
        for path in folder.glob("*")
        if path.suffix.lower() in {".csv", ".xlsx", ".xls"}
        and not path.name.startswith("~$")  # Ignore Excel temporary lock files.
    ]
    if not candidates:
        raise FileNotFoundError(f"No .csv, .xlsx or .xls prediction log found in {folder.resolve()}")
    return max(candidates, key=lambda path: path.stat().st_mtime)
