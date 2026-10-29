"""Score the summarize-neighborhood function against ground truth computed from the data.

Run the server first (uvicorn app:app --port 8000, with Ollama running), then:
    python eval.py                  # runs all three designs
    python eval.py raw_types        # runs only the named design(s) and merges into eval_results.csv

Designs, all scored on the same ZIPs:
  raw        LLM sees up to 200 rows as 'type: descriptor' and must count (naive baseline)
  raw_types  LLM sees up to 200 rows as 'type' only and must count (fairer baseline)
  counts     SQL counts first, LLM only writes the summary

Results are also split by whether the baseline could see every open request (open_total <= 200).
"""
import os
import re
import sys

import pandas as pd
import requests

API = os.environ.get("API", "http://localhost:8000")
ALL_MODES = ["raw", "raw_types", "counts"]
modes = sys.argv[1:] or ALL_MODES
for m in modes:
    if m not in ALL_MODES:
        sys.exit(f"Unknown mode '{m}'. Choose from {ALL_MODES}")

cases = pd.read_csv("out/eval_cases.csv", dtype=str).fillna("")
new_rows = []

for mode in modes:
    for _, c in cases.iterrows():
        try:
            r = requests.post(f"{API}/api/functions/summarize-neighborhood",
                              json={"zip": c["incident_zip"], "mode": mode}, timeout=600)
        except requests.RequestException as e:
            sys.exit(f"Cannot reach the server at {API}. Start it first. ({e})")
        if r.status_code != 200:
            sys.exit(f"Server error for ZIP {c['incident_zip']}: {r.status_code} {r.text}")
        out = r.json()["summary"]
        if out.startswith("[STUB"):
            sys.exit("LLM_BACKEND is 'stub'. Restart the server with Ollama running to get real scores.")
        flat = out.replace(",", "")
        type_ok = c["top1_type"].lower() in out.lower()
        count_ok = re.search(rf"(?<!\d){re.escape(c['top1_count'])}(?!\d)", flat) is not None
        new_rows.append({"zip": c["incident_zip"], "mode": mode, "open_total": int(c["open_total"]),
                         "top1_type": c["top1_type"], "top1_count": c["top1_count"],
                         "type_ok": type_ok, "count_ok": count_ok, "summary": out})
        print(f"{mode:9} {c['incident_zip']}  type_ok={type_ok}  count_ok={count_ok}", flush=True)

new = pd.DataFrame(new_rows)
if os.path.exists("eval_results.csv"):
    old = pd.read_csv("eval_results.csv", dtype={"zip": str})
    old = old[~old["mode"].isin(modes)]
    df = pd.concat([old, new], ignore_index=True)
else:
    df = new
df.to_csv("eval_results.csv", index=False)

print("\n=== RESULTS ===")
for mode in ALL_MODES:
    d = df[df["mode"] == mode]
    if d.empty:
        continue
    seen = d[d["open_total"] <= 200]
    trunc = d[d["open_total"] > 200]
    print(f"{mode:9} all {len(d)} ZIPs: type {int(d.type_ok.sum())}/{len(d)}, count {int(d.count_ok.sum())}/{len(d)}"
          f"  |  saw every request ({len(seen)} ZIPs): count {int(seen.count_ok.sum())}/{len(seen)}"
          f"  |  truncated ({len(trunc)} ZIPs): count {int(trunc.count_ok.sum())}/{len(trunc)}")
print("Per-ZIP detail saved to eval_results.csv")
