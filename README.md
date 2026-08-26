# LabCheck — Automated Lab Report Generation & Error-Checking

Tested and working end-to-end: file upload → reference-range checking →
flagged results → pathologist sign-off → downloadable PDF.

```
labreport/
├── backend/            Python (FastAPI) — parsing, flagging engine, PDF generation
│   ├── main.py
│   ├── reference_ranges.py
│   ├── report_pdf.py
│   └── requirements.txt
└── frontend/            Installable web app (PWA) — works on phone, laptop, PC
    ├── index.html
    ├── app.js
    ├── manifest.json
    ├── sw.js
    └── icon.svg
```

## 1. Run it on your own laptop (10 minutes)

**Install Python 3.10+** from python.org if you don't have it already.

**Step 1 — start the backend:**
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Leave this terminal running. Visit `http://127.0.0.1:8000/api/health` in a
browser — you should see `{"status":"ok", ...}`.

**Step 2 — start the frontend (a second terminal):**
```bash
cd frontend
python3 -m http.server 8080
```
Open `http://127.0.0.1:8080/index.html` in your browser. Try uploading a
CSV with columns: `Patient ID, Patient Name, Test Name, Value, Unit`
(Test Name can be Hemoglobin, Glucose, WBC, Creatinine, Sodium, Potassium,
etc. — see `backend/reference_ranges.py` for the full list, and add more
there as needed).

## 2. Install it like a real app on your phone/laptop

Once both servers are running (or deployed — see step 3), open the
frontend URL on your phone or laptop's Chrome/Edge browser:
- **Android/Desktop Chrome:** tap the "Install" icon in the address bar,
  or menu → "Install app". It now appears as its own icon, opens full-screen.
- **iPhone Safari:** Share → "Add to Home Screen".

This is what makes it "an actual app" without going through the Play
Store / App Store review process — ideal for your SIH timeline.

## 3. Put it online so anyone (judges, a real lab) can use it

You need the backend and frontend hosted somewhere permanent instead of
your laptop:

- **Backend** → [Render.com](https://render.com) or
  [Railway.app](https://railway.app): free tier, connect your GitHub
  repo, it auto-detects `requirements.txt` and runs
  `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- **Frontend** → [Vercel](https://vercel.com) or
  [Netlify](https://netlify.com): drag-and-drop the `frontend` folder,
  done in under a minute.
- After deploying, open `frontend/app.js` and change the first line's
  `API_BASE` fallback to your live backend URL (e.g.
  `https://labcheck-api.onrender.com`), or set
  `window.LABCHECK_API_BASE` before `app.js` loads in `index.html`.

## 4. Before you show this at SIH — do these

1. Add 15–20 more tests to `reference_ranges.py` (renal panel, lipid
   panel, liver panel, thyroid panel) — judges will test with a variety.
2. Swap the in-memory `REPORTS` dict in `main.py` for a real database
   (SQLite is enough for a demo: `pip install sqlmodel`) so reports
   survive a server restart.
3. Add basic login (even a simple hardcoded-per-role password for the
   demo) so "Technician / Pathologist / Admin" isn't just a client-side
   toggle.
4. Prepare 2–3 sample CSVs (clean data, one with an out-of-range value,
   one with an obviously-impossible value like Hemoglobin = 50) so your
   live demo is reliable and repeatable.
5. Have your one-line pitch ready: *"Labs lose hours a day manually
   formatting reports and cross-checking values by eye — LabCheck
   automates both, and catches the transcription errors before they
   reach a patient."*

## Notes on the reference ranges

The values in `reference_ranges.py` are general teaching values, **not**
clinically validated. Before any real-world use, get every range
reviewed and signed off by a qualified pathologist and cite the source
standard (e.g., your lab's NABL-accredited SOP).
