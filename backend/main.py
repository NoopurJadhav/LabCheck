"""
LabCheck - Automated Lab Report Generation & Error-Checking Backend
--------------------------------------------------------------------
Run with:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import io
import datetime
import uuid
import traceback

from reference_ranges import REFERENCE_RANGES, find_test_profile
from report_pdf import build_pdf_report

app = FastAPI(title="LabCheck API", version="0.1.0")

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

# ---- In-memory stores (swap for a real DB — Postgres/SQLite — before production) ----
REPORTS = {}
PENDING_UPLOADS = {}   # upload_id -> raw pandas DataFrame, waiting for a column mapping
INSTRUMENT_PROFILES = {}  # signature (sorted tuple of raw column names) -> {canonical_field: raw_column}

REQUIRED_FIELDS = ["patient_id", "test_name", "value"]
OPTIONAL_FIELDS = ["patient_name", "unit"]


def _signature(raw_columns):
    """A fingerprint of a file's raw column names, used to recognise
    'I've seen this exact instrument export shape before'."""
    return tuple(sorted(str(c).strip() for c in raw_columns))


def _apply_mapping(df: pd.DataFrame, mapping: dict) -> list:
    """mapping: {canonical_field: raw_column_name}. Returns clean row dicts."""
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
    out = out[REQUIRED_FIELDS + OPTIONAL_FIELDS].fillna("")
    return out.to_dict(orient="records")


class ResultRow(BaseModel):
    patient_id: str
    patient_name: Optional[str] = ""
    test_name: str
    value: float
    unit: Optional[str] = ""


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
    Accepts a raw instrument export (CSV or XLSX) in ANY column layout.

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


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest):
    """
    Runs each row against the reference-range engine and flags:
      - NORMAL
      - OUT_OF_RANGE  (outside normal reference range for age/general adult)
      - CRITICAL       (outside physiologically plausible bounds -> likely
                         transcription/unit/decimal error, needs immediate check)
      - UNKNOWN_TEST    (no reference profile found — still shown, not blocked)
    """
    results = []
    for row in payload.rows:
        profile = find_test_profile(row.test_name)
        entry = {
            "patient_id": row.patient_id,
            "patient_name": row.patient_name,
            "test_name": row.test_name,
            "value": row.value,
            "unit": row.unit or (profile["unit"] if profile else ""),
            "status": "UNKNOWN_TEST",
            "reason": "No reference profile configured for this test.",
            "normal_range": None,
        }

        if profile:
            lo, hi = profile["normal_low"], profile["normal_high"]
            plo, phi = profile["plausible_low"], profile["plausible_high"]
            entry["normal_range"] = f"{lo}–{hi} {profile['unit']}"

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

        results.append(entry)

    summary = {
        "total": len(results),
        "normal": sum(1 for r in results if r["status"] == "NORMAL"),
        "out_of_range": sum(1 for r in results if r["status"] == "OUT_OF_RANGE"),
        "critical": sum(1 for r in results if r["status"] == "CRITICAL"),
        "unknown": sum(1 for r in results if r["status"] == "UNKNOWN_TEST"),
    }

    report_id = str(uuid.uuid4())[:8]
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
