// ---- CONFIG ----
// Point this at your backend. While developing locally it's the FastAPI
// server started with `uvicorn main:app --reload --port 8000`. Once
// deployed, this points at the live Render URL instead.
const API_BASE = window.LABCHECK_API_BASE || "https://labcheckk.onrender.com";

// ---- STATE ----
let state = {
  role: "technician",
  uploadedRows: null,
  reportId: null,
  summary: null,
  results: null,
  signed: null,
  pendingMapping: null, // { upload_id, columns, preview, required_fields, optional_fields }
};

const app = document.getElementById("app");

// ---- ROLE SWITCHER ----
document.getElementById("roleNav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-role]");
  if (!btn) return;
  document.querySelectorAll("nav.roles button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  state.role = btn.dataset.role;
  render();
});

// ---- RENDER ----
function render() {
  app.innerHTML = "";
  if (state.role === "technician") renderTechnician();
  else if (state.role === "pathologist") renderPathologist();
  else renderAdmin();
}

function renderTechnician() {
  const card = document.createElement("div");
  card.className = "card fade-in";
  card.innerHTML = `
    <h2 style="margin-top:0">Upload instrument export</h2>
    <p class="muted">CSV or Excel with columns: Patient ID, Patient Name, Test Name, Value, Unit.</p>
    <div class="dropzone" id="dropzone">
      <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#3EC1B3" stroke-width="1.6">
        <path d="M12 16V4M12 4l-4 4M12 4l4 4" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <div><strong>Drop file here</strong> or click to browse</div>
      <input type="file" id="fileInput" accept=".csv,.xlsx,.xls" />
    </div>
    <div id="uploadStatus" class="muted" style="margin-top:10px;"></div>
  `;
  app.appendChild(card);

  const dz = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("drag");
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length) handleFile(e.target.files[0]);
  });

  if (state.pendingMapping) renderMappingCard();
  if (state.results) renderResultsCard(false);
}

async function handleFile(file) {
  const statusEl = document.getElementById("uploadStatus");
  statusEl.textContent = "Uploading & parsing…";
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch(`${API_BASE}/api/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail || "Upload failed");
    const data = await res.json();

    if (data.needs_mapping) {
      state.pendingMapping = data;
      statusEl.textContent = "This file's layout hasn't been seen before — match the columns below.";
      render();
      return;
    }

    state.uploadedRows = data.rows;
    statusEl.textContent = data.auto_mapped
      ? `Parsed ${data.row_count} rows (format recognized automatically). Running checks…`
      : `Parsed ${data.row_count} rows. Running checks…`;
    await runAnalysis();
    statusEl.textContent = `✓ ${data.row_count} rows analyzed.`;
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
}

function renderMappingCard() {
  const pm = state.pendingMapping;
  const card = document.createElement("div");
  card.className = "card fade-in";
  const allFields = [...pm.required_fields, ...pm.optional_fields];
  const fieldLabels = {
    patient_id: "Patient ID *", patient_name: "Patient Name",
    test_name: "Test Name *", value: "Value *", unit: "Unit",
  };

  card.innerHTML = `
    <h2 style="margin-top:0">Match your columns</h2>
    <p class="muted">We don't recognize this file's column headings yet. Tell us which
    column in <em>your</em> file matches each field below — you'll only need to do this once
    for this format.</p>
    <div id="mappingRows" style="display:flex; flex-direction:column; gap:10px; margin:16px 0;">
      ${allFields.map(field => `
        <div style="display:flex; align-items:center; gap:12px;">
          <label style="width:150px; font-size:13.5px; font-weight:600;">${fieldLabels[field] || field}</label>
          <select class="text-input" data-field="${field}" style="flex:1;">
            <option value="">— not present —</option>
            ${pm.columns.map(c => `<option value="${c}">${c}</option>`).join("")}
          </select>
        </div>
      `).join("")}
    </div>
    <p class="muted" style="margin-bottom:6px;">Preview of your file's first rows:</p>
    <div style="overflow-x:auto;">
      <table>
        <thead><tr>${pm.columns.map(c => `<th>${c}</th>`).join("")}</tr></thead>
        <tbody>
          ${pm.preview.map(row => `<tr>${pm.columns.map(c => `<td>${row[c] ?? ""}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
    <div style="display:flex; align-items:center; gap:10px; margin-top:16px;">
      <label style="display:flex; align-items:center; gap:6px; font-size:13.5px;">
        <input type="checkbox" id="saveProfileCheck" checked />
        Remember this format for next time
      </label>
    </div>
    <div style="margin-top:14px; display:flex; gap:10px;">
      <button class="primary" id="confirmMappingBtn">Confirm & analyze</button>
      <button class="ghost" id="cancelMappingBtn">Cancel</button>
    </div>
    <div id="mappingStatus" class="muted" style="margin-top:8px;"></div>
  `;
  app.appendChild(card);

  // Best-effort pre-fill: guess a matching column by loose name similarity
  allFields.forEach(field => {
    const select = card.querySelector(`select[data-field="${field}"]`);
    const guess = pm.columns.find(c => {
      const norm = c.toLowerCase().replace(/[^a-z]/g, "");
      const target = field.replace(/_/g, "");
      return norm.includes(target) || target.includes(norm);
    });
    if (guess) select.value = guess;
  });

  document.getElementById("cancelMappingBtn").addEventListener("click", () => {
    state.pendingMapping = null;
    render();
  });

  document.getElementById("confirmMappingBtn").addEventListener("click", async () => {
    const mapping = {};
    card.querySelectorAll("select[data-field]").forEach(sel => {
      if (sel.value) mapping[sel.dataset.field] = sel.value;
    });
    for (const req of pm.required_fields) {
      if (!mapping[req]) {
        document.getElementById("mappingStatus").textContent = `Please select a column for ${fieldLabels[req] || req}.`;
        return;
      }
    }
    const saveProfile = document.getElementById("saveProfileCheck").checked;
    document.getElementById("mappingStatus").textContent = "Applying…";
    try {
      const res = await fetch(`${API_BASE}/api/upload/${pm.upload_id}/map`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapping, save_profile: saveProfile }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Mapping failed");
      const data = await res.json();
      state.pendingMapping = null;
      state.uploadedRows = data.rows;
      await runAnalysis();
    } catch (err) {
      document.getElementById("mappingStatus").textContent = `Error: ${err.message}`;
    }
  });
}

async function runAnalysis() {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows: state.uploadedRows }),
  });
  const data = await res.json();
  state.reportId = data.report_id;
  state.summary = data.summary;
  state.results = data.results;
  state.signed = null;
  render();
}

function renderResultsCard(showSignoff) {
  const card = document.createElement("div");
  card.className = "card fade-in";
  const s = state.summary;
  card.innerHTML = `
    <h2 style="margin-top:0">Results — Report #${state.reportId}</h2>
    <div class="summary-row">
      <div class="chip"><span class="n">${s.total}</span> total</div>
      <div class="chip normal"><span class="n">${s.normal}</span> normal</div>
      <div class="chip flag"><span class="n">${s.out_of_range}</span> out of range</div>
      <div class="chip critical"><span class="n">${s.critical}</span> critical</div>
    </div>
    <div class="table-scroll">
    <table>
      <thead><tr><th>Patient</th><th>Test</th><th>Value</th><th>Normal range</th><th>Status</th></tr></thead>
      <tbody>
        ${state.results.map(r => `
          <tr class="${r.status}">
            <td>${r.patient_name || r.patient_id}<div class="muted mono">${r.patient_id}</div></td>
            <td>${r.test_name}</td>
            <td class="val">${r.value} ${r.unit}</td>
            <td class="muted">${r.normal_range || "—"}</td>
            <td><span class="status-pill ${r.status}">${r.status.replace("_"," ")}</span></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
    </div>
    <div style="margin-top:16px; display:flex; gap:10px;">
      <button class="ghost" id="downloadBtn">Download PDF</button>
    </div>
  `;
  app.appendChild(card);
  document.getElementById("downloadBtn").addEventListener("click", () => {
    window.open(`${API_BASE}/api/report/${state.reportId}/pdf`, "_blank");
  });
}

function renderPathologist() {
  if (!state.results) {
    app.innerHTML = `<div class="card empty">No report loaded yet. Ask the technician to upload a batch first, or switch to Technician view to upload one now.</div>`;
    return;
  }
  renderResultsCard(true);

  const card = document.createElement("div");
  card.className = "card fade-in";
  if (state.signed) {
    card.innerHTML = `<div class="signed-badge">✓ Signed off by ${state.signed.signed_by} — ready to release.</div>`;
  } else {
    card.innerHTML = `
      <h3 style="margin-top:0">Review & sign off</h3>
      <p class="muted">Confirm flagged values have been checked before releasing this report.</p>
      <div class="signoff-box">
        <input class="text-input" id="signName" placeholder="Your name" />
        <button class="primary" id="signBtn">Sign off report</button>
      </div>
    `;
  }
  app.appendChild(card);

  const signBtn = document.getElementById("signBtn");
  if (signBtn) {
    signBtn.addEventListener("click", async () => {
      const name = document.getElementById("signName").value.trim();
      if (!name) return;
      const res = await fetch(`${API_BASE}/api/sign-off`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_id: state.reportId, signed_by: name, role: "pathologist" }),
      });
      if (res.ok) {
        state.signed = { signed_by: name };
        render();
      }
    });
  }
}

function renderAdmin() {
  const card = document.createElement("div");
  card.className = "card fade-in";
  if (!state.summary) {
    card.innerHTML = `<div class="empty">No activity yet today.</div>`;
    app.appendChild(card);
    return;
  }
  const s = state.summary;
  card.innerHTML = `
    <h2 style="margin-top:0">Today's overview</h2>
    <div class="summary-row">
      <div class="chip"><span class="n">${s.total}</span> results processed</div>
      <div class="chip flag"><span class="n">${s.out_of_range}</span> flagged</div>
      <div class="chip critical"><span class="n">${s.critical}</span> critical</div>
    </div>
    <p class="muted" style="margin-top:14px;">Report #${state.reportId} — ${state.signed ? `signed off by ${state.signed.signed_by}` : "awaiting pathologist sign-off"}.</p>
  `;
  app.appendChild(card);
}

// ---- INIT ----
render();

// ---- PWA install support ----
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}
