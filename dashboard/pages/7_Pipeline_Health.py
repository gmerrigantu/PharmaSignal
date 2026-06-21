"""Pipeline Health — operational trust + data-quality (§12.2, §14.3)."""
from __future__ import annotations

import pathlib as _pl, sys as _sys
_d = _pl.Path(__file__).resolve().parent
_sys.path.insert(0, str(_d if (_d / "lib.py").exists() else _d.parent))
import lib
import streamlit as st

lib.page_setup("Pipeline Health", "🩹")
st.title("🩹 Pipeline Health")
lib.source_badge()

health = lib.gold("pipeline_health")
latest = health.iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Status", str(latest["status"]).upper())
c2.metric("Gold rows", f"{int(latest['rows_gold']):,}")
c3.metric("Failed checks", int(latest["failed_checks"]))
c4.metric("Warnings", int(latest["warning_checks"]))

st.caption(
    f"Run **{latest['run_id']}** · source **{latest['source']}** · period **{latest['source_period']}** · "
    f"refreshed {str(latest['run_timestamp'])[:19]} UTC · est. cost ${float(latest['estimated_cost_usd']):.2f}"
)
st.write(latest["notes"])

st.subheader("Row counts by stage")
st.dataframe(
    health[["source", "source_period", "rows_raw", "rows_silver", "rows_gold",
            "failed_checks", "warning_checks", "status"]].rename(columns={
        "source": "Source", "source_period": "Period", "rows_raw": "Raw reports",
        "rows_silver": "Silver rows", "rows_gold": "Gold rows", "failed_checks": "Failed",
        "warning_checks": "Warnings", "status": "Status"}),
    hide_index=True, width="stretch",
)

if lib.has("data_quality_checks"):
    st.subheader("Data-quality checks")
    dq = lib.gold("data_quality_checks")
    dq["icon"] = dq["status"].map({"pass": "🟢", "warn": "🟡", "fail": "🔴"})
    st.dataframe(
        dq[["icon", "table", "check", "category", "detail"]].rename(columns={
            "icon": "", "table": "Table", "check": "Check", "category": "Category", "detail": "Detail"}),
        hide_index=True, width="stretch",
    )
