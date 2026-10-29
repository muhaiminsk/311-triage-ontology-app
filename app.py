"""311 Triage: an ontology-driven operational app (objects, links, actions, functions)
on DuckDB + FastAPI, with a local-LLM function. Run: uvicorn app:app --port 8000"""
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

import llm
import ontology

DB_PATH = os.environ.get("DB_PATH", "data.duckdb")
DATA_DIR = Path(os.environ.get("DATA_DIR", "out"))
HERE = Path(__file__).parent

con = duckdb.connect(DB_PATH)
_lock = threading.Lock()


def rows(sql, params=()):
    with _lock:
        cur = con.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def execute(sql, params=()):
    with _lock:
        con.execute(sql, list(params))


def init_db():
    """Load each dataset CSV into a table once. Edits then persist in the DB file."""
    for obj in ontology.OBJECT_TYPES.values():
        name = obj["table"]
        exists = rows("select count(*) as n from information_schema.tables where table_name = ?", [name])[0]["n"]
        if exists:
            continue
        path = DATA_DIR / f"{name}.csv"
        if not path.exists():
            raise RuntimeError(f"Missing {path}. Run prep_311.py first (or make_sample_data.py for a quick test).")
        df = pd.read_csv(path, dtype=str).fillna("")
        with _lock:
            con.register("df_tmp", df)
            con.execute(f"create table {name} as select * from df_tmp")
            con.unregister("df_tmp")
    execute("create table if not exists audit_log (ts varchar, action varchar, object_key varchar, "
            "old_value varchar, new_value varchar, note varchar)")


init_db()
app = FastAPI(title="311 Triage")


# ---------- Objects ----------
SORTABLE = {"unique_key", "request_text", "borough", "incident_zip", "agency",
            "assigned_agency", "status", "created_date"}


@app.get("/api/requests")
def list_requests(borough: Optional[str] = None, q: Optional[str] = None,
                  status: Optional[str] = None, zip_code: Optional[str] = Query(None, alias="zip"),
                  open_only: bool = False, sort: str = "created_date", order: str = "desc",
                  limit: int = Query(100, le=500), offset: int = 0):
    if sort not in SORTABLE:
        raise HTTPException(400, f"Cannot sort by '{sort}'")
    if order not in ("asc", "desc"):
        raise HTTPException(400, "order must be 'asc' or 'desc'")
    where, params = [], []
    if borough:
        where.append("borough = ?"); params.append(borough)
    if q:
        where.append("lower(request_text) like ?"); params.append(f"%{q.lower()}%")
    if status:
        where.append("status = ?"); params.append(status)
    if zip_code:
        where.append("incident_zip = ?"); params.append(zip_code)
    if open_only:
        where.append("lower(status) <> 'closed'")
    w = ("where " + " and ".join(where)) if where else ""
    total = rows(f"select count(*) as n from requests {w}", params)[0]["n"]
    items = rows(f"select * from requests {w} order by {sort} {order}, unique_key limit ? offset ?",
                 params + [limit, offset])
    return {"total": total, "items": items}


@app.get("/api/requests/{key}")
def get_request(key: str):
    r = rows("select * from requests where unique_key = ?", [key])
    if not r:
        raise HTTPException(404, "Request not found")
    r = r[0]
    agency = rows("select * from agencies where agency = ?", [r["agency"]])
    assigned = rows("select * from agencies where agency = ?", [r["assigned_agency"]])
    hood = rows("select * from neighborhoods where incident_zip = ?", [r["incident_zip"]])
    return {"request": r, "agency": agency[0] if agency else None,
            "assigned_agency": assigned[0] if assigned else None,
            "neighborhood": hood[0] if hood else None}


@app.get("/api/facets")
def facets():
    return {
        "boroughs": [r["borough"] for r in rows("select distinct borough from requests where borough <> '' order by 1")],
        "statuses": [r["status"] for r in rows("select distinct status from requests order by 1")],
        "agencies": rows("select agency, agency_name from agencies order by agency"),
    }


@app.get("/api/ontology")
def get_ontology():
    return {"object_types": ontology.OBJECT_TYPES, "link_types": ontology.LINK_TYPES,
            "action_types": ontology.ACTION_TYPES, "functions": ontology.FUNCTIONS}


# ---------- Action ----------
class ReassignBody(BaseModel):
    unique_key: str
    new_agency: str
    note: str = ""


@app.post("/api/actions/reassign-request")
def reassign_request(body: ReassignBody):
    req = rows("select * from requests where unique_key = ?", [body.unique_key])
    if not req:
        raise HTTPException(404, "Request not found")
    if not rows("select 1 from agencies where agency = ?", [body.new_agency]):
        raise HTTPException(400, f"Unknown agency '{body.new_agency}'")
    old = req[0]["assigned_agency"]
    if old == body.new_agency:
        raise HTTPException(400, "Request is already assigned to that agency")
    execute("update requests set assigned_agency = ?, reassign_note = ? where unique_key = ?",
            [body.new_agency, body.note, body.unique_key])
    execute("insert into audit_log values (?, ?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc).isoformat(timespec="seconds"), "reassign_request",
             body.unique_key, old, body.new_agency, body.note])
    return get_request(body.unique_key)


@app.get("/api/audit")
def audit(limit: int = 50):
    return rows("select * from audit_log order by ts desc limit ?", [limit])


# ---------- Function ----------
SYSTEM = ("You summarize NYC 311 service requests for one ZIP code. "
          "Use ONLY the data provided. Do not use outside knowledge.")
INSTRUCTIONS = ("Write exactly 5 lines. Line 1: the total number of open requests. "
                "Lines 2 to 4: the three most common complaint types, each with its count, most common first. "
                "Line 5: one sentence on anything unusual. If you are unsure of a count, say so.")


def build_prompt(zip_code: str, mode: str):
    open_where = "incident_zip = ? and lower(status) <> 'closed'"
    total = rows(f"select count(*) as n from requests where {open_where}", [zip_code])[0]["n"]
    if mode == "raw":
        sample = rows(f"select complaint_type, descriptor from requests where {open_where} "
                      "order by created_date desc limit 200", [zip_code])
        data = "\n".join(f"- {r['complaint_type']}: {r['descriptor']}" for r in sample)
        header = f"Open requests (showing {len(sample)} of {total}):"
    elif mode == "raw_types":
        # Fairer baseline: one complaint type per line, no descriptors, so the model
        # counts at the same granularity as the ground truth.
        sample = rows(f"select complaint_type from requests where {open_where} "
                      "order by created_date desc limit 200", [zip_code])
        data = "\n".join(f"- {r['complaint_type']}" for r in sample)
        header = f"Open requests, one complaint type per line (showing {len(sample)} of {total}):"
    else:
        counts = rows(f"select complaint_type, count(*) as n from requests where {open_where} "
                      "group by 1 order by n desc limit 10", [zip_code])
        data = "\n".join(f"- {r['complaint_type']}: {r['n']}" for r in counts)
        header = (f"Total open requests: {total}\nOpen requests by complaint type "
                  "(already counted, use these numbers exactly):")
    return f"ZIP code: {zip_code}\n{header}\n{data}\n\n{INSTRUCTIONS}", total


class SummarizeBody(BaseModel):
    zip: str
    mode: str = "counts"  # "raw", "raw_types" or "counts"


@app.post("/api/functions/summarize-neighborhood")
def summarize_neighborhood(body: SummarizeBody):
    if body.mode not in ("raw", "raw_types", "counts"):
        raise HTTPException(400, "mode must be 'raw', 'raw_types' or 'counts'")
    if not rows("select 1 from neighborhoods where incident_zip = ?", [body.zip]):
        raise HTTPException(404, "Unknown ZIP")
    prompt, total = build_prompt(body.zip, body.mode)
    try:
        text = llm.chat(SYSTEM, prompt)
    except llm.LLMError as e:
        raise HTTPException(503, str(e))
    return {"zip": body.zip, "mode": body.mode, "open_requests": total,
            "backend": llm.BACKEND, "summary": text}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")
