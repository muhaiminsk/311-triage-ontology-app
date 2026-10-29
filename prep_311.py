"""Pull a recent slice of NYC 311 data and shape it into Foundry-ready CSVs.

Usage:  python prep_311.py            (needs: pandas, internet access)
Output: out/requests.csv, out/agencies.csv, out/neighborhoods.csv, out/eval_cases.csv
"""
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pandas as pd

DAYS_BACK = 30
LIMIT = 50000
BASE = "https://data.cityofnewyork.us/resource/erm2-nwe9.csv"
COLS = ["unique_key", "created_date", "closed_date", "agency", "agency_name",
        "complaint_type", "descriptor", "status", "borough", "incident_zip"]

since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT00:00:00")
params = {
    "$select": ",".join(COLS),
    "$where": f"created_date > '{since}' AND incident_zip IS NOT NULL",
    "$order": "created_date DESC",
    "$limit": LIMIT,
}
url = f"{BASE}?{urlencode(params)}"
print("Fetching", url)
df = pd.read_csv(url, dtype={"incident_zip": str, "unique_key": str})
print("Raw rows:", len(df))

# Clean
df = df.dropna(subset=["agency", "incident_zip", "complaint_type"])
df["incident_zip"] = df["incident_zip"].str.extract(r"(\d{5})")[0]
df = df.dropna(subset=["incident_zip"])
df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce")
df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce")
df["descriptor"] = df["descriptor"].fillna("")
df["request_text"] = (df["complaint_type"] + ": " + df["descriptor"]).str.strip(": ")
df["hours_to_close"] = ((df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600).round(1)
# Property that the Foundry "Reassign Request" action will edit. Starts equal to agency.
df["assigned_agency"] = df["agency"]
df["reassign_note"] = ""
df = df.drop_duplicates(subset=["unique_key"])
print("Clean rows:", len(df))

os.makedirs("out", exist_ok=True)
df.to_csv("out/requests.csv", index=False)

agencies = df[["agency", "agency_name"]].drop_duplicates(subset=["agency"])
agencies.to_csv("out/agencies.csv", index=False)

nb = (df.groupby("incident_zip")
        .agg(borough=("borough", lambda s: s.mode().iat[0] if not s.mode().empty else ""),
             request_count=("unique_key", "count"))
        .reset_index())
nb.to_csv("out/neighborhoods.csv", index=False)

# Ground truth for evaluating LLM summaries: top complaint types per zip, computed from the data.
open_df = df[df["status"].str.lower() != "closed"]
rows = []
top_zips = nb.sort_values("request_count", ascending=False)["incident_zip"].head(40)
for z in top_zips.sample(min(20, len(top_zips)), random_state=7):
    sub = open_df[open_df["incident_zip"] == z]
    vc = sub["complaint_type"].value_counts().head(3)
    row = {"incident_zip": z, "open_total": len(sub)}
    for i, (k, v) in enumerate(vc.items(), 1):
        row[f"top{i}_type"], row[f"top{i}_count"] = k, int(v)
    rows.append(row)
pd.DataFrame(rows).to_csv("out/eval_cases.csv", index=False)
print("Wrote out/requests.csv, agencies.csv, neighborhoods.csv, eval_cases.csv")
