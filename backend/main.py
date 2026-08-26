"""
LabCheck - Automated Lab Report Generation & Error-Checking Backend
--------------------------------------------------------------------
Run with:  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import io
import datetime
import uuid

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

# ---- In-memory store (swap for a real DB — Postgres/SQLite — before production) ----
REPORTS = {}


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
    Accepts a raw instrument export (CSV or XLSX).
    Expected (flexible) columns, case-insensitive:
      Patient ID | Patient Name | Test Name | Value | Unit
    Returns parsed rows so the frontend can preview before analysis.
    """
    name = file.filename.lower()
    content = await file.read()

    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(400, "Unsupported file type. Upload .csv or .xlsx")
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    # Normalize column names (instruments export inconsistent headers)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    col_map = {}
    for col in df.columns:
        if col in ("patient_id", "patientid", "id", "sample_id"):
            col_map[col] = "patient_id"
        elif col in ("patient_name", "name"):
            col_map[col] = "patient_name"
        elif col in ("test_name", "test", "parameter", "analyte"):
            col_map[col] = "test_name"
        elif col in ("value", "result", "reading"):
            col_map[col] = "value"
        elif col in ("unit", "units"):
            col_map[col] = "unit"
    df = df.rename(columns=col_map)

    required = {"patient_id", "test_name", "value"}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(
            400,
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}",
        )

    if "patient_name" not in df.columns:
        df["patient_name"] = ""
    if "unit" not in df.columns:
        df["unit"] = ""

    df = df[["patient_id", "patient_name", "test_name", "value", "unit"]].fillna("")
    rows = df.to_dict(orient="records")
    return {"rows": rows, "row_count": len(rows)}


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
