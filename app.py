"""Printflo Delivery Portal — Streamlit dashboard for DHL dispatch tracking.

Reads DHL "DashboardSummary" CSV exports from the data/ folder, overlays live
statuses from printflo_tracking_cache.json (maintained by sync_dhl.py via a
GitHub Action), and presents a searchable, filterable delivery table.
"""

import glob
import json
import os
import re
from datetime import datetime

import pandas as pd
import streamlit as st

# ------------------------------------------------------------------ constants
CACHE_FILE = "printflo_tracking_cache.json"
DATA_GLOBS = ["data/*.csv", "*.csv"]  # data/ preferred; root kept for compatibility

BRAND_BLUE = "#174A8C"

st.set_page_config(page_title="Printflo Delivery Portal", page_icon="📦", layout="wide")

# ------------------------------------------------------------------ auth gate
def check_password() -> bool:
    """Simple access-code gate. Set APP_PASSWORD in Streamlit secrets.

    If no password is configured (e.g. running locally), the app is open.
    """
    expected = None
    try:
        expected = st.secrets.get("APP_PASSWORD")
    except Exception:
        pass
    expected = expected or os.environ.get("APP_PASSWORD")

    if not expected:
        return True  # no password configured — open access (local dev)

    if st.session_state.get("auth_ok"):
        return True

    st.markdown("<br><br>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        try:
            st.image("printflo-logo.png", width=200)
        except Exception:
            pass
        st.markdown("#### Printflo Delivery Portal")
        pwd = st.text_input("Access code", type="password", key="pwd_input")
        if st.button("Sign in", type="primary", use_container_width=True):
            if pwd == expected:
                st.session_state["auth_ok"] = True
                st.rerun()
            else:
                st.error("Incorrect access code.")
    return False


if not check_password():
    st.stop()

# ------------------------------------------------------------------ styling
st.markdown(
    f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif !important; }}
    #MainMenu, footer, header {{ visibility: hidden; }}
    h1 {{ font-weight: 700 !important; letter-spacing: -0.5px;
         border-bottom: 3px solid {BRAND_BLUE}; padding-bottom: 10px; margin-bottom: 5px !important; }}
    [data-testid="stMetricValue"] {{ font-size: 2.1rem !important; font-weight: 700 !important; color: {BRAND_BLUE} !important; }}
    table {{ border-collapse: collapse !important; width: 100% !important; font-size: 0.9rem !important;
            background-color: #FFFFFF !important; border-radius: 8px !important; overflow: hidden !important;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05) !important; }}
    th {{ background-color: #F3F4F6 !important; font-weight: 600 !important; border-bottom: 2px solid #E5E7EB !important;
         text-transform: uppercase; font-size: 0.75rem; color: #4B5563 !important; text-align: left !important;
         padding: 12px 16px !important; }}
    td {{ background-color: #FFFFFF !important; border-bottom: 1px solid #E5E7EB !important;
         vertical-align: middle !important; text-align: left !important; padding: 12px 16px !important;
         color: #111827 !important; }}
</style>
""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ data load
def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def clean_shipment_number(value) -> str:
    s = re.sub(r"\.0$", "", str(value))
    return re.sub(r"[^A-Za-z0-9]", "", s)


@st.cache_data(ttl=60)
def load_and_merge_data() -> pd.DataFrame:
    files: list[str] = []
    for pattern in DATA_GLOBS:
        files.extend(sorted(glob.glob(pattern)))
    if not files:
        return pd.DataFrame()

    frames = []
    for file in files:
        try:
            df = pd.read_csv(file, dtype={"Shipment number": str})
        except Exception:
            continue
        base = os.path.basename(file).rsplit(".csv", 1)[0]
        is_generic = base.lower().replace(" ", "").startswith("dashboardsummary")
        df["Campaign"] = "Standard Dispatch" if is_generic else base
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    master = pd.concat(frames, ignore_index=True)
    master.columns = master.columns.str.strip()

    if "Shipment number" in master.columns:
        master["Shipment number"] = master["Shipment number"].apply(clean_shipment_number)
        master = master.drop_duplicates(subset=["Shipment number"], keep="last")

    if "Dispatch date" in master.columns:
        master["Dispatch Date Parsed"] = pd.to_datetime(
            master["Dispatch date"], format="%d/%m/%Y", errors="coerce"
        )

    # Overlay live data from the background sync cache
    cache = {k: v for k, v in load_cache().items() if not k.startswith("_")}
    if "Shipment number" in master.columns and cache:
        keys = master["Shipment number"].astype(str)
        if "Status" in master.columns:
            live_status = keys.map(lambda k: cache.get(k, {}).get("status"))
            master["Status"] = live_status.fillna(master["Status"])
        master["Last Seen"] = keys.map(lambda k: cache.get(k, {}).get("last_event", "") or "")
        master["Delivered At Parsed"] = pd.to_datetime(
            keys.map(lambda k: cache.get(k, {}).get("delivered_at", "") or None),
            errors="coerce",
            utc=True,
        )
        # Live ETA from DHL overrides the CSV's ETA window where available
        live_eta = pd.to_datetime(
            keys.map(lambda k: cache.get(k, {}).get("eta", "") or None),
            errors="coerce",
        )
        if "ETA" in master.columns:
            master["ETA"] = live_eta.dt.strftime("%d/%m by %H:%M").fillna(master["ETA"])
        else:
            master["ETA"] = live_eta.dt.strftime("%d/%m by %H:%M")

    return master


# ------------------------------------------------------------------ header
col_logo, col_title = st.columns([1, 5])
with col_logo:
    try:
        st.markdown("<div style='margin-top: 15px;'>", unsafe_allow_html=True)
        st.image("printflo-logo.png", width=180)
        st.markdown("</div>", unsafe_allow_html=True)
    except Exception:
        pass
with col_title:
    st.title("Printflo Delivery Portal")
    st.markdown(
        "<p style='color: #6B7280; font-size: 1.1rem; margin: 0 0 30px 0;'>"
        "Track and manage network deliveries.</p>",
        unsafe_allow_html=True,
    )

df = load_and_merge_data()

if df.empty:
    st.warning("No tracking data available. Upload the latest DHL CSV export to the data/ folder.")
    st.stop()

# ------------------------------------------------------------------ metrics
df["Clean Status"] = df["Status"].astype(str).str.strip().str.lower()

today = pd.Timestamp.now(tz="Europe/London").tz_localize(None).normalize()
start_of_week = today - pd.Timedelta(days=today.dayofweek)

total_shipments = len(df)
in_transit = int((df["Clean Status"] == "in transit").sum())
out_for_delivery = int((df["Clean Status"] == "out for delivery").sum())
delivered = int((df["Clean Status"] == "delivered").sum())
exceptions = int(
    df["Clean Status"].str.contains("exception|delay|fail", na=False, regex=True).sum()
)

# Real delivery timestamps (from DHL scans, via the sync cache)
delivered_today = delivered_week = 0
if "Delivered At Parsed" in df.columns:
    delivered_local = df["Delivered At Parsed"].dt.tz_convert("Europe/London").dt.tz_localize(None)
    delivered_today = int((delivered_local >= today).sum())
    delivered_week = int((delivered_local >= start_of_week).sum())

if "Dispatch Date Parsed" in df.columns:
    dispatched_week = int((df["Dispatch Date Parsed"] >= start_of_week).sum())
else:
    dispatched_week = 0

st.markdown("<br>", unsafe_allow_html=True)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total Shipments", f"{total_shipments:,}")
m2.metric("In Transit", f"{in_transit:,}")
m3.metric("Out for Delivery", f"{out_for_delivery:,}")
m4.metric("Delivered", f"{delivered:,}")
m5.metric("Exceptions", f"{exceptions:,}")

n1, n2, n3, _, _ = st.columns(5)
n1.metric("Delivered Today", f"{delivered_today:,}", help="From DHL delivery scans (live sync)")
n2.metric("Delivered This Week", f"{delivered_week:,}", help="From DHL delivery scans (live sync)")
n3.metric("Dispatched This Week", f"{dispatched_week:,}")

# Last-sync stamp: prefer timestamp recorded inside the cache, fall back to file mtime
sync_time_str = "Awaiting initial background sync"
cache_meta = load_cache().get("_meta", {})
if cache_meta.get("last_sync"):
    try:
        ts = datetime.fromisoformat(cache_meta["last_sync"])
        sync_time_str = ts.strftime("%A, %d %B %Y at %H:%M %Z").strip()
    except Exception:
        pass
elif os.path.exists(CACHE_FILE):
    sync_time_str = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE)).strftime(
        "%A, %d %B %Y at %H:%M"
    )

st.markdown(
    f"<div style='text-align: center; color: #6B7280; font-size: 0.85rem; margin: 10px 0 20px; "
    f"font-weight: 500;'>Network data last synced: {sync_time_str}</div>",
    unsafe_allow_html=True,
)
st.markdown("<hr><br>", unsafe_allow_html=True)

# ------------------------------------------------------------------ filters
f1, f2, f3, f4 = st.columns(4)

search_postcode = f1.text_input("SEARCH POSTCODE", placeholder="e.g. B78 3JD")
search_ref = f2.text_input("SEARCH CUSTOMER REF. / RECIPIENT")
campaigns = sorted(df["Campaign"].dropna().unique()) if "Campaign" in df.columns else []
selected_campaign = f3.selectbox("CAMPAIGN", ["All Campaigns"] + list(campaigns))
selected_status = f4.selectbox(
    "STATUS", ["All Statuses", "In Transit", "Out for Delivery", "Delivered", "Exception"]
)

filtered = df.copy()

if search_postcode.strip() and "Postal Code" in filtered.columns:
    filtered = filtered[
        filtered["Postal Code"].astype(str).str.contains(search_postcode.strip(), case=False, na=False)
    ]
if search_ref.strip():
    term = search_ref.strip()
    mask = pd.Series(False, index=filtered.index)
    for col in ("Customer reference", "Business/Recipient name"):
        if col in filtered.columns:
            mask |= filtered[col].astype(str).str.contains(term, case=False, na=False)
    filtered = filtered[mask]
if selected_campaign != "All Campaigns":
    filtered = filtered[filtered["Campaign"] == selected_campaign]
if selected_status != "All Statuses":
    if selected_status == "Exception":
        filtered = filtered[
            filtered["Clean Status"].str.contains("exception|delay|fail", na=False, regex=True)
        ]
    else:
        filtered = filtered[filtered["Clean Status"] == selected_status.lower()]

# Undelivered first, newest dispatches first within each group
filtered["Is_Delivered"] = filtered["Clean Status"] == "delivered"
sort_cols = ["Is_Delivered"]
ascending = [True]
if "Dispatch Date Parsed" in filtered.columns:
    sort_cols.append("Dispatch Date Parsed")
    ascending.append(False)
filtered = filtered.sort_values(by=sort_cols, ascending=ascending)

st.markdown(
    f"<div style='color:#6B7280; font-size:0.85rem; margin-bottom:8px;'>"
    f"Showing {len(filtered):,} of {total_shipments:,} shipments</div>",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ export
export_df = filtered.drop(
    columns=[
        c
        for c in ("Is_Delivered", "Clean Status", "Dispatch Date Parsed", "Delivered At Parsed")
        if c in filtered.columns
    ]
)
st.download_button(
    label="📥 Download filtered data as CSV",
    data=export_df.to_csv(index=False).encode("utf-8"),
    file_name=f"Printflo_Deliveries_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
    mime="text/csv",
)
st.markdown("<br>", unsafe_allow_html=True)

# ------------------------------------------------------------------ table
def badge(text: str, bg: str, fg: str) -> str:
    return (
        f'<span style="background-color:{bg}; color:{fg}; padding:6px 12px; border-radius:20px; '
        f'font-weight:600; font-size:0.8rem; text-transform:uppercase;">{text}</span>'
    )


def blank_when_delivered(row, col_name):
    val = str(row[col_name]) if pd.notna(row[col_name]) else ""
    if row["Clean Status"] == "delivered":
        return badge("-", "#D4EDDA", "#155724")
    return val


for col in ("Delivery due date", "ETA"):
    if col in filtered.columns:
        filtered[col] = filtered.apply(lambda r, c=col: blank_when_delivered(r, c), axis=1)


def make_clickable(shipment_num):
    s = str(shipment_num).strip()
    if not s or s.lower() == "nan" or len(s) < 5:
        return ""
    url = f"https://www.dhl.com/gb-en/home/tracking.html?tracking-id={s}&submit=1"
    return (
        f'<a href="{url}" target="_blank" style="color:{BRAND_BLUE}; text-decoration:underline; '
        f'font-weight:600;">Track Order</a>'
    )


if "Shipment number" in filtered.columns:
    filtered["Tracking Link"] = filtered["Shipment number"].apply(make_clickable)


def color_status(status_val):
    v = str(status_val).strip().lower()
    bg, fg = "#E0E0E0", "#333333"
    if v == "delivered":
        bg, fg = "#D4EDDA", "#155724"
    elif v in ("in transit", "out for delivery"):
        bg, fg = "#FFF3CD", "#856404"
    elif re.search(r"exception|delay|fail", v):
        bg, fg = "#F8D7DA", "#721C24"
    return badge(status_val, bg, fg)


filtered["Status"] = filtered["Status"].apply(color_status)

display_cols = [
    "Campaign", "Customer reference", "Business/Recipient name", "Status",
    "Last Seen", "Delivery due date", "ETA", "Tracking Link",
    "Number of parcels", "Weight", "Shipment number", "Postal Code",
]
available = [c for c in display_cols if c in filtered.columns]
st.write(filtered[available].to_html(escape=False, index=False), unsafe_allow_html=True)
