"""
dashboard/app.py
=================
Streamlit monitoring dashboard for the Redbubble Automation System.

Run with:
    streamlit run dashboard/app.py

Displays
--------
- KPI cards: total designs, published, upload success rate, revenue
- Top-performing niches table + bar chart
- Recent upload logs with status indicators
- Product gallery (published products with links)
- Pipeline control: trigger a run-now
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is on the path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.database_manager import DatabaseManager
from modules.analytics import Analytics

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Redbubble Automation Dashboard",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #1e1e2e, #2a2a3e);
        border: 1px solid #3a3a5e;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
    }
    .metric-value {
        font-size: 2.2rem;
        font-weight: 700;
        color: #7c6aff;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #9999bb;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .status-success { color: #4ade80; font-weight: 600; }
    .status-failed  { color: #f87171; font-weight: 600; }
    .status-pending { color: #fbbf24; font-weight: 600; }
    .section-title {
        font-size: 1.15rem;
        font-weight: 600;
        border-left: 4px solid #7c6aff;
        padding-left: 10px;
        margin-bottom: 12px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# DB connection (cached per session)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db() -> DatabaseManager:
    db_path = ROOT / "data" / "database.db"
    return DatabaseManager(str(db_path))


@st.cache_resource
def get_analytics(_db: DatabaseManager) -> Analytics:
    return Analytics(db_manager=_db)


db       = get_db()
reporter = get_analytics(db)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image("https://i.imgur.com/placeholder.png", width=60)  # Replace with logo
    st.title("🎨 RB Automation")
    st.caption("Print-on-Demand Pipeline")
    st.divider()

    st.subheader("⚡ Quick Actions")

    if st.button("▶ Run Pipeline Now", use_container_width=True, type="primary"):
        with st.spinner("Launching pipeline…"):
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, str(ROOT / "main.py"), "--run-now"],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    st.success("Pipeline completed!")
                else:
                    st.error(f"Pipeline error:\n{result.stderr[:400]}")
            except Exception as exc:
                st.error(f"Launch failed: {exc}")

    if st.button("🔄 Refresh Dashboard", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption(f"DB: `{ROOT / 'data' / 'database.db'}`")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🎨 Redbubble Automation — Live Dashboard")
st.caption("Real-time monitoring of your print-on-demand publishing pipeline")

# ---------------------------------------------------------------------------
# KPI Cards
# ---------------------------------------------------------------------------

stats = reporter.overall_stats()

col1, col2, col3, col4, col5 = st.columns(5)

def kpi_card(col, value, label: str, delta: str = "") -> None:
    with col:
        st.metric(label=label, value=value, delta=delta if delta else None)

kpi_card(col1, stats.get("total_designs", 0),    "🖼 Designs Generated")
kpi_card(col2, stats.get("total_published", 0),  "✅ Products Published")
kpi_card(col3, f"{stats.get('upload_success_rate', 0.0):.1f}%", "📈 Upload Success Rate")
kpi_card(col4, stats.get("total_failed", 0),     "❌ Failed Uploads")
kpi_card(col5, f"${stats.get('total_estimated_revenue_usd', 0.0):.2f}",
         "💰 Est. Revenue (USD)")

st.divider()

# ---------------------------------------------------------------------------
# Two-column layout: Niches | Logs
# ---------------------------------------------------------------------------

left, right = st.columns([1.4, 1], gap="large")

# --- Top Niches ---
with left:
    st.markdown('<div class="section-title">🏆 Top Performing Niches</div>',
                unsafe_allow_html=True)

    niche_df = reporter.niche_performance_df()
    if niche_df.empty:
        st.info("No niche performance data yet. Run the pipeline to populate.")
    else:
        display_cols = [c for c in [
            "keyword", "product_count", "total_views",
            "total_favorites", "total_sales", "total_revenue",
        ] if c in niche_df.columns]
        st.dataframe(
            niche_df[display_cols].head(10),
            use_container_width=True,
            hide_index=True,
        )

        # Bar chart
        if "total_views" in niche_df.columns and not niche_df.empty:
            chart_df = niche_df[["keyword", "total_views"]].head(8).set_index("keyword")
            st.bar_chart(chart_df, use_container_width=True)

# --- Recent Logs ---
with right:
    st.markdown('<div class="section-title">📋 Recent Upload Logs</div>',
                unsafe_allow_html=True)
    logs = db.get_recent_logs(limit=30)
    if not logs:
        st.info("No upload logs yet.")
    else:
        for log in logs[:15]:
            status = log.get("status", "")
            icon   = "✅" if status == "success" else "❌" if status == "failed" else "⏳"
            title  = log.get("product_title", "Unknown product") or "Unknown product"
            msg    = log.get("message", "")
            ts     = (log.get("created_at", "")[:16]).replace("T", " ")
            st.markdown(
                f"{icon} **{title[:45]}** &nbsp; `{ts}`  \n"
                f"<small style='color:#888'>{msg[:100]}</small>",
                unsafe_allow_html=True,
            )
            st.divider()

# ---------------------------------------------------------------------------
# Published Products Gallery
# ---------------------------------------------------------------------------

st.markdown('<div class="section-title">🛍 Recently Published Products</div>',
            unsafe_allow_html=True)

products = db.get_published_products(limit=20)
if not products:
    st.info("No published products yet. Run the pipeline to start publishing!")
else:
    prod_df = pd.DataFrame(products)
    show_cols = [c for c in [
        "seo_title", "niche_keyword", "published_at", "redbubble_url", "status"
    ] if c in prod_df.columns]

    # Make URLs clickable
    if "redbubble_url" in prod_df.columns:
        prod_df["redbubble_url"] = prod_df["redbubble_url"].apply(
            lambda u: f"[View on Redbubble]({u})" if u else "—"
        )

    st.dataframe(
        prod_df[show_cols].rename(columns={
            "seo_title":      "Title",
            "niche_keyword":  "Niche",
            "published_at":   "Published",
            "redbubble_url":  "URL",
            "status":         "Status",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# Pipeline Log Viewer
# ---------------------------------------------------------------------------

with st.expander("📄 Full Pipeline Logs", expanded=False):
    log_file = ROOT / "logs" / "pipeline.log"
    if log_file.exists():
        log_text = log_file.read_text(encoding="utf-8", errors="replace")
        # Show last 200 lines
        lines = log_text.splitlines()[-200:]
        st.code("\n".join(lines), language="text")
    else:
        st.info("No log file found yet.")

# ---------------------------------------------------------------------------
# DB Stats footer
# ---------------------------------------------------------------------------

with st.expander("🗄 Database Statistics", expanded=False):
    raw_summary = db.get_products_summary()
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Total Products",   raw_summary.get("total_products", 0))
    col_b.metric("Total Designs",    raw_summary.get("total_designs", 0))
    col_c.metric("Published",        raw_summary.get("total_published", 0))
