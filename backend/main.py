"""
LabCheck - Automated Lab Report Generation & Error-Checking Backend
--------------------------------------------------------------------
Run with:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, field_validator
from typing import List, Optional, Dict
import pandas as pd
import io
import json
import sqlite3
import datetime
import uuid
import traceback

from reference_ranges import REFERENCE_RANGES, find_test_profile
from report_pdf import build_pdf_report

app = FastAPI(title="LabCheck API", version="0.2.0")

# Allow the frontend (served from anywhere while you're building/testing)
# to call this API. Tighten this to your real domain before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Without this, an unhandled crash anywhere in the app skips FastAPI's
# normal CORS handling, and the browser reports a confusing "CORS
# blocked" error instead of the real problem. This guarantees every
# response - success, handled error, or unexpected crash - always
# carries the CORS header, and always tells us the real error message.
@app.exception_handler(Exception)
async def catch_all_exceptions(request: Request, exc: Exception):
    print("UNHANDLED ERROR:", traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {type(exc).__name__}: {exc}"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


# ============================================================
# PERSISTENT STORAGE (SQLite)
# ------------------------------------------------------------
# NOTE: on Render's free tier, this file lives on the container's local
# disk, which is wiped on every redeploy/restart. That's fine for
# demoing delta-checks during development, but before real lab data is
# involved, this needs a Render persistent disk or a real hosted
# database (Postgres) so history survives restarts.
# ============================================================
DB_PATH = "labcheck.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patient_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            patient_name TEXT,
            test_key TEXT NOT NULL,
            test_name_raw TEXT,
            value REAL NOT NULL,
            unit TEXT,
            status TEXT,
            report_id TEXT,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ---- In-memory stores (fine for these - they're session/workflow state,
# not permanent records) ----
REPORTS = {}
PENDING_UPLOADS = {}   # upload_id -> raw pandas DataFrame, waiting for a column mapping
INSTRUMENT_PROFILES = {}  # signature (sorted tuple of raw column names) -> {canonical_field: raw_column}

# Fields we specifically understand and use in the app's logic.
REQUIRED_FIELDS = ["patient_id", "test_name", "value"]
OPTIONAL_FIELDS = ["patient_name", "unit", "sex", "age"]
# Any other columns in the raw file (Address, Doctor, Ward, whatever a
# specific lab happens to include) are preserved too, in "extra" - the
# app doesn't need to understand them to avoid throwing that data away.


def _signature(raw_columns):
    """A fingerprint of a file's raw column names, used to recognise
    'I've seen this exact instrument export shape before'."""
    return tuple(sorted(str(c).strip() for c in raw_columns))


def _apply_mapping(df: pd.DataFrame, mapping: dict) -> list:
    """mapping: {canonical_field: raw_column_name}. Returns clean row dicts,
    including an 'extra' dict of any raw columns not explicitly mapped."""
    out = pd.DataFrame()
    for field in REQUIRED_FIELDS:
        if field not in mapping or mapping[field] not in df.columns:
            raise HTTPException(400, f"Mapping is missing required field: {field}")
        out[field] = df[mapping[field]]
    for field in OPTIONAL_FIELDS:
        if field in mapping and mapping[field] in df.columns:
            out[field] = df[mapping[field]]
        else:
            out[field] = ""
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["value"])

    mapped_raw_columns = set(mapping.values())
    leftover_columns = [c for c in df.columns if c not in mapped_raw_columns]

    rows = out[REQUIRED_FIELDS + OPTIONAL_FIELDS].fillna("").to_dict(orient="records")
    for i, row in enumerate(rows):
        original_index = out.index[i]
        extra = {}
        for col in leftover_columns:
            val = df.at[original_index, col]
            if pd.notna(val) and str(val).strip() != "":
                extra[col] = str(val)
        row["extra"] = extra
    return rows


class ResultRow(BaseModel):
    patient_id: str
    patient_name: Optional[str] = ""
    test_name: str
    value: float
    unit: Optional[str] = ""
    sex: Optional[str] = ""
    age: Optional[str] = ""
    extra: Optional[Dict[str, str]] = {}

    # Excel/CSV data comes in with mixed types (Age might be read as a
    # number, not text) - accept anything reasonable here and convert
    # it to a plain string, instead of rejecting the whole request.
    @field_validator("patient_name", "unit", "sex", "age", mode="before")
    @classmethod
    def _coerce_to_string(cls, v):
        if v is None:
            return ""
        return str(v)


class AnalyzeRequest(BaseModel):
    rows: List[ResultRow]


class SignOffRequest(BaseModel):
    report_id: str
    signed_by: str
    role: str  # "pathologist"


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.datetime.utcnow().isoformat()}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Accepts a raw instrument export (CSV or XLSX) in ANY column layout,
    with ANY extra columns a specific lab happens to include.

    Flow:
      1. Try to auto-detect columns via common aliases (Patient ID, ID, Sample ID, etc).
      2. If this exact file shape was mapped before (a saved instrument profile),
         apply that mapping automatically — no need to ask again.
      3. Otherwise, return needs_mapping=true with the raw columns + a preview,
         so the frontend can show a "which column is which?" screen once.
    """
    name = file.filename.lower()
    content = await file.read()

    try:
        if name.endswith(".csv"):
            df_raw = pd.read_csv(io.BytesIO(content))
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            df_raw = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(400, "Unsupported file type. Upload .csv or .xlsx")
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    df_raw.columns = [str(c).strip() for c in df_raw.columns]
    signature = _signature(df_raw.columns)

    # 2. Have we already learned this exact instrument's format?
    if signature in INSTRUMENT_PROFILES:
        mapping = INSTRUMENT_PROFILES[signature]
        rows = _apply_mapping(df_raw, mapping)
        return {"rows": rows, "row_count": len(rows), "auto_mapped": True}

    # 1. Try auto-detecting via common aliases
    normalized = {c: c.strip().lower().replace(" ", "_") for c in df_raw.columns}
    alias_map = {}
    for orig, norm in normalized.items():
        if norm in ("patient_id", "patientid", "id", "sample_id"):
            alias_map["patient_id"] = orig
        elif norm in ("patient_name", "name"):
            alias_map["patient_name"] = orig
        elif norm in ("test_name", "test", "parameter", "analyte"):
            alias_map["test_name"] = orig
        elif norm in ("value", "result", "reading"):
            alias_map["value"] = orig
        elif norm in ("unit", "units"):
            alias_map["unit"] = orig
        elif norm in ("sex", "gender", "m/f", "sex/gender"):
            alias_map["sex"] = orig
        elif norm in ("age", "age(years)", "patient_age", "years"):
            alias_map["age"] = orig

    if all(f in alias_map for f in REQUIRED_FIELDS):
        rows = _apply_mapping(df_raw, alias_map)
        return {"rows": rows, "row_count": len(rows), "auto_mapped": True}

    # 3. Couldn't figure it out — ask the human to map it, once.
    upload_id = str(uuid.uuid4())[:8]
    PENDING_UPLOADS[upload_id] = df_raw
    preview = df_raw.head(5).fillna("").astype(str).to_dict(orient="records")
    return {
        "needs_mapping": True,
        "upload_id": upload_id,
        "columns": list(df_raw.columns),
        "preview": preview,
        "required_fields": REQUIRED_FIELDS,
        "optional_fields": OPTIONAL_FIELDS,
    }


class MappingRequest(BaseModel):
    mapping: dict  # {"patient_id": "raw col name", "test_name": "...", "value": "...", ...}
    save_profile: bool = False


@app.post("/api/upload/{upload_id}/map")
def apply_mapping(upload_id: str, payload: MappingRequest):
    df_raw = PENDING_UPLOADS.get(upload_id)
    if df_raw is None:
        raise HTTPException(404, "Upload not found or already processed — please re-upload the file.")

    rows = _apply_mapping(df_raw, payload.mapping)

    if payload.save_profile:
        signature = _signature(df_raw.columns)
        INSTRUMENT_PROFILES[signature] = payload.mapping

    del PENDING_UPLOADS[upload_id]
    return {"rows": rows, "row_count": len(rows), "auto_mapped": False}


@app.get("/api/profiles")
def list_profiles():
    return {
        "count": len(INSTRUMENT_PROFILES),
        "profiles": [
            {"columns": list(sig), "mapping": mapping}
            for sig, mapping in INSTRUMENT_PROFILES.items()
        ],
    }


# Delta significant-change threshold: a change bigger than this percentage
# from the patient's own last result gets flagged, in addition to the
# normal range check. 20% is a reasonable general-purpose starting point;
# a real lab's SOP may want this tuned per-test.
DELTA_THRESHOLD_PERCENT = 20.0


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest):
    """
    Runs each row against the reference-range engine and flags:
      - NORMAL
      - OUT_OF_RANGE  (outside normal reference range; sex-specific
                        range used automatically when Sex is provided)
      - CRITICAL       (outside physiologically plausible bounds -> likely
                         transcription/unit/decimal error, needs immediate check)
      - UNKNOWN_TEST    (no reference profile found — still shown, not blocked)

    Also runs a DELTA CHECK: if this patient (by Patient ID) has a
    previous recorded value for this same test, compares the new value
    against it and flags a significant change - a real clinical safety
    practice, independent of whether the new value itself looks "normal".
    """
    report_id = str(uuid.uuid4())[:8]
    conn = get_db()
    results = []

    for row in payload.rows:
        profile = find_test_profile(row.test_name, sex=row.sex)
        entry = {
            "patient_id": row.patient_id,
            "patient_name": row.patient_name,
            "test_name": row.test_name,
            "value": row.value,
            "unit": row.unit or (profile["unit"] if profile else ""),
            "sex": row.sex,
            "age": row.age,
            "extra": row.extra or {},
            "status": "UNKNOWN_TEST",
            "reason": "No reference profile configured for this test.",
            "normal_range": None,
            "used_sex_specific_range": False,
            "delta": None,
        }

        if profile:
            lo, hi = profile["normal_low"], profile["normal_high"]
            plo, phi = profile["plausible_low"], profile["plausible_high"]
            entry["normal_range"] = f"{lo}–{hi} {profile['unit']}"
            entry["used_sex_specific_range"] = profile.get("used_sex_specific_range", False)

            if row.value < plo or row.value > phi:
                entry["status"] = "CRITICAL"
                entry["reason"] = (
                    f"{row.value} {profile['unit']} is outside physiologically "
                    f"plausible bounds ({plo}–{phi}). Likely a decimal, unit, "
                    f"or transcription error — verify before reporting."
                )
            elif row.value < lo or row.value > hi:
                entry["status"] = "OUT_OF_RANGE"
                entry["reason"] = (
                    f"{row.value} {profile['unit']} falls outside the normal "
                    f"reference range ({lo}–{hi} {profile['unit']})."
                )
            else:
                entry["status"] = "NORMAL"
                entry["reason"] = "Within normal reference range."

        # ---- Delta check against this patient's own history ----
        test_key = profile["name"] if profile else row.test_name.strip().lower()
        prev = conn.execute(
            """SELECT value, unit, recorded_at FROM patient_history
               WHERE patient_id = ? AND test_key = ?
               ORDER BY recorded_at DESC LIMIT 1""",
            (row.patient_id, test_key),
        ).fetchone()

        if prev is not None and prev["value"] != 0:
            prev_value = prev["value"]
            change_percent = ((row.value - prev_value) / abs(prev_value)) * 100
            entry["delta"] = {
                "previous_value": prev_value,
                "previous_unit": prev["unit"],
                "previous_recorded_at": prev["recorded_at"],
                "change_percent": round(change_percent, 1),
                "flag": "SIGNIFICANT_CHANGE" if abs(change_percent) >= DELTA_THRESHOLD_PERCENT else "STABLE",
            }

        # Record this new result into history for future delta checks
        conn.execute(
            """INSERT INTO patient_history
               (patient_id, patient_name, test_key, test_name_raw, value, unit, status, report_id, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row.patient_id, row.patient_name, test_key, row.test_name, row.value,
             entry["unit"], entry["status"], report_id, datetime.datetime.utcnow().isoformat()),
        )

        results.append(entry)

    conn.commit()
    conn.close()

    summary = {
        "total": len(results),
        "normal": sum(1 for r in results if r["status"] == "NORMAL"),
        "out_of_range": sum(1 for r in results if r["status"] == "OUT_OF_RANGE"),
        "critical": sum(1 for r in results if r["status"] == "CRITICAL"),
        "unknown": sum(1 for r in results if r["status"] == "UNKNOWN_TEST"),
        "significant_changes": sum(1 for r in results if r["delta"] and r["delta"]["flag"] == "SIGNIFICANT_CHANGE"),
    }

    REPORTS[report_id] = {
        "results": results,
        "summary": summary,
        "created_at": datetime.datetime.utcnow().isoformat(),
        "signed_by": None,
        "signed_role": None,
        "signed_at": None,
    }

    return {"report_id": report_id, "summary": summary, "results": results}


@app.post("/api/sign-off")
def sign_off(payload: SignOffRequest):
    report = REPORTS.get(payload.report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    report["signed_by"] = payload.signed_by
    report["signed_role"] = payload.role
    report["signed_at"] = datetime.datetime.utcnow().isoformat()
    return {"status": "signed", "report_id": payload.report_id}


@app.get("/api/report/{report_id}/pdf")
def get_pdf(report_id: str):
    report = REPORTS.get(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    pdf_bytes = build_pdf_report(report_id, report)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=lab_report_{report_id}.pdf"},
    )


@app.get("/api/report/{report_id}")
def get_report(report_id: str):
    report = REPORTS.get(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return {"report_id": report_id, **report}


@app.get("/api/patient/{patient_id}/history")
def patient_history(patient_id: str):
    """Full test history for one patient - useful for a future 'patient
    trend view' feature, and handy for debugging delta checks."""
    conn = get_db()
    rows = conn.execute(
        """SELECT patient_name, test_key, test_name_raw, value, unit, status, recorded_at
           FROM patient_history WHERE patient_id = ? ORDER BY recorded_at DESC""",
        (patient_id,),
    ).fetchall()
    conn.close()
    return {"patient_id": patient_id, "history": [dict(r) for r in rows]}
