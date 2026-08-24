"""Background DHL tracking sync for the Printflo Delivery Portal.

Reads every CSV in data/ (and the repo root), collects shipment numbers that
are not yet delivered, queries the DHL Shipment Tracking (Unified) API for
each one, and writes the latest statuses to printflo_tracking_cache.json.

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


def fetch_with_backoff(tracking_num: str, max_retries: int = 4):
    """Return (status_code, description) from DHL, or (None, None) on failure."""
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
                    for shipment in data.get("shipments", []):
                        status = shipment.get("status", {})
                        return (
                            str(status.get("statusCode", "")).lower(),
                            status.get("description", ""),
                        )
                return (None, None)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):  # rate limit / spike arrest
                print(f"  Rate-limited on {tracking_num}; backing off {delay:.0f}s (attempt {attempt + 1})")
                time.sleep(delay)
                delay *= 2
            elif e.code == 404:
                print(f"  {tracking_num}: not found (may not be in DHL system yet)")
                return (None, None)
            else:
                print(f"  HTTP {e.code} on {tracking_num}")
                return (None, None)
        except Exception as e:
            print(f"  Connection error on {tracking_num}: {e}")
            return (None, None)
    return (None, None)


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
    now = time.time()

    for trk in active:
        if cache.get(trk, {}).get("status") == "Delivered":
            continue  # already final — never re-poll

        print(f"Checking {trk}...")
        status_code, description = fetch_with_backoff(trk)

        if status_code:
            display = STATUS_MAP.get(status_code)
            if display is None:
                # 'unknown' or anything unexpected — keep the CSV's status
                print(f"  Unrecognised statusCode '{status_code}', leaving CSV status in place")
            else:
                cache[trk] = {
                    "status": display,
                    "description": description,
                    "timestamp": now,
                }

        time.sleep(1.0)  # stay under DHL spike-arrest limits

    save_cache(cache)
    print("Sync complete. Cache updated.")


if __name__ == "__main__":
    run_sync()
