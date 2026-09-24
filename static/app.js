// ============================================================================
// präsentation_ai — Frontend-Logik
// Reines Vanilla JS, keine Build-Tools nötig — läuft direkt im Browser.
// ============================================================================

const $ = (sel) => document.querySelector(sel);

const el = {
  dropzone: $("#dropzone"),
  dropzoneTitle: $("#dropzoneTitle"),
  dropzoneSub: $("#dropzoneSub"),
  fileInput: $("#fileInput"),
  skipVideo: $("#skipVideo"),
  skipEmotion: $("#skipEmotion"),
  startBtn: $("#startBtn"),
  uploadError: $("#uploadError"),

  stageUpload: $("#stageUpload"),
  stageProgress: $("#stageProgress"),
  stageDashboard: $("#stageDashboard"),

  progressTitle: $("#progressTitle"),
  progressSub: $("#progressSub"),
  steplist: $("#steplist"),
  backToUpload: $("#backToUpload"),

  dashboardSub: $("#dashboardSub"),
  gesamtScoreValue: $("#gesamtScoreValue"),
  gesamtReportBtn: $("#gesamtReportBtn"),
  modalitaetBars: $("#modalitaetBars"),
  tipsWrap: $("#tipsWrap"),
  moduleGrid: $("#moduleGrid"),
  konsistenzWrap: $("#konsistenzWrap"),
  newAnalysisBtn2: $("#newAnalysisBtn2"),

  stageDateien: $("#stageDateien"),
  dateiFilter: $("#dateiFilter"),
  dateiListe: $("#dateiListe"),

  historyList: $("#historyList"),
  historyEmpty: $("#historyEmpty"),

  drawerOverlay: $("#drawerOverlay"),
  drawer: $("#drawer"),
  drawerTitle: $("#drawerTitle"),
  drawerEyebrow: $("#drawerEyebrow"),
  drawerRendered: $("#drawerRendered"),
  drawerDlBar: $("#drawerDlBar"),
  drawerDlTxt: $("#drawerDlTxt"),
  drawerDlPdf: $("#drawerDlPdf"),
  drawerDlWord: $("#drawerDlWord"),
  drawerClose: $("#drawerClose"),
  drawerToggle: $("#drawerToggle"),
  toggleKurz: $("#toggleKurz"),
  toggleDetail: $("#toggleDetail"),
  printArea: $("#print-area"),
};

let PIPELINE_SCHRITTE = [];
let selectedFile = null;
let pollTimer = null;
let _elapsedTimer = null;
let _elapsedStart = null;

// Roher Report-Text für Download-Zwecke gespeichert
let _drawerRawText = "";
let _drawerLabel = "";
// Aktuell geöffnetes Modul + Ebene (für Toggle)
let _drawerModul = "";
let _drawerEbene = "kurz";

const MODUL_LABELS = {
  fuellwoerter:          "Füllwörter",
  sprechtempo:           "Sprechtempo",
  pausen:                "Pausen",
  sprechfluss:           "Sprechfluss",
  lautstaerke:           "Lautstärke",
  pitch_variation:       "Tonhöhen-Variation",
  emotionale_variation:  "Emotionale Variation",
  video:                 "Körpersprache",
};

const MODUL_DESC = {
  fuellwoerter:          "Häufigkeit von Ähm, Halt, Eigentlich usw.",
  sprechtempo:           "Silben pro Sekunde, Variation, Verlangsamung",
  pausen:                "Anzahl und Länge der Sprechpausen",
  sprechfluss:           "Wortwiederholungen und Satzabbrüche",
  lautstaerke:           "Lautstärkevariation und Betonung",
  pitch_variation:       "Tonhöhenvariation in Halbtönen",
  emotionale_variation:  "Emotionserkennung pro Segment",
  video:                 "Gestik, Körperhaltung, Ausdrucksstärke",
};

const MODALITAET_LABELS = {
  inhalt_sprache: "Inhalt / Sprache",
  prosodie:       "Prosodie",
  video:          "Video / Körpersprache",
};

// ============================================================ INIT

async function init() {
  const res = await fetch("/api/pipeline-schritte");
  PIPELINE_SCHRITTE = await res.json();
  renderStepListSkeleton();
  bindEvents();
  loadHistory();
}
init();

// ============================================================ NAV

const STAGES = ["stageUpload", "stageProgress", "stageDashboard", "stageDateien"];

function zeigeStage(id) {
  STAGES.forEach((s) => {
    const el2 = document.getElementById(s);
    if (el2) el2.hidden = s !== id;
  });
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    const view = btn.dataset.view;
    if (view === "verlauf") {
      loadHistory();
    } else if (view === "dateien") {
      zeigeStage("stageDateien");
      loadDateien();
    } else if (view === "analyse") {
      zeigeStage("stageUpload");
    }
  });
});

// ============================================================ UPLOAD

function bindEvents() {
  el.dropzone.addEventListener("click", () => el.fileInput.click());
  el.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") el.fileInput.click();
  });
  el.dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    el.dropzone.classList.add("is-dragover");
  });
  el.dropzone.addEventListener("dragleave", () => el.dropzone.classList.remove("is-dragover"));
  el.dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    el.dropzone.classList.remove("is-dragover");
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });
  el.fileInput.addEventListener("change", (e) => {
    if (e.target.files.length) handleFile(e.target.files[0]);
  });

  el.startBtn.addEventListener("click", startAnalysis);
  el.backToUpload.addEventListener("click", resetToUpload);
  el.newAnalysisBtn2.addEventListener("click", resetToUpload);

  el.drawerClose.addEventListener("click", closeDrawer);
  el.drawerOverlay.addEventListener("click", closeDrawer);

  // Kurz / Detail Toggle
  [el.toggleKurz, el.toggleDetail].forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("is-active")) return;
      const ebene = btn.dataset.ebene;
      el.toggleKurz.classList.toggle("is-active", ebene === "kurz");
      el.toggleDetail.classList.toggle("is-active", ebene === "detail");
      _ladeReportEbene(_drawerModul, _drawerLabel, ebene);
    });
  });

  // Download-Buttons
  el.drawerDlPdf.addEventListener("click", downloadAsPdf);
  el.drawerDlWord.addEventListener("click", downloadAsWord);

  // Gesamtreport-Button im Dashboard
  if (el.gesamtReportBtn) {
    el.gesamtReportBtn.addEventListener("click", () => openDrawer("gesamt", "Gesamtreport"));
  }
}

function handleFile(file) {
  selectedFile = file;
  el.dropzone.classList.add("has-file");
  el.dropzoneTitle.textContent = file.name;
  const mb = (file.size / (1024 * 1024)).toFixed(1);
  el.dropzoneSub.textContent = `${mb} MB — bereit zum Hochladen`;
  el.startBtn.disabled = false;
  el.uploadError.hidden = true;

  // File-Info-Chip aktualisieren
  let chip = document.getElementById("fileInfoChip");
  if (!chip) {
    chip = document.createElement("div");
    chip.id = "fileInfoChip";
    chip.className = "file-info-chip";
    el.dropzone.insertAdjacentElement("afterend", chip);
  }
  chip.innerHTML = `
    <span class="file-info-name">${escapeHtml(file.name)}</span>
    <span class="file-info-size">${mb} MB</span>
    <button class="file-info-clear" type="button" aria-label="Datei entfernen">×</button>`;
  chip.hidden = false;
  chip.querySelector(".file-info-clear").addEventListener("click", () => {
    resetToUpload();
  });
}

async function startAnalysis() {
  if (!selectedFile) return;
  el.startBtn.disabled = true;
  el.uploadError.hidden = true;

  const form = new FormData();
  form.append("video", selectedFile);
  form.append("skip_video", el.skipVideo.checked);
  form.append("skip_emotion", el.skipEmotion.checked);

  try {
    const res = await fetch("/api/analyze", { method: "POST", body: form });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || "Start fehlgeschlagen.";
      throw new Error(detail);
    }
    const data = await res.json();
    showProgressStage(selectedFile.name);
    pollStatus(data.job_id);
  } catch (err) {
    el.uploadError.textContent = err.message;
    el.uploadError.hidden = false;
    el.startBtn.disabled = false;
  }
}

// ============================================================ PROGRESS

function renderStepListSkeleton() {
  el.steplist.innerHTML = PIPELINE_SCHRITTE.map(
    (s) => stepRowHTML(s.key, s.label, { status: "pending" })
  ).join("");
}

function stepRowHTML(key, label, step) {
  const status = step.status || "pending";
  const grund = step.grund ? `<span class="step-grund">${escapeHtml(step.grund)}</span>` : "";
  const zeit = step.dauer != null ? `${step.dauer.toFixed(1)}s` : "";
  return `
    <li class="step-row is-${status}" data-key="${key}">
      <span class="step-icon">
        ${status === "running" ? '<span class="spinner"></span>' : '<span class="dot"></span>'}
      </span>
      <span class="step-name">${label}</span>
      ${grund}
      <span class="step-meter"><span></span><span></span><span></span><span></span><span></span></span>
      <span class="step-time">${zeit}</span>
    </li>`;
}

function showProgressStage(videoName) {
  zeigeStage("stageProgress");
  el.backToUpload.hidden = true;
  el.progressTitle.textContent = "Analyse läuft …";
  el.progressSub.textContent = `„${videoName}" wird verarbeitet. Dieses Fenster kann offen bleiben.`;
  renderStepListSkeleton();
  startElapsedTimer();
}

function startElapsedTimer() {
  clearInterval(_elapsedTimer);
  _elapsedStart = Date.now();
  const elapsedEl = document.getElementById("elapsedTime");
  if (elapsedEl) elapsedEl.textContent = "0:00";
  _elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - _elapsedStart) / 1000);
    const m = Math.floor(secs / 60);
    const s = String(secs % 60).padStart(2, "0");
    const el2 = document.getElementById("elapsedTime");
    if (el2) el2.textContent = `${m}:${s}`;
  }, 1000);
}

function stopElapsedTimer() {
  clearInterval(_elapsedTimer);
  _elapsedTimer = null;
}

async function pollStatus(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      if (!res.ok) { clearInterval(pollTimer); return; }
      const job = await res.json();
      updateStepList(job.steps);

      if (job.status === "done") {
        clearInterval(pollTimer);
        stopElapsedTimer();
        el.progressTitle.textContent = "Fertig ✓";
        el.progressSub.textContent = "Die Auswertung ist bereit.";
        setTimeout(() => showDashboard(job), 600);
        loadHistory();
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        stopElapsedTimer();
        el.progressTitle.textContent = "Analyse abgebrochen";
        el.progressSub.textContent = job.error ? `Fehler: ${job.error}` : "Ein Schritt ist fehlgeschlagen.";
        el.backToUpload.hidden = false;
        loadHistory();
      }
    } catch (e) {
      // Netzwerk-Hiccup ignorieren
    }
  }, 1500);
}

function updateStepList(steps) {
  PIPELINE_SCHRITTE.forEach((s) => {
    const step = steps[s.key] || { status: "pending" };
    const row = el.steplist.querySelector(`[data-key="${s.key}"]`);
    if (row) row.outerHTML = stepRowHTML(s.key, s.label, step);
  });
}

function resetToUpload() {
  selectedFile = null;
  el.fileInput.value = "";
  el.dropzone.classList.remove("has-file");
  el.dropzoneTitle.textContent = "Video hierher ziehen oder klicken zum Auswählen";
  el.dropzoneSub.textContent = "MP4, MOV, MKV — Audio-Spur wird automatisch extrahiert";
  el.startBtn.disabled = true;
  const chip = document.getElementById("fileInfoChip");
  if (chip) chip.hidden = true;
  stopElapsedTimer();
  zeigeStage("stageUpload");
}

// ============================================================ DASHBOARD

function scoreColor(score) {
  if (score == null) return "var(--faint)";
  if (score >= 75) return "var(--score-hi)";   /* grün  */
  if (score >= 50) return "var(--score-mid)";  /* amber */
  return "var(--score-lo)";                    /* rot   */
}

function scoreLabel(score) {
  if (score == null) return "";
  if (score >= 75) return "Gut ✓";
  if (score >= 50) return "Ausbaufähig";
  return "Verbesserungsbedarf";
}

async function showDashboard(job) {
  zeigeStage("stageDashboard");
  el.dashboardSub.textContent = `„${job.video_name}" — ausgewertet am ${new Date(job.created_at).toLocaleString("de-DE")}`;

  let data;
  try {
    const res = await fetch("/api/report/gesamt");
    data = await res.json();
  } catch (e) {
    return;
  }

  // ── Gesamtscore
  const gesamt = data.gesamtscore;
  const scoreNum = gesamt != null ? Math.round(gesamt) : null;
  const scoreCol = scoreColor(gesamt);

  // SVG Ring
  const radius = 44;
  const circ = 2 * Math.PI * radius;
  const pctFill = scoreNum != null ? Math.max(0, Math.min(100, scoreNum)) / 100 : 0;
  const dashoffset = circ * (1 - pctFill);
  const ringHtml = scoreNum != null ? `
    <svg class="score-ring" viewBox="0 0 100 100" aria-hidden="true">
      <circle class="score-ring-bg" cx="50" cy="50" r="${radius}"/>
      <circle class="score-ring-fill" cx="50" cy="50" r="${radius}"
        stroke="${scoreCol}"
        stroke-dasharray="${circ.toFixed(2)}"
        stroke-dashoffset="${dashoffset.toFixed(2)}"
        style="--score-ring-offset:${dashoffset.toFixed(2)};--score-ring-circ:${circ.toFixed(2)}"/>
    </svg>` : "";

  el.gesamtScoreValue.innerHTML = ringHtml + (scoreNum != null
    ? `<span class="score-hero-count" data-target="${scoreNum}" style="color:${scoreCol}">0</span><small>/100</small>`
    : `—<small>/100</small>`);
  el.gesamtScoreValue.style.color = "";

  // Count-up Animation
  const countEl = el.gesamtScoreValue.querySelector(".score-hero-count");
  if (countEl) animateCountUp(countEl, scoreNum, 800);

  // ── Modalitäts-Balken
  el.modalitaetBars.innerHTML = Object.entries(data.modalitaets_scores || {}).map(([key, val]) => {
    const pct = val != null ? Math.max(0, Math.min(100, val)) : 0;
    const col = scoreColor(val);
    return `
      <div class="modalitaet-row">
        <span class="modalitaet-label">${MODALITAET_LABELS[key] || key}</span>
        <span class="modalitaet-track">
          <span class="modalitaet-fill" style="width:${pct}%; background:${col}"></span>
        </span>
        <span class="modalitaet-val" style="color:${col}">${val != null ? Math.round(val) : "—"}</span>
      </div>`;
  }).join("");

  // ── Top-3 Verbesserungstipps
  const top3 = data.top3_schwaechste || data.top3_verbesserung || [];
  if (top3.length) {
    el.tipsWrap.innerHTML = `
      <h2 class="section-label">Top-3 Verbesserungspotenzial</h2>
      <div class="tips-block">
        ${top3.map((t, i) => {
          const modulKey = t.modul || t.name || "";
          const label = MODUL_LABELS[modulKey] || modulKey;
          const score = t.score != null ? Math.round(t.score) : null;
          const col = scoreColor(t.score);
          return `
            <div class="tip-item">
              <div class="tip-rank">${i + 1}</div>
              <div class="tip-body">
                <div class="tip-head">
                  <span class="tip-label">${label}</span>
                  <span class="tip-score" style="color:${col}">${score != null ? score + " / 100" : ""}</span>
                </div>
                <div class="tip-desc">${escapeHtml(MODUL_DESC[modulKey] || "")}</div>
                <button class="tip-link" data-modul="${modulKey}">Detail-Report ansehen →</button>
              </div>
            </div>`;
        }).join("")}
      </div>`;

    el.tipsWrap.querySelectorAll(".tip-link").forEach((btn) => {
      btn.addEventListener("click", () =>
        openDrawer(btn.dataset.modul, MODUL_LABELS[btn.dataset.modul] || btn.dataset.modul)
      );
    });
  } else {
    el.tipsWrap.innerHTML = `
      <div class="tips-empty">
        <span class="tips-empty-icon">✓</span>
        Keine kritischen Schwachstellen erkannt — alle Module im grünen oder gelben Bereich.
      </div>`;
  }

  // ── Modul-Karten
  el.moduleGrid.innerHTML = Object.entries(data.modul_scores || {}).map(([key, val]) => {
    const label = MODUL_LABELS[key] || key;
    const desc  = MODUL_DESC[key] || "";
    const na    = val == null;
    const pct   = na ? 0 : Math.max(0, Math.min(100, val));
    const col   = scoreColor(val);
    const lbl   = scoreLabel(val);
    const tierClass = na ? "" : val >= 75 ? "tier-hi" : val >= 50 ? "tier-mid" : "tier-lo";
    return `
      <button class="module-card ${tierClass}" data-modul="${key}">
        <div class="module-card-top">
          <span class="module-card-name">${label}</span>
          <span class="module-card-score ${na ? "na" : ""}" style="${na ? "" : `color:${col}`}">
            ${na ? "n/a" : Math.round(val)}
          </span>
        </div>
        <div class="module-track"><div class="module-fill" style="width:${pct}%; background:${col}"></div></div>
        <div class="module-card-foot">
          <span class="module-card-desc">${desc}</span>
          ${!na ? `<span class="module-card-badge" style="color:${col}">${lbl}</span>` : ""}
        </div>
      </button>`;
  }).join("");

  el.moduleGrid.querySelectorAll(".module-card").forEach((card) => {
    card.addEventListener("click", () =>
      openDrawer(card.dataset.modul, MODUL_LABELS[card.dataset.modul] || card.dataset.modul)
    );
  });

  // ── Konsistenz-Hinweise
  const konsistenz = data.konsistenz_hinweise || [];
  if (konsistenz.length) {
    el.konsistenzWrap.innerHTML = `
      <h2 class="section-label">Konsistenz-Hinweise</h2>
      <div class="konsistenz-block">
        ${konsistenz.map((k) => `
          <div class="konsistenz-item">
            <div class="konsistenz-title">${escapeHtml(k.titel)}</div>
            <div class="konsistenz-text">${escapeHtml(k.text)}</div>
          </div>`).join("")}
      </div>`;
  } else {
    el.konsistenzWrap.innerHTML = "";
  }
}

// ============================================================ VERLAUF

async function loadHistory() {
  try {
    const res = await fetch("/api/verlauf");
    const items = await res.json();
    if (!items.length) {
      el.historyEmpty.hidden = false;
      el.historyList.innerHTML = "";
      return;
    }
    el.historyEmpty.hidden = true;
    el.historyList.innerHTML = items.map((it) => {
      const datum = new Date(it.created_at).toLocaleString("de-DE", {
        day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
      });
      const scoreText = it.status === "error" ? "" : (it.gesamtscore != null ? Math.round(it.gesamtscore) : "…");
      const metaClass = it.status === "error" ? "err" : "";
      const metaText  = it.status === "error" ? "fehlgeschlagen" : (it.status === "running" ? "läuft …" : datum);
      return `
        <li class="history-item" data-id="${it.id}">
          <div class="history-item-top">
            <span class="history-item-name">${escapeHtml(it.video_name)}</span>
            <span class="history-item-score">${scoreText}</span>
          </div>
          <div class="history-item-meta ${metaClass}">${metaText}</div>
        </li>`;
    }).join("");

    el.historyList.querySelectorAll(".history-item").forEach((item) => {
      item.addEventListener("click", async () => {
        const found = items.find((i) => i.id === item.dataset.id);
        if (!found || found.status !== "done") return;
        el.stageUpload.hidden = true;
        el.stageProgress.hidden = true;
        showDashboard(found);
      });
    });
  } catch (e) {
    // Verlauf ist ein Nice-to-have
  }
}

// ============================================================ DRAWER

async function openDrawer(modul, label) {
  _drawerModul = modul;
  _drawerLabel = label;
  _drawerEbene = "kurz";

  // Toggle zurücksetzen
  el.toggleKurz.classList.add("is-active");
  el.toggleDetail.classList.remove("is-active");

  // Gesamt-Modul hat keine Kurz/Detail-Ebenen → Toggle ausblenden
  const hatEbenen = !["gesamt", "transkript", "inhalt"].includes(modul);
  el.drawerToggle.style.display = hatEbenen ? "flex" : "none";

  el.drawerEyebrow.textContent = "Modul-Report";
  el.drawerTitle.textContent = label;

  el.drawerOverlay.classList.add("is-open");
  el.drawer.classList.add("is-open");

  await _ladeReportEbene(modul, label, "kurz");
}

async function _ladeReportEbene(modul, label, ebene) {
  _drawerEbene = ebene;
  _drawerRawText = "";
  el.drawerRendered.innerHTML = renderReportLoading();

  // TXT-Download-Link setzen
  el.drawerDlTxt.href = `/api/report/${modul}/download?ebene=${ebene}`;
  el.drawerDlTxt.download = `${modul}_${ebene}_report.txt`;
  el.drawerDlBar.hidden = false;

  try {
    const res = await fetch(`/api/report/${modul}/txt?ebene=${ebene}`);
    if (!res.ok) {
      // Fallback: versuche ohne ebene (alte Module)
      const resFallback = await fetch(`/api/report/${modul}/txt`);
      if (!resFallback.ok) {
        el.drawerRendered.innerHTML = renderReportError("Für dieses Modul liegt noch kein Report vor.");
        el.drawerDlBar.hidden = true;
        return;
      }
      const dataFb = await resFallback.json();
      _drawerRawText = dataFb.content;
      el.drawerRendered.innerHTML = renderReportHtml(_drawerRawText, label);
      return;
    }
    const data = await res.json();
    _drawerRawText = data.content;
    el.drawerRendered.innerHTML = renderReportHtml(_drawerRawText, label);
  } catch (e) {
    el.drawerRendered.innerHTML = renderReportError("Report konnte nicht geladen werden.");
    el.drawerDlBar.hidden = true;
  }
}

function closeDrawer() {
  el.drawerOverlay.classList.remove("is-open");
  el.drawer.classList.remove("is-open");
}

// Escape-Taste schließt den Drawer
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && el.drawer.classList.contains("is-open")) {
    closeDrawer();
  }
});

// ============================================================ DATEIEN

const KATEGORIE_LABELS = {
  transkript:           "Transkript",
  report_inhalt:        "Report: Inhalt",
  report_gesamt:        "Report: Gesamt",
  report_pausen:        "Report: Pausen",
  report_sprechfluss:   "Report: Sprechfluss",
  report_sprechtempo:   "Report: Sprechtempo",
  report_fuellwoerter:  "Report: Füllwörter",
  report_lautstaerke:   "Report: Lautstärke",
  report_pitch:         "Report: Tonhöhe",
  report_emotion:       "Report: Emotion",
  report_video:         "Report: Körpersprache",
  json:                 "JSON-Daten",
};

const KATEGORIE_ORDER = [
  "report_gesamt", "transkript",
  "report_inhalt", "report_pausen", "report_sprechfluss", "report_sprechtempo",
  "report_fuellwoerter", "report_lautstaerke", "report_pitch", "report_emotion",
  "report_video", "json",
];

let aktiveKategorie = null;
let alleDateien = [];

function dateiRelPfad(eintrag) {
  const kat = eintrag.kategorie;
  if (kat === "transkript")      return `Transkripte/${eintrag.filename}`;
  if (kat === "json")            return `zwischen_output/${eintrag.filename}`;
  if (kat === "report_inhalt")   return `reports/${eintrag.filename}`;
  const ordner = kat.replace("report_", "");
  return `reports/${ordner}/${eintrag.filename}`;
}

async function loadDateien() {
  el.dateiListe.innerHTML = '<div class="datei-loading">Lädt …</div>';
  try {
    const res = await fetch("/api/dateien");
    alleDateien = await res.json();
  } catch (e) {
    el.dateiListe.innerHTML = '<div class="datei-loading">Fehler beim Laden.</div>';
    return;
  }
  renderDateiFilter();
  renderDateiListe(aktiveKategorie);
}

function renderDateiFilter() {
  const vorhandene = new Set(alleDateien.map((d) => d.kategorie));
  const kategorien = [null, ...KATEGORIE_ORDER.filter((k) => vorhandene.has(k))];

  el.dateiFilter.innerHTML = kategorien.map((k) => {
    const label = k === null ? "Alle" : (KATEGORIE_LABELS[k] || k);
    const active = k === aktiveKategorie ? "is-active" : "";
    return `<button class="filter-chip ${active}" data-kat="${k ?? ""}">${label}</button>`;
  }).join("");

  el.dateiFilter.querySelectorAll(".filter-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      aktiveKategorie = btn.dataset.kat || null;
      el.dateiFilter.querySelectorAll(".filter-chip").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      renderDateiListe(aktiveKategorie);
    });
  });
}

function renderDateiListe(filterKat) {
  const gefiltert = filterKat
    ? alleDateien.filter((d) => d.kategorie === filterKat)
    : alleDateien;

  if (!gefiltert.length) {
    el.dateiListe.innerHTML = '<div class="datei-loading">Keine Dateien in dieser Kategorie.</div>';
    return;
  }

  const gruppen = {};
  gefiltert.forEach((d) => {
    if (!gruppen[d.kategorie]) gruppen[d.kategorie] = [];
    gruppen[d.kategorie].push(d);
  });

  const reihenfolge = filterKat
    ? [filterKat]
    : KATEGORIE_ORDER.filter((k) => gruppen[k]);

  el.dateiListe.innerHTML = reihenfolge.map((kat) => {
    const items = gruppen[kat] || [];
    const gruppenLabel = KATEGORIE_LABELS[kat] || kat;
    return `
      <div class="datei-gruppe">
        <div class="datei-gruppe-titel">${gruppenLabel}</div>
        ${items.map((d) => {
          const rel = dateiRelPfad(d);
          const groesseKb = (d.groesse / 1024).toFixed(1);
          const datum = new Date(d.geaendert * 1000).toLocaleString("de-DE", {
            day: "2-digit", month: "2-digit", year: "2-digit",
            hour: "2-digit", minute: "2-digit",
          });
          const kannAnzeigen = d.filename.endsWith(".txt") || d.filename.endsWith(".json");
          return `
            <div class="datei-row" data-pfad="${encodeURIComponent(rel)}" data-name="${escapeHtml(d.filename)}">
              <div class="datei-icon">${d.filename.endsWith(".json") ? "{}" : "≡"}</div>
              <div class="datei-info">
                <div class="datei-name">${escapeHtml(d.filename)}</div>
                <div class="datei-meta">${datum} · ${groesseKb} KB</div>
              </div>
              <div class="datei-actions">
                ${kannAnzeigen
                  ? `<button class="datei-btn-lesen" title="Anzeigen" data-pfad="${encodeURIComponent(rel)}" data-label="${escapeHtml(d.filename)}">Anzeigen</button>`
                  : ""}
                <a class="datei-btn-dl" href="/api/dateien/download?pfad=${encodeURIComponent(rel)}" download="${escapeHtml(d.filename)}" title="Herunterladen">↓</a>
              </div>
            </div>`;
        }).join("")}
      </div>`;
  }).join("");

  el.dateiListe.querySelectorAll(".datei-btn-lesen").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pfad = decodeURIComponent(btn.dataset.pfad);
      const label = btn.dataset.label;
      openDrawerByPfad(pfad, label);
    });
  });
}

async function openDrawerByPfad(pfad, label) {
  _drawerLabel = label;
  _drawerRawText = "";

  el.drawerEyebrow.textContent = "Dateiinhalt";
  el.drawerTitle.textContent = label;
  el.drawerRendered.innerHTML = renderReportLoading();
  el.drawerDlTxt.href = `/api/dateien/download?pfad=${encodeURIComponent(pfad)}`;
  el.drawerDlTxt.download = label;
  el.drawerDlBar.hidden = false;

  el.drawerOverlay.classList.add("is-open");
  el.drawer.classList.add("is-open");

  try {
    const res = await fetch(`/api/dateien/lesen?pfad=${encodeURIComponent(pfad)}`);
    if (!res.ok) {
      el.drawerRendered.innerHTML = renderReportError("Datei konnte nicht geladen werden.");
      el.drawerDlBar.hidden = true;
      return;
    }
    const data = await res.json();
    _drawerRawText = data.content;

    if (pfad.endsWith(".json")) {
      el.drawerRendered.innerHTML = renderJsonHtml(_drawerRawText);
    } else {
      el.drawerRendered.innerHTML = renderReportHtml(_drawerRawText, label);
    }
  } catch (e) {
    el.drawerRendered.innerHTML = renderReportError("Fehler beim Laden.");
    el.drawerDlBar.hidden = true;
  }
}

// ============================================================ REPORT RENDERER

function renderReportLoading() {
  return `<div class="rpt-muted" style="padding:12px 0">Lädt …</div>`;
}

function renderReportError(msg) {
  return `<div class="rpt-muted" style="padding:12px 0;color:var(--red)">${escapeHtml(msg)}</div>`;
}

/**
 * Parst den rohen Report-Text und baut daraus strukturiertes HTML.
 * Der Parser erkennt folgende Muster:
 *  - === / --- Trennlinien -> Abschnitt-Grenzen
 *  - Zeilen in ALL CAPS (nach einer Trennlinie) -> Abschnittstitel
 *  - Zeilen mit "X / 100  [█…]  [✅🟡❌]" -> Score-Balken
 *  - Eingerückte Zeilen nach einem Score -> Submodul-Karten
 *  - Nummerierte Zeilen "1. …" -> geordnete Liste
 */
function renderReportHtml(raw, label) {
  const lines = raw.split("\n");
  const blocks = [];
  let currentBlock = null;
  let currentLines = [];

  function pushBlock() {
    if (currentBlock || currentLines.length) {
      blocks.push({ type: currentBlock, lines: [...currentLines] });
      currentLines = [];
      currentBlock = null;
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // Trennlinien (===, ---) → Abschnitt-Ende
    if (/^[=]{4,}/.test(trimmed) || /^[-]{4,}/.test(trimmed)) {
      if (currentLines.length > 0) pushBlock();
      continue;
    }

    // Leerzeile innerhalb eines Blocks → einfach hinzufügen
    if (trimmed === "") {
      currentLines.push("");
      continue;
    }

    currentLines.push(line);
  }
  pushBlock();

  // Nun rendern
  let html = "";

  for (const block of blocks) {
    const nonEmpty = block.lines.filter((l) => l.trim() !== "");
    if (!nonEmpty.length) continue;
    html += renderBlock(nonEmpty);
  }

  return html || `<div class="rpt-muted">Kein Inhalt.</div>`;
}

function renderBlock(lines) {
  let html = '<div class="rpt-block">';

  // Erste nicht-leere Zeile: möglicher Titel?
  let idx = 0;
  const firstLine = lines[idx]?.trim() || "";

  // Erkenne Haupttitel (ALL CAPS oder "PRÄSENTATION" etc.)
  const isTitel = isUppercaseTitle(firstLine);
  if (isTitel) {
    html += `<div class="rpt-h1">${escapeHtml(firstLine)}</div>`;
    idx++;
  }

  // Zweite Zeile kann Unterüberschrift sein (z.B. Erstellt, Quelle)
  // Restliche Zeilen iterieren
  const remaining = lines.slice(idx);

  let i = 0;
  while (i < remaining.length) {
    const raw = remaining[i];
    const t = raw.trim();

    if (!t) { i++; continue; }

    // Abschnitts-Unterüberschrift (ALL CAPS, kurz)
    if (isUppercaseTitle(t) && t.length < 80) {
      html += `<div class="rpt-h2">${escapeHtml(t)}</div>`;
      i++;
      continue;
    }

    // Score-Zeile: "79.6 / 100   [███░]   ✅"
    const scoreMatch = parseScoreLine(t);
    if (scoreMatch) {
      html += renderScoreRow(scoreMatch);
      i++;
      // Direkt folgende Zeile kann Bewertungstext sein
      if (remaining[i + 1]) {
        const nextT = remaining[i]?.trim() || "";
        if (nextT && !isUppercaseTitle(nextT) && !parseScoreLine(nextT)) {
          html += `<div class="rpt-muted">${escapeHtml(nextT)}</div>`;
          i++;
        }
      }
      continue;
    }

    // Submodul-Zeile: "❌ Bewusste Pausen (40%)   20/100"
    const submodMatch = parseSubmodulLine(t);
    if (submodMatch) {
      // Sammle eingerückte Folgezeilen
      const subLines = [];
      i++;
      while (i < remaining.length) {
        const st = remaining[i].trim();
        if (!st) { i++; break; }
        // Neue Submodul-Zeile beendet den Block
        if (parseSubmodulLine(st) || isUppercaseTitle(st)) break;
        subLines.push(st);
        i++;
      }
      html += renderSubmodule(submodMatch, subLines);
      continue;
    }

    // Fundstelle: "▶ Im Video bei 1:23 min: ..."
    if (t.startsWith("▶")) {
      const fundstellen = [];
      while (i < remaining.length && remaining[i]?.trim().startsWith("▶")) {
        const fzeile = remaining[i].trim();
        // Folgezeile mit Tipp 💡 einsammeln
        let tipp = "";
        if (i + 1 < remaining.length) {
          const nextT = remaining[i + 1]?.trim() || "";
          if (nextT.startsWith("💡")) { tipp = nextT; i++; }
        }
        fundstellen.push({ zeile: fzeile, tipp });
        i++;
      }
      html += renderFundstellen(fundstellen);
      continue;
    }

    // Verbesserungsvorschlag-Block: "💡 Verbesserungsvorschlag:"
    if (t.startsWith("💡")) {
      const tippLines = [t];
      i++;
      while (i < remaining.length) {
        const nt = remaining[i]?.trim();
        if (!nt || isUppercaseTitle(nt) || nt.startsWith("▶") || parseSubmodulLine(nt)) break;
        tippLines.push(nt);
        i++;
      }
      html += `<div class="rpt-tipp">${tippLines.map((l) => `<span>${escapeHtml(l)}</span>`).join(" ")}</div>`;
      continue;
    }

    // Nummerierte Liste: "1. 🔥 Text…"
    if (/^\d+\.\s/.test(t)) {
      const listItems = [];
      while (i < remaining.length && /^\d+\.\s/.test(remaining[i]?.trim())) {
        // Sammle auch Folgzeilen die zur selben Nr. gehören (eingerückt)
        const item = [remaining[i].trim()];
        i++;
        while (i < remaining.length) {
          const nt = remaining[i]?.trim();
          if (!nt || /^\d+\.\s/.test(nt) || isUppercaseTitle(nt) || parseSubmodulLine(nt)) break;
          item.push(nt);
          i++;
        }
        listItems.push(item.join(" "));
      }
      html += renderNumberedList(listItems);
      continue;
    }

    // Referenz-Tabelle: Zeilen mit "•" Aufzählungspunkt
    if (t.startsWith("\u2022")) {
      const bullets = [];
      while (i < remaining.length && remaining[i]?.trim().startsWith("\u2022")) {
        const bline = remaining[i].trim();
        bullets.push(bline.startsWith("\u2022") ? bline.slice(1).trim() : bline);
        i++;
      }
      html += renderBullets(bullets);
      continue;
    }

    // Tabellen-Zeile (Pausenlängen-Tabelle): enthält mehrere "–" und Tabs/Leerzeichen
    if (looksLikeTableRow(t)) {
      const tableLines = [];
      while (i < remaining.length && looksLikeTableRow(remaining[i]?.trim())) {
        tableLines.push(remaining[i].trim());
        i++;
      }
      html += renderSimpleTable(tableLines);
      continue;
    }

    // Metazeile (Erstellt, Quelle, Dauer)
    if (/^(Erstellt|Quelle|Dauer|Erstellt am):/.test(t)) {
      html += `<div class="rpt-muted">${escapeHtml(t)}</div>`;
      i++;
      continue;
    }

    // Normale Textzeile
    html += `<div class="rpt-p">${escapeHtml(t)}</div>`;
    i++;
  }

  html += "</div>";
  return html;
}

// ── Hilfsfunktionen für den Parser ──

function isUppercaseTitle(line) {
  if (!line || line.length < 3) return false;
  const letters = line.replace(/[^a-zA-ZÄÖÜäöüß\s]/g, "");
  if (!letters.trim()) return false;
  return letters === letters.toUpperCase() && letters.trim().length > 2;
}

function parseScoreLine(line) {
  // Muster: "79.6 / 100   [███░░]   ✅" oder "32 / 100  [██░░░]  ❌"
  const m = line.match(/^([✅🟡❌]*\s*)?([\d.]+)\s*\/\s*100\s*(\[.*?\])?\s*([✅🟡❌]?)(.*)$/u);
  if (!m) return null;
  const score = parseFloat(m[2]);
  if (isNaN(score)) return null;
  return {
    score,
    emoji: (m[1] || m[4] || "").trim(),
    label: m[5]?.trim() || "",
  };
}

function parseSubmodulLine(line) {
  // Muster: "❌ Bewusste Pausen (40%)   20/100" oder "✅ Füllwörter   94.0/100"
  const m = line.match(/^([✅🟡❌])\s+(.+?)\s+([\d.]+)\s*\/\s*100/u);
  if (!m) return null;
  return { emoji: m[1], title: m[2].trim(), score: parseFloat(m[3]) };
}

function looksLikeTableRow(line) {
  if (!line) return false;
  // Zeile mit Tabulator oder mehreren aufeinanderfolgenden Leerzeichen + inhalt
  return /\t/.test(line) || (/\s{3,}/.test(line) && line.includes("–"));
}

// ── Render-Bausteine ──

function renderScoreRow({ score, label }) {
  const col = scoreColor(score);
  const pct = Math.max(0, Math.min(100, score));
  const lbl = scoreLabel(score);
  return `
    <div class="rpt-score-row">
      <div class="rpt-score-num" style="color:${col}">${Math.round(score)}</div>
      <div class="rpt-score-meta">
        <div class="rpt-score-label">von 100 Punkten${label ? " · " + escapeHtml(label) : ""}</div>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="rpt-track" style="flex:1"><div class="rpt-fill" style="width:${pct}%;background:${col}"></div></div>
          <span class="rpt-badge" style="color:${col}">${lbl}</span>
        </div>
      </div>
    </div>`;
}

function renderSubmodule({ emoji, title, score }, descLines) {
  const col = scoreColor(score);
  const pct = Math.max(0, Math.min(100, score));
  const tagClass = score >= 75 ? "rpt-tag-green" : score >= 50 ? "rpt-tag-amber" : "rpt-tag-red";

  // Trenne "Was gemessen wird", "Befund", "Bewertung" etc.
  let wasGemessen = "";
  let befund = "";
  let rest = [];
  for (const l of descLines) {
    if (/Was gemessen wird/.test(l)) { wasGemessen = l.replace(/Was gemessen wird\s*:?\s*/i, "").trim(); continue; }
    if (/^Befund:/.test(l)) { befund = l.replace(/^Befund:\s*/, "").trim(); continue; }
    if (/^Bewertung:/.test(l)) { rest.push(l); continue; }
    if (/^Referenzwerte:/.test(l)) { rest.push(l); continue; }
    rest.push(l);
  }

  return `
    <div class="rpt-submodule ${tagClass}">
      <div class="rpt-submodule-head">
        <span class="rpt-submodule-title">${emoji} ${escapeHtml(title)}</span>
        <span class="rpt-submodule-score" style="color:${col}">${Math.round(score)} / 100</span>
      </div>
      <div class="rpt-track" style="margin-bottom:8px"><div class="rpt-fill" style="width:${pct}%;background:${col}"></div></div>
      ${wasGemessen ? `<div class="rpt-submodule-desc">${escapeHtml(wasGemessen)}</div>` : ""}
      ${befund ? `<div class="rpt-submodule-befund"><strong>Befund:</strong> ${escapeHtml(befund)}</div>` : ""}
      ${rest.map((l) => `<div class="rpt-muted">${escapeHtml(l)}</div>`).join("")}
    </div>`;
}

function renderNumberedList(items) {
  const itemsHtml = items.map((item, i) => {
    const text = item.replace(/^\d+\.\s*/, "");
    return `
      <li class="rpt-list-item">
        <span class="rpt-list-num">${i + 1}.</span>
        <span>${escapeHtml(text)}</span>
      </li>`;
  }).join("");
  return `<ul class="rpt-list">${itemsHtml}</ul>`;
}

function renderFundstellen(fundstellen) {
  if (!fundstellen.length) return "";
  const items = fundstellen.map(({ zeile, tipp }) => {
    // Extrahiere Zeitstempel aus "▶ Im Video bei 1:23 min: ..."
    const zeitMatch = zeile.match(/bei\s+([\d:]+)\s*min/);
    const zeit = zeitMatch ? zeitMatch[1] : "";
    const text = zeile.replace(/^▶\s*/, "");
    return `
      <li class="rpt-fundstelle">
        ${zeit ? `<span class="rpt-fundstelle-zeit">${escapeHtml(zeit)}</span>` : ""}
        <div class="rpt-fundstelle-body">
          <span class="rpt-fundstelle-text">${escapeHtml(text)}</span>
          ${tipp ? `<span class="rpt-fundstelle-tipp">${escapeHtml(tipp)}</span>` : ""}
        </div>
      </li>`;
  }).join("");
  return `<ul class="rpt-fundstellen">${items}</ul>`;
}

function renderBullets(items) {
  return `<ul class="rpt-list">${items.map((item) =>
    `<li class="rpt-list-item"><span class="rpt-list-num">·</span><span>${escapeHtml(item)}</span></li>`
  ).join("")}</ul>`;
}

function renderSimpleTable(lines) {
  const rows = lines.map((l) => {
    // Splitte nach mehreren Leerzeichen oder Tab
    const cells = l.split(/\t|\s{3,}/).map((c) => c.trim()).filter(Boolean);
    return cells;
  });

  if (!rows.length) return "";

  const tdRows = rows.map((cells) =>
    `<tr>${cells.map((c, ci) => `<td${ci === 0 ? "" : ""}>${escapeHtml(c)}</td>`).join("")}</tr>`
  ).join("");

  return `<table class="rpt-table"><tbody>${tdRows}</tbody></table>`;
}

function renderJsonHtml(raw) {
  try {
    const obj = JSON.parse(raw);
    const pretty = JSON.stringify(obj, null, 2);
    return `<div class="rpt-block"><pre style="font-family:var(--font-mono);font-size:12px;color:var(--text);white-space:pre-wrap;word-break:break-all;line-height:1.6">${escapeHtml(pretty)}</pre></div>`;
  } catch {
    return `<div class="rpt-block"><pre style="font-family:var(--font-mono);font-size:12px;color:var(--text);white-space:pre-wrap">${escapeHtml(raw)}</pre></div>`;
  }
}

// ============================================================ PDF DOWNLOAD

function downloadAsPdf() {
  if (!_drawerRawText) return;
  // Den aktuellen Drawer-Inhalt in den Print-Bereich kopieren und drucken
  el.printArea.innerHTML = `
    <div style="font-family:inherit">
      <h1 style="font-size:20pt;margin-bottom:4mm">${escapeHtml(_drawerLabel)}</h1>
      ${el.drawerRendered.innerHTML}
    </div>`;
  window.print();
  // Nach dem Druck aufräumen
  setTimeout(() => { el.printArea.innerHTML = ""; }, 1000);
}

// ============================================================ WORD DOWNLOAD

function downloadAsWord() {
  if (!_drawerRawText) return;

  // Word-kompatibles HTML generieren (Word versteht einfaches HTML mit inline-Styles)
  const wordHtml = buildWordHtml(_drawerLabel, _drawerRawText);
  const blob = new Blob(
    ["\uFEFF" + wordHtml],   // BOM für korrektes UTF-8 in Word
    { type: "application/msword;charset=utf-8" }
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${_drawerLabel.replace(/[^a-zA-Z0-9äöüÄÖÜß_\-]/g, "_")}_report.doc`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function buildWordHtml(label, raw) {
  const lines = raw.split("\n");

  let body = `<h1 style="font-family:Calibri,sans-serif;font-size:22pt;color:#1a1a1a">${escapeHtml(label)}</h1>\n`;

  for (const line of lines) {
    const t = line.trim();
    if (!t) { body += "<br/>\n"; continue; }

    if (/^[=]{4,}/.test(t) || /^[-]{4,}/.test(t)) {
      body += `<hr style="border:none;border-top:1pt solid #cccccc;margin:6pt 0"/>\n`;
      continue;
    }

    const scoreMatch = parseScoreLine(t);
    if (scoreMatch) {
      const col = scoreColor(scoreMatch.score);
      const colHex = cssVarToHex(col);
      body += `<p style="font-size:18pt;font-weight:bold;color:${colHex};font-family:Calibri,sans-serif">${Math.round(scoreMatch.score)} / 100</p>\n`;
      continue;
    }

    if (isUppercaseTitle(t) && t.length < 80) {
      body += `<h2 style="font-family:Calibri,sans-serif;font-size:13pt;color:#2563eb;text-transform:uppercase;letter-spacing:1pt">${escapeHtml(t)}</h2>\n`;
      continue;
    }

    if (/^\d+\.\s/.test(t)) {
      body += `<p style="font-family:Calibri,sans-serif;font-size:11pt;color:#1a1a1a;margin-left:12pt">${escapeHtml(t)}</p>\n`;
      continue;
    }

    if (t.startsWith("•")) {
      body += `<p style="font-family:Calibri,sans-serif;font-size:11pt;color:#1a1a1a;margin-left:12pt">${escapeHtml(t)}</p>\n`;
      continue;
    }

    body += `<p style="font-family:Calibri,sans-serif;font-size:11pt;color:#1a1a1a;line-height:1.6">${escapeHtml(t)}</p>\n`;
  }

  return `<!DOCTYPE html>
<html xmlns:o='urn:schemas-microsoft-com:office:office'
      xmlns:w='urn:schemas-microsoft-com:office:word'
      xmlns='http://www.w3.org/TR/REC-html40'>
<head>
  <meta charset="utf-8"/>
  <!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>90</w:Zoom></w:WordDocument></xml><![endif]-->
  <style>body{margin:2cm;font-family:Calibri,sans-serif}</style>
</head>
<body>${body}</body>
</html>`;
}

/** Mappt CSS-Variablen-Namen auf Hex-Farben für Word-Export */
function cssVarToHex(cssVar) {
  const map = {
    "var(--score-hi)":  "#16a34a",
    "var(--score-mid)": "#d97706",
    "var(--score-lo)":  "#dc2626",
    "var(--faint)":     "#A1A1AA",
    "var(--text)":      "#09090B",
  };
  return map[cssVar] || "#333333";
}

/** Animiert eine Zahl von 0 auf `target` in `duration` ms */
function animateCountUp(el2, target, duration) {
  const start = performance.now();
  function frame(now) {
    const progress = Math.min((now - start) / duration, 1);
    // easeOutQuart
    const eased = 1 - Math.pow(1 - progress, 4);
    el2.textContent = Math.round(eased * target);
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// ============================================================ HELPERS

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}
