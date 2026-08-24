# Printflo Delivery Portal

A Streamlit dashboard for Printflo to track DHL dispatches across their account. CSV manifests exported from the DHL dashboard live in `data/`; a scheduled GitHub Action polls the DHL Shipment Tracking API for undelivered parcels and commits fresh statuses to `printflo_tracking_cache.json`, which the app overlays on top of the CSV data.

## How it works

```
DHL DashboardSummary CSV exports  ──►  data/*.csv
                                          │
GitHub Action (4x daily, Mon–Fri) ──►  sync_dhl.py ──► printflo_tracking_cache.json
                                          │                       │
                                          ▼                       ▼
                                   app.py (Streamlit) — merged view with live statuses
```

The DHL API is fed **individual shipment numbers** taken from the CSVs — DHL's public tracking API cannot list shipments by account number, so the CSV exports remain the source of truth for *which* parcels exist. The API then keeps their *statuses* current.

## Deploying

### 1. Push to GitHub

Create a repo (private recommended — the CSVs contain recipient names and postcodes) and push this folder's contents.

### 2. Add the DHL API key as a GitHub secret

Repo → Settings → Secrets and variables → Actions → New repository secret:

- Name: `DHL_API_KEY`
- Value: your key from [developer.dhl.com](https://developer.dhl.com) (Shipment Tracking – Unified API)

> **Important:** the previous version of this project had the API key hardcoded in `sync_dhl.py`. That key should be treated as compromised — rotate it in the DHL developer portal and use the new one here.

The sync workflow (`.github/workflows/dhl_sync.yml`) runs hourly from 06:00 to 20:00 UTC, Monday–Saturday, and can be run manually from the Actions tab.

### 3. Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and create a new app from the repo (`app.py` as the entry point).
2. In the app's **Settings → Secrets**, add the access code for Printflo:

   ```toml
   APP_PASSWORD = "choose-a-code-for-printflo"
   ```

3. Share the app URL and access code with Printflo.

If `APP_PASSWORD` is not set, the app runs without a login gate (useful for local development).

Note: the app reads the cache committed to the repo, and Streamlit Cloud redeploys automatically when the Action pushes a new cache — so statuses on the dashboard refresh shortly after each sync.

## Adding new dispatch data

Export the latest DashboardSummary (or campaign) CSV from the DHL dashboard and commit it to `data/`. Rules:

- Files named `DashboardSummary*.csv` are grouped under the campaign **Standard Dispatch**.
- Any other filename becomes its own campaign name (e.g. `GLP SPK Christmas.csv` → campaign "GLP SPK Christmas").
- Duplicate shipment numbers across files are de-duplicated (latest file wins), so overlapping exports are fine.

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Optional: `export DHL_API_KEY=...` and `python sync_dhl.py` to refresh statuses locally.

## DHL API notes & rate limits

- Endpoint used: `GET https://api-eu.dhl.com/track/shipments?trackingNumber=...` (Shipment Tracking – Unified).
- Current allowance: **10,000 calls/day**. The hourly Mon–Sat schedule uses roughly `active parcels x 15` calls per day (~2,500 at 170 active parcels), so there is headroom up to ~650 active parcels. The sync only polls parcels not yet marked delivered, and never re-polls delivered ones.
- Calls are paced at ~0.3s apart with exponential backoff on 429/503, so a burst-limit change on DHL's side degrades gracefully rather than failing.
- Each sync also captures the **actual delivery scan timestamp**, DHL's **live ETA**, and the **last scan event/location** per parcel — these power the "Delivered Today / This Week" metrics and the "Last Seen" column.
- For account-level automation (no CSVs), DHL eCommerce UK offers a customer API — see the main project notes.

## Future: Inkari integration

The data model here (shipment number → status, campaign, recipient, postcode) is deliberately simple so it can be lifted into Inkari later: replace `data/*.csv` with a database table keyed by store/account, keep the same sync job, and scope each store's login to its own shipments.
