"""Interactive X-ray Operations Dashboard. Run: python -m streamlit run app.py"""
from __future__ import annotations

from pathlib import Path

import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from xray_pipeline import (
    clean_and_validate,
    clinic_summary,
    daily_summary,
    data_quality_table,
    disease_summary,
    filter_records,
    find_latest_input,
    image_category_summary,
    kpi_summary,
    read_prediction_file,
    report_csv_zip_bytes,
    report_excel_bytes,
    report_pdf_bytes,
)

st.set_page_config(page_title="X-ray Operations Dashboard", page_icon="X", layout="wide")
ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = ROOT / "data" / "incoming"


@st.cache_data(show_spinner=False)
def load_from_path(path_string: str, modified_time_ns: int):
    """Load the local Excel/CSV file.

    ``modified_time_ns`` is deliberately a parameter: when the source file is
    overwritten or saved again, its modification time changes and Streamlit
    refreshes this cached result on the next automatic rerun.
    """
    del modified_time_ns  # Used as a cache key; the path supplies the file itself.
    path = Path(path_string)
    raw = read_prediction_file(path, path.name)
    return clean_and_validate(raw)


def number(value: int) -> str:
    return f"{value:,}"


def selected_or_all(values: list[str], label: str) -> str:
    if not values:
        return f"All {label}"
    if len(values) <= 3:
        return ", ".join(values)
    return f"{len(values)} selected {label}"


# Rerun the dashboard every 60 seconds while the browser tab is open.
# This makes the dashboard pick up a newly saved or replaced prediction log
# without any file upload or manual page refresh.
st_autorefresh(interval=60_000, key="xray_local_data_refresh")

st.title("X-ray Operations Dashboard")

try:
    latest_local_file = find_latest_input(DEFAULT_INPUT_DIR)
except FileNotFoundError:
    st.error("No prediction log was found in data/incoming. Add or replace an Excel/CSV file in that folder.")
    st.stop()

try:
    result = load_from_path(
        str(latest_local_file),
        latest_local_file.stat().st_mtime_ns,
    )
except Exception as error:
    st.error(f"The latest prediction log could not be read: {error}")
    st.stop()

source_label = latest_local_file.name

with st.sidebar:
    st.caption(f"Data file: {source_label}")
    st.caption("Checks for updated data every 60 seconds.")

if result.missing_required:
    st.error("The uploaded file is missing required column(s): " + ", ".join(result.missing_required))
    st.stop()

records = result.data
if records.empty:
    st.warning("No valid records remained after timestamp validation.")
    st.stop()

with st.sidebar:
    st.header("Filters")
    min_date, max_date = records["exam_date"].min(), records["exam_date"].max()
    selected_dates = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        help="Choose one date or a date range. All dashboard results update immediately.",
    )
    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
    else:
        start_date = end_date = selected_dates

    clinic_options = sorted(records["clinic"].dropna().astype(str).unique().tolist())
    selected_clinics = st.multiselect("Clinic", clinic_options, placeholder="All clinics")

    customer_options = sorted(records["customer"].dropna().astype(str).unique().tolist())
    selected_customers = st.multiselect(
        "Customer / Image Name",
        customer_options,
        placeholder="All customers",
    )

    category_options = sorted(records["image_category_clean"].dropna().unique().tolist())
    selected_categories = st.multiselect("Image category", category_options, placeholder="All image categories")
    selected_outcomes = st.multiselect("Prediction outcome", ["Normal", "Abnormal"], placeholder="All outcomes")
    chart_limit_option = st.selectbox(
        "Clinic bars shown",
        ["Top 10", "Top 25", "Top 50", "All"],
        index=1,
        help="Clinics are ranked by number of X-ray studies after the filters are applied.",
    )

filtered = filter_records(
    records,
    start_date=start_date,
    end_date=end_date,
    clinics=selected_clinics,
    customers=selected_customers,
    image_categories=selected_categories,
    outcomes=selected_outcomes,
)
kpis = kpi_summary(filtered)
scope_text = (
    f"Date: {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')} | "
    f"Clinic: {selected_or_all(selected_clinics, 'clinics')} | "
    f"Customer: {selected_or_all(selected_customers, 'customers')} | "
    f"Image category: {selected_or_all(selected_categories, 'categories')} | "
    f"Outcome: {selected_or_all(selected_outcomes, 'outcomes')}"
)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total X-rays", number(kpis["Total X-rays"]))
k2.metric("Normal cases", number(kpis["Normal cases"]))
k3.metric("Abnormal cases", number(kpis["Abnormal cases"]))
k4.metric("Abnormality %", f"{kpis['Abnormality %']:.1f}%")
k5.metric("Clinics processed", number(kpis["Clinics processed"]))

st.divider()
left, right = st.columns(2)
with left:
    outcome_table = filtered["outcome"].value_counts().rename_axis("Outcome").reset_index(name="Cases")
    if outcome_table.empty:
        st.info("No records match the active filters.")
    else:
        figure = px.pie(outcome_table, names="Outcome", values="Cases", hole=0.58, title="Normal vs Abnormal")
        figure.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(figure, use_container_width=True)
with right:
    trend = daily_summary(filtered)
    if trend.empty:
        st.info("No records match the active filters.")
    else:
        trend_long = trend.melt(id_vars="exam_date", value_vars=["Normal", "Abnormal"], var_name="Outcome", value_name="Cases")
        figure = px.line(trend_long, x="exam_date", y="Cases", color="Outcome", markers=True, title="Daily X-ray Trend")
        figure.update_xaxes(title="Date")
        st.plotly_chart(figure, use_container_width=True)

left, right = st.columns(2)
with left:
    diseases = disease_summary(filtered)
    figure = px.bar(diseases, x="Finding", y="Cases", text="Cases", title="Disease / Finding Distribution")
    figure.update_traces(textposition="outside")
    st.plotly_chart(figure, use_container_width=True)
with right:
    image_categories = image_category_summary(filtered)
    if image_categories.empty:
        st.info("No records match the active filters.")
    else:
        figure = px.bar(image_categories, x="Image category", y="Studies", text="Studies", title="Image Category Distribution")
        figure.update_traces(textposition="outside")
        st.plotly_chart(figure, use_container_width=True)

clinics = clinic_summary(filtered)
st.subheader("Clinic-wise Volume")
if clinics.empty:
    st.info("No records match the active filters.")
else:
    limit_map = {"Top 10": 10, "Top 25": 25, "Top 50": 50, "All": len(clinics)}
    shown_clinics = clinics.head(limit_map[chart_limit_option]).sort_values("Studies")
    figure = px.bar(
        shown_clinics,
        x="Studies",
        y="Clinic",
        orientation="h",
        text="Studies",
        title=f"Clinic-wise X-ray Volume ({chart_limit_option.lower()} by studies)",
    )
    figure.update_traces(textposition="outside")
    figure.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(figure, use_container_width=True)

st.subheader("Operational Statistics by Clinic")
st.caption("Abnormality % = (abnormal studies for a clinic / total studies for that clinic) x 100.")
st.dataframe(
    clinics,
    use_container_width=True,
    hide_index=True,
    column_config={"Abnormality %": st.column_config.NumberColumn(format="%.1f%%")},
)

with st.expander("Data validation and source quality"):
    st.dataframe(data_quality_table(result.quality), use_container_width=True, hide_index=True)
    st.caption("Normal/Abnormal status uses flag_abnormal when available. Disease groups come from pred_summary. Duplicate studies are removed using request_id when available.")

st.subheader("Filtered Study Records")
columns_to_show = [
    column for column in [
        "exam_timestamp", "clinic", "customer", "image_category_clean", "outcome", "disease_group", "prediction_clean", "request_id"
    ] if column in filtered.columns
]
st.dataframe(filtered[columns_to_show], use_container_width=True, hide_index=True)

st.subheader("Management Report Downloads")
report_title = f"X-ray Management Report - {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
file_slug = f"xray_report_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"

try:
    excel_report = report_excel_bytes(filtered, result.quality, report_title, scope_text)
    pdf_report = report_pdf_bytes(filtered, result.quality, report_title, scope_text)
    csv_bundle = report_csv_zip_bytes(filtered, result.quality, scope_text)
except Exception as error:
    st.error(f"The report files could not be created: {error}")
    st.stop()

r1, r2, r3, r4 = st.columns(4)
with r1:
    st.download_button(
        "Download Excel summary",
        data=excel_report,
        file_name=f"{file_slug}_management_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with r2:
    st.download_button(
        "Download PDF report",
        data=pdf_report,
        file_name=f"{file_slug}_management_report.pdf",
        mime="application/pdf",
    )
with r3:
    st.download_button(
        "Download CSV summary ZIP",
        data=csv_bundle,
        file_name=f"{file_slug}_csv_summaries.zip",
        mime="application/zip",
    )
with r4:
    st.download_button(
        "Download filtered records CSV",
        data=filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"{file_slug}_filtered_records.csv",
        mime="text/csv",
    )
