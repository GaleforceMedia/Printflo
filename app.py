import streamlit as st
import pandas as pd
import glob
import os
import json
import time
import urllib.request
import urllib.error
import re
from datetime import datetime

# Set up page layout
st.set_page_config(page_title="Printflo Delivery Portal", layout="wide")

# --- DHL API CONFIGURATION ---
DHL_API_KEY = "i043Uc7SRU6Zxs2GfxGk4QmWa4SxA6Ac"
CACHE_FILE = "printflo_tracking_cache.json"
CACHE_EXPIRY = 14400 # 4 hours in seconds (Saves your 250 daily limit)

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache_data):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f)
    except Exception:
        pass

def fetch_dhl_status_safe(tracking_numbers):
    if not tracking_numbers:
        return {}, "No numbers to check"
        
    tracking_str = ",".join(tracking_numbers)
    url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_str}"
    
    headers = {
        "DHL-API-Key": DHL_API_KEY,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                live_updates = {}
                for shipment in data.get('shipments', []):
                    trk = str(shipment.get('id'))
                    dhl_status = shipment.get('status', {}).get('statusCode', '').lower()
                    
                    if dhl_status == 'delivered':
                        live_updates[trk] = 'Delivered'
                    elif dhl_status == 'transit':
                        live_updates[trk] = 'In Transit'
                    else:
                        live_updates[trk] = 'Exception'
                return live_updates, "Success"
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        return {}, f"HTTP {e.code} on [{tracking_str}]: {error_body}"
    except Exception as e:
        return {}, f"Connection Error on [{tracking_str}]: {str(e)}"

# --- Custom CSS (Printflo Branding & Clean Layout) ---
printflo_css = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    html, body, [class*="css"]  { 
        font-family: 'Inter', sans-serif !important; 
        background-color: #F8F9FA !important; 
        color: #111827 !important; 
    }
    
    #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
    
    h1 { 
        font-weight: 700 !important; 
        letter-spacing: -0.5px; 
        color: #111827 !important;
        border-bottom: 3px solid #174A8C; 
        padding-bottom: 10px; 
        margin-bottom: 5px !important; 
    }
    
    [data-testid="stMetricValue"] { 
        font-size: 2.2rem !important; 
        font-weight: 700 !important; 
        color: #174A8C !important; 
    }
    
    table { 
        border-collapse: collapse !important; 
        width: 100% !important; 
        font-size: 0.9rem !important; 
        background-color: #FFFFFF !important;
        border-radius: 8px !important;
        overflow: hidden !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05) !important;
    }
    
    th { 
        background-color: #F3F4F6 !important; 
        font-weight: 600 !important; 
        border-bottom: 2px solid #E5E7EB !important; 
        text-transform: uppercase; 
        font-size: 0.75rem; 
        color: #4B5563 !important; 
        text-align: left !important; 
        padding: 12px 16px !important;
    }
    
    td { 
        background-color: #FFFFFF !important; 
        border-bottom: 1px solid #E5E7EB !important; 
        vertical-align: middle !important; 
        text-align: left !important; 
        padding: 12px 16px !important;
    }
</style>
"""
st.markdown(printflo_css, unsafe_allow_html=True)

# --- Header Section (Left-Aligned) ---
col1, col2 = st.columns([1, 5])
with col1:
    try:
        # Pushed down slightly to align better with the text
        st.markdown("<div style='margin-top: 15px;'>", unsafe_allow_html=True)
        st.image("printflo-logo.png", width=180)
        st.markdown("</div>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass
with col2:
    st.title("Printflo Delivery Portal")
    st.markdown("<p style='color: #6B7280; font-size: 1.1rem; margin-top: 0px; margin-bottom: 30px;'>Track and manage network deliveries.</p>", unsafe_allow_html=True)

# 1. LOAD CSV DATA 
@st.cache_data(ttl=300) 
def load_csv_data():
    all_files = sorted(glob.glob("*.csv"))
    if not all_files:
        return pd.DataFrame()
        
    df_list = []
    for file in all_files:
        try:
            temp_df = pd.read_csv(file, dtype={'Shipment number': str})
            base_name = os.path.basename(file).replace('.csv', '')
            temp_df['Campaign'] = 'Standard Dispatch' if 'dashboard summary' in base_name.lower().replace('dashboardsummary', 'dashboard summary') else base_name
            df_list.append(temp_df)
        except Exception:
            continue
            
    if not df_list:
        return pd.DataFrame()
        
    master_df = pd.concat(df_list, ignore_index=True)
    master_df.columns = master_df.columns.str.strip()
    
    if 'Shipment number' in master_df.columns:
        master_df['Shipment number'] = master_df['Shipment number'].astype(str).str.replace(r'\.0$', '', regex=True)
        master_df['Shipment number'] = master_df['Shipment number'].apply(lambda x: re.sub(r'[^A-Za-z0-9]', '', str(x)))
        master_df = master_df.drop_duplicates(subset=['Shipment number'], keep='last')
        
    if 'Dispatch date' in master_df.columns:
        master_df['Dispatch Date Parsed'] = pd.to_datetime(master_df['Dispatch date'], format='%d/%m/%Y', errors='coerce')
        
    if 'Customer reference' in master_df.columns:
        master_df.loc[master_df['Campaign'] != 'Standard Dispatch', 'Customer reference'] = "-"
        
    return master_df

# 2. SYNC WITH DHL
def sync_dhl_api(master_df):
    api_stats = {"vault_hits": 0, "api_calls": 0, "errors": [], "updated_rows": 0}
    
    if master_df.empty or 'Status' not in master_df.columns or 'Shipment number' not in master_df.columns:
        return master_df, api_stats
        
    cache = load_cache()
    current_time = time.time()
    
    active_mask = master_df['Status'].astype(str).str.strip().str.lower() != 'delivered'
    active_parcels_raw = master_df[active_mask]['Shipment number'].unique().tolist()
    
    active_parcels = [trk for trk in active_parcels_raw if len(trk) >= 10 and trk.lower() != 'nan']
    
    needs_update = []
    for trk in active_parcels:
        cached_info = cache.get(trk)
        if not cached_info:
            needs_update.append(trk)
        elif current_time - cached_info.get('timestamp', 0) > CACHE_EXPIRY:
            if cached_info.get('status') != 'Delivered':
                needs_update.append(trk)
        else:
            api_stats["vault_hits"] += 1
                
    if needs_update:
        chunk_size = 1 
        for i in range(0, len(needs_update), chunk_size):
            chunk = needs_update[i:i + chunk_size]
            api_stats["api_calls"] += 1
            
            updates, error_msg = fetch_dhl_status_safe(chunk)
            
            if error_msg != "Success":
                api_stats["errors"].append(error_msg)
            
            for trk, status in updates.items():
                cache[trk] = {'status': status, 'timestamp': current_time}
                api_stats["updated_rows"] += 1
            
            time.sleep(1.5) 
        
        save_cache(cache)
        
    master_df['Status'] = master_df.apply(
        lambda row: cache.get(str(row['Shipment number']), {}).get('status', row['Status']), axis=1
    )
    
    return master_df, api_stats

try:
    df_raw = load_csv_data()

    if df_raw.empty:
        st.warning("No tracking data available for Printflo. Please upload the latest CSV manifest.")
        st.stop()

    with st.spinner("Synchronizing with DHL Network..."):
        df, stats = sync_dhl_api(df_raw)

    # --- Live Metric Calculations ---
    today = pd.Timestamp.now('Europe/London').normalize().tz_localize(None)
    yesterday = today - pd.Timedelta(days=1)
    start_of_week = today - pd.Timedelta(days=today.dayofweek)
    start_of_month = today.replace(day=1)
    
    df['Clean Status'] = df['Status'].astype(str).str.strip().str.lower()
    in_transit = len(df[df['Clean Status'].isin(['in transit', 'out for delivery'])])
    delivered_df = df[df['Clean Status'] == 'delivered']
    
    if 'Dispatch Date Parsed' in df.columns:
        delivered_df_dates = delivered_df['Dispatch Date Parsed'].dt.tz_localize(None)
        delivered_today = len(delivered_df[delivered_df_dates.isin([today, yesterday])])
        delivered_week = len(delivered_df[delivered_df_dates >= start_of_week])
        delivered_month = len(delivered_df[delivered_df_dates >= start_of_month])
    else:
        delivered_today, delivered_week, delivered_month = 0, 0, 0

    # --- Display Top Metrics ---
    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(label="In Transit", value=in_transit)
    col2.metric(label="Delivered Today", value=delivered_today)
    col3.metric(label="Delivered This Week", value=delivered_week)
    col4.metric(label="Delivered This Month", value=delivered_month)

    # --- API DIAGNOSTICS READOUT ---
    timestamp = pd.Timestamp.now('Europe/London').strftime("%A, %d %B %Y at %I:%M %p")
    diag_color = "#28a745" if not stats["errors"] else "#dc3545"
    diag_text = f"Data synced: {timestamp} | Vault Hits: {stats['vault_hits']} | Live API Pings: {stats['api_calls']}"
    
    if stats["errors"]:
        diag_text += f" | ⚠️ API ERROR: {stats['errors'][0]}"
        
    st.markdown(f"<div style='text-align: center; color: {diag_color}; font-size: 0.85rem; margin-top: 10px; margin-bottom: 20px; font-weight: 600;'>{diag_text}</div>", unsafe_allow_html=True)
    st.markdown("<hr><br>", unsafe_allow_html=True)

    # --- Side-by-Side Filtering ---
    col_filter1, col_filter2, col_filter3 = st.columns(3)
    
    unique_campaigns = sorted(df['Campaign'].dropna().unique()) if 'Campaign' in df.columns else []
        
    search_postcode = col_filter1.text_input("SEARCH POSTCODE", placeholder="e.g. B78 3JD")
    search_ref = col_filter2.text_input("SEARCH CUSTOMER REF.")
    selected_campaign = col_filter3.selectbox("SEARCH CAMPAIGN", ["All Campaigns"] + list(unique_campaigns))

    filtered_df = df.copy()

    if search_postcode.strip():
        filtered_df = filtered_df[filtered_df['Postal Code'].astype(str).str.contains(search_postcode.strip(), case=False, na=False)]
    if search_ref.strip() and 'Customer reference' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['Customer reference'].astype(str).str.contains(search_ref.strip(), case=False, na=False)]
    if selected_campaign != "All Campaigns":
        filtered_df = filtered_df[filtered_df['Campaign'] == selected_campaign]

    # --- Formatting Blank Dates & ETAs for Delivered Parcels ---
    def format_delivered_blanks(row, col_name):
        val = str(row[col_name]) if pd.notna(row[col_name]) else ""
        if row['Clean Status'] == 'delivered':
            return '<span style="background-color: #D4EDDA; color: #155724; padding: 6px 12px; border-radius: 20px; font-size: 0.8rem; font-weight: 600;">-</span>'
        return val

    if 'Delivery due date' in filtered_df.columns:
        filtered_df['Delivery due date'] = filtered_df.apply(lambda r: format_delivered_blanks(r, 'Delivery due date'), axis=1)
    if 'ETA' in filtered_df.columns:
        filtered_df['ETA'] = filtered_df.apply(lambda r: format_delivered_blanks(r, 'ETA'), axis=1)

    # --- Dynamic Carrier Link Generation ---
    def make_clickable(shipment_num):
        if pd.isna(shipment_num) or str(shipment_num).strip().lower() == 'nan' or len(str(shipment_num)) < 5:
            return ""
        clean_num = str(shipment_num).strip()
        url = f"https://www.dhl.com/en/express/tracking.html?AWB={clean_num}"
        return f'<a href="{url}" target="_blank" style="color: #174A8C; text-decoration: underline; font-weight: 600;">Track Order</a>'

    filtered_df['Tracking Link'] = filtered_df['Shipment number'].apply(make_clickable)

    # --- Colour Coded Status Badges ---
    def color_status(status_val):
        val_lower = str(status_val).strip().lower()
        bg_color = "#E0E0E0" 
        text_color = "#333333"
        if val_lower == 'delivered':
            bg_color, text_color = "#D4EDDA", "#155724"
        elif val_lower in ['in transit', 'out for delivery']:
            bg_color, text_color = "#FFF3CD", "#856404"
        elif 'exception' in val_lower or 'delay' in val_lower:
            bg_color, text_color = "#F8D7DA", "#721C24"
        return f'<span style="background-color: {bg_color}; color: {text_color}; padding: 6px 12px; border-radius: 20px; font-weight: 600; font-size: 0.8rem; text-transform: uppercase;">{status_val}</span>'

    filtered_df['Status'] = filtered_df['Status'].apply(color_status)

    display_cols = [
        'Campaign', 'Customer reference', 'Business/Recipient name', 'Status', 
        'Delivery due date', 'ETA', 'Tracking Link', 'Number of parcels', 
        'Weight', 'Shipment number', 'Postal Code'
    ]
    available_cols = [col for col in display_cols if col in filtered_df.columns]

    st.write(filtered_df[available_cols].to_html(escape=False, index=False), unsafe_allow_html=True)

except Exception as e:
    st.error(f"An error occurred: {e}")
