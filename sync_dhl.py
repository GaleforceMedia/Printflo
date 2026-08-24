"""Background DHL tracking sync for the Printflo Delivery Portal.

Reads every CSV in data/ (and the repo root), collects shipment numbers that
are not yet delivered, queries the DHL Shipment Tracking (Unified) API for
each one, and writes the latest statuses to printflo_tracking_cache.json.

For each parcel the cache stores:
  status        - display status (Delivered / In Transit / Awaiting Collection / Exception)
  description   - DHL's human-readable status line
  delivered_at  - ISO timestamp of the actual delivery scan (when delivered)
  eta           - DHL's estimated delivery timestamp (while in transit)
  last_event    - latest scan: what happened and where
  updated_at    - when this entry was last refreshed

Run by the GitHub Action in .github/workflows/dhl_sync.yml. Requires the
DHL_API_KEY environment variable (set it as a GitHub Actions secret).
"""

import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

DHL_API_KEY = os.environ.get("DHL_API_KEY", "")
CACHE_FILE = "printflo_tracking_cache.json"
DATA_GLOBS = ["data/*.csv", "*.csv"]

# Pacing between successful calls. 10k/day allowance; keep a small gap to
# stay clear of per-second spike arrests, and back off exponentially on 429s.
CALL_INTERVAL = 0.3

# DHL unified statusCode -> display status shown on the dashboard
STATUS_MAP = {
    "delivered": "Delivered",
    "transit": "In Transit",
    "pre-transit": "Awaiting Collection",
    "failure": "Exception",
}


def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    cache["_meta"] = {"last_sync": datetime.now(timezone.utc).isoformat()}
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_active_tracking_numbers() -> list:
    files = []
    for pattern in DATA_GLOBS:
        files.extend(sorted(glob.glob(pattern)))
    if not files:
        return []

    frames = []
    for file in files:
        try:
            frames.append(pd.read_csv(file, dtype={"Shipment number": str}))
        except Exception:
            continue
    if not frames:
        return []

    df = pd.concat(frames, ignore_index=True)
    df.columns = df.columns.str.strip()
    if "Shipment number" not in df.columns or "Status" not in df.columns:
        return []

    df["Shipment number"] = (
        df["Shipment number"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .apply(lambda x: re.sub(r"[^A-Za-z0-9]", "", str(x)))
    )
    df = df.drop_duplicates(subset=["Shipment number"], keep="last")

    # Only poll DHL for parcels the CSV doesn't already show as delivered
    active = df[df["Status"].astype(str).str.strip().str.lower() != "delivered"]
    numbers = active["Shipment number"].unique().tolist()
    return [t for t in numbers if len(t) >= 10 and t.lower() != "nan"]


def parse_shipment(shipment: dict) -> dict:
    """Extract the fields we keep from one DHL shipment payload."""
    status = shipment.get("status", {}) or {}
    status_code = str(status.get("statusCode", "")).lower()

    entry = {
        "status_code": status_code,
        "description": status.get("description", "") or status.get("status", ""),
        "eta": shipment.get("estimatedTimeOfDelivery", ""),
        "delivered_at": "",
        "last_event": "",
    }

    # Latest scan event: what happened, where
    events = shipment.get("events") or []
    latest = events[0] if events else status
    if latest:
        desc = latest.get("description", "") or latest.get("status", "")
        loc = (
            ((latest.get("location") or {}).get("address") or {}).get("addressLocality", "")
        )
        entry["last_event"] = f"{desc} — {loc}".strip(" —") if (desc or loc) else ""

    if status_code == "delivered":
        # Prefer the delivery event's own timestamp, fall back to status timestamp
        delivered_ts = ""
        for ev in events:
            if str(ev.get("statusCode", "")).lower() == "delivered":
                delivered_ts = ev.get("timestamp", "")
                break
        entry["delivered_at"] = delivered_ts or status.get("timestamp", "")

    return entry


def fetch_with_backoff(tracking_num: str, max_retries: int = 4):
    """Return a parsed shipment dict from DHL, or None on failure."""
    url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_num}"
    headers = {
        "DHL-API-Key": DHL_API_KEY,
        "Accept": "application/json",
        "User-Agent": "PrintfloDeliveryPortal/1.0",
    }
    delay = 2.0
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    shipments = data.get("shipments", [])
                    if shipments:
                        return parse_shipment(shipments[0])
                return None
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):  # rate limit / spike arrest
                print(f"  Rate-limited on {tracking_num}; backing off {delay:.0f}s (attempt {attempt + 1})")
                time.sleep(delay)
                delay *= 2
            elif e.code == 404:
                print(f"  {tracking_num}: not found (may not be in DHL system yet)")
                return None
            else:
                print(f"  HTTP {e.code} on {tracking_num}")
                return None
        except Exception as e:
            print(f"  Connection error on {tracking_num}: {e}")
            return None
    return None


def run_sync() -> None:
    if not DHL_API_KEY:
        print("ERROR: DHL_API_KEY environment variable is not set.")
        print("Add it as a repository secret and pass it through in the workflow.")
        sys.exit(1)

    print("Initiating Printflo background sync...")
    active = get_active_tracking_numbers()
    if not active:
        print("No active tracking numbers found. Exiting.")
        return

    print(f"Found {len(active)} active parcels to check.")
    cache = load_cache()
    now_iso = datetime.now(timezone.utc).isoformat()
    checked = updated = 0

    for trk in active:
        if cache.get(trk, {}).get("status") == "Delivered":
            continue  # already final — never re-poll

        checked += 1
        result = fetch_with_backoff(trk)

        if result:
            display = STATUS_MAP.get(result["status_code"])
            if display is None:
                # 'unknown' or anything unexpected — keep the CSV's status
                print(f"  {trk}: unrecognised statusCode '{result['status_code']}', keeping CSV status")
            else:
                cache[trk] = {
                    "status": display,
                    "description": result["description"],
                    "delivered_at": result["delivered_at"],
                    "eta": result["eta"],
                    "last_event": result["last_event"],
                    "updated_at": now_iso,
                }
                updated += 1

        time.sleep(CALL_INTERVAL)

    save_cache(cache)
    print(f"Sync complete. Checked {checked}, updated {updated}. Cache written.")


if __name__ == "__main__":
    run_sync()
