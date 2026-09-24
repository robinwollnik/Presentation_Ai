"""app.py
==============================================================================
Lokales Web-Interface für präsentation_ai.

Startet einen FastAPI-Server auf deinem eigenen Rechner, der die bestehende
Pipeline aus main.py über eine Browser-Oberfläche bedienbar macht:
  - Video per Drag & Drop hochladen
  - Fortschritt live verfolgen
  - Gesamtscore + Modul-Scores als Dashboard ansehen
  - Einzelreports pro Modul lesen und herunterladen
  - Verlauf früherer Analysen durchsuchen

Es wird NICHTS gehostet oder ins Internet geschickt. Der Server läuft nur
lokal (127.0.0.1) und ist ausschließlich vom eigenen Rechner erreichbar.
Jede Person, die den Code hat, startet ihre eigene Instanz.

Installation (einmalig):
    pip install -r requirements.txt

Start:
    python app.py
    -> öffnet automatisch http://127.0.0.1:8000 im Browser

Diese Datei muss im selben Ordner liegen wie main.py, transcribe.py,
inhalt_analyse.py usw. (also im Projekt-Root / abgabe_struktur/).
==============================================================================
"""

from __future__ import annotations

import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # kein __pycache__ → immer frische .py-Dateien

import json
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import main as pipeline  # bestehende main.py wird 1:1 wiederverwendet

# ============================================================================
# PFADE
# ============================================================================

APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
UPLOADS_DIR = APP_ROOT / "uploads"
JOBS_REGISTRY = pipeline.ZWISCHEN_OUTPUT / "ui_verlauf.json"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
pipeline.ZWISCHEN_OUTPUT.mkdir(parents=True, exist_ok=True)
pipeline.REPORTS_ROOT.mkdir(parents=True, exist_ok=True)

# ============================================================================
# MODELLE VORLADEN (einmalig beim Serverstart)
# ============================================================================
# Vorher wurden diese Modelle bei JEDER Analyse neu von der Platte geladen:
#   - spaCy + Zero-Shot + Sentiment  (inhalt_analyse.py)
#   - Whisper                        (transcribe.py)
#   - Wav2Vec2                       (emotionale_variation_analyse.py)
# Das war der Hauptgrund für die langen Laufzeiten, v.a. bei kurzen Videos.
# Jetzt werden sie einmal hier geladen und für alle folgenden Analysen
# wiederverwendet. Das passiert BEVOR der Server den Port öffnet — die
# Modelle sind also schon bereit, wenn sich der Browser-Tab öffnet.
#
# Bei "python main.py video.mp4" auf der Kommandozeile (ohne app.py) laden
# die Module weiterhin wie bisher ihre Modelle selbst — das ändert sich
# nur für den Web-Weg über app.py.

import importlib.util as _ilu


def _lade_modul(name: str, dateiname: str):
    spec = _ilu.spec_from_file_location(name, APP_ROOT / dateiname)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("\n" + "=" * 70)
print("  präsentation_ai — Modelle werden vorgeladen...")
print("  (beim allerersten Start inkl. Downloads — kann einige Minuten dauern)")
print("=" * 70)

_t0_modelle = time.time()

_inhalt_mod = _lade_modul("inhalt_analyse_preload", "inhalt_analyse.py")
_transcribe_mod = _lade_modul("transcribe_preload", "transcribe.py")
_emotion_mod = _lade_modul("emotion_preload", "emotionale_variation_analyse.py")

print("\n[app] → Inhaltsanalyse-Modelle (spaCy, Zero-Shot, Sentiment)...")
NLP, ZERO_SHOT, SENTIMENT = _inhalt_mod.lade_modelle()

print("\n[app] → Whisper-Modell (Transkription, 'small')...")
WHISPER_MODEL = _transcribe_mod.lade_whisper_modell("small")

print("\n[app] → Wav2Vec2-Modell (Emotionale Variation)...")
EMOTION_MODEL = _emotion_mod.EmotionModel()

print(f"\n[app] ✓ Alle Modelle geladen in {time.time() - _t0_modelle:.1f}s — Server bereit.")
print("=" * 70 + "\n")

# Reihenfolge + Anzeigenamen der Pipeline-Schritte für die Fortschrittsanzeige
PIPELINE_SCHRITTE = [
    ("transkription",          "Transkription (Whisper)"),
    ("inhalt_analyse",         "Inhaltsanalyse"),
    ("pausen_analyse",         "Pausen"),
    ("sprechfluss_analyse",    "Sprechfluss"),
    ("sprechtempo_analyse",    "Sprechtempo"),
    ("fuellwoerter_analyse",   "Füllwörter"),
    ("audio_extraktion",       "Audio-Extraktion"),
    ("lautstaerke_analyse",    "Lautstärke"),
    ("pitch_variation_analyse","Tonhöhen-Variation"),
    ("emotionale_variation",   "Emotionale Variation"),
    ("video_analyse",          "Körpersprache (Video)"),
    ("gesamtscore",            "Gesamtscore"),
]

# Zuordnung Modul-Key -> Report-Unterordner unter reports/
MODUL_REPORT_ORDNER = {
    "pausen":                "pausen",
    "sprechfluss":            "sprechfluss",
    "sprechtempo":            "sprechtempo",
    "fuellwoerter":           "fuellwoerter",
    "lautstaerke":            "lautstaerke",
    "pitch_variation":        "pitch",
    "emotionale_variation":   "emotion",
    "video":                  "video",
    "gesamt":                 "gesamt",
    "transkript":             "transkript",
}

MODUL_ANZEIGENAME = {
    "fuellwoerter":          "Füllwörter",
    "sprechtempo":           "Sprechtempo",
    "pausen":                "Pausen",
    "sprechfluss":           "Sprechfluss",
    "lautstaerke":           "Lautstärke",
    "pitch_variation":       "Tonhöhen-Variation",
    "emotionale_variation":  "Emotionale Variation",
    "video":                 "Körpersprache",
}

# ============================================================================
# JOB-VERWALTUNG
# ============================================================================
# Es läuft immer nur eine Analyse gleichzeitig (lokales Ein-Personen-Tool,
# main.py schreibt seine Zwischen-Outputs unter festen Dateinamen).

JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
AKTIVER_JOB: Optional[str] = None


def _neuer_job(video_name: str, flags: dict) -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "video_name": video_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",  # running | done | error
        "flags": flags,
        "steps": {key: {"status": "pending", "dauer": None, "grund": None}
                  for key, _ in PIPELINE_SCHRITTE},
        "error": None,
        "gesamtscore": None,
        "modalitaeten": None,
    }


def _step(job: dict, key: str, status: str, dauer: float = None, grund: str = None):
    with JOBS_LOCK:
        job["steps"][key] = {"status": status, "dauer": dauer, "grund": grund}


def _lade_verlauf() -> list:
    if JOBS_REGISTRY.exists():
        try:
            return json.loads(JOBS_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _speichere_verlauf_eintrag(job: dict):
    reg = _lade_verlauf()
    reg = [e for e in reg if e["id"] != job["id"]]
    reg.append({
        "id": job["id"],
        "video_name": job["video_name"],
        "created_at": job["created_at"],
        "status": job["status"],
        "gesamtscore": job["gesamtscore"],
    })
    JOBS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    JOBS_REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================================
# PIPELINE-AUSFÜHRUNG (Hintergrund-Thread)
# ============================================================================

def _run_pipeline(job_id: str, video_pfad: Path, flags: dict):
    global AKTIVER_JOB
    job = JOBS[job_id]

    def schritt(key, fn):
        _step(job, key, "running")
        t0 = time.time()
        try:
            ergebnis = fn()
            _step(job, key, "ok", dauer=round(time.time() - t0, 1))
            return ergebnis
        except Exception as e:
            _step(job, key, "fail", dauer=round(time.time() - t0, 1), grund=str(e)[:300])
            print(f"[app] Schritt '{key}' fehlgeschlagen:\n{traceback.format_exc()}")
            raise

    def parallel_schritte(aufgaben: list):
        """Führt (key, fn)-Paare gleichzeitig aus. Fehler werden isoliert, nicht weitergereicht."""
        for key, _ in aufgaben:
            _step(job, key, "running")
        with ThreadPoolExecutor(max_workers=len(aufgaben)) as pool:
            futures = {pool.submit(_parallel_worker, key, fn): key for key, fn in aufgaben}
            for future in as_completed(futures):
                key = futures[future]
                exc = future.exception()
                if exc:
                    _step(job, key, "fail", grund=str(exc)[:300])
                    print(f"[app] Parallel-Schritt '{key}' fehlgeschlagen:\n{traceback.format_exc()}")

    def _parallel_worker(key, fn):
        t0 = time.time()
        ergebnis = fn()
        _step(job, key, "ok", dauer=round(time.time() - t0, 1))
        return ergebnis

    def skip(key, grund):
        _step(job, key, "skip", grund=grund)

    try:
        # Vorgeladenes Whisper-Modell durchreichen statt bei jeder
        # Transkription neu zu laden.
        transkript_pfad = schritt(
            "transkription",
            lambda: pipeline.run_transcribe(video_pfad, whisper_model=WHISPER_MODEL),
        )

        # ------- Gruppe A: Video + Audio-Extraktion — brauchen kein JSON -------
        # Starten parallel während inhalt_analyse läuft.
        _audio_box: list = []
        gruppe_a = []
        def _extract_audio():
            _audio_box.append(pipeline.extrahiere_audio(video_pfad))
        gruppe_a.append(("audio_extraktion", _extract_audio))

        if flags.get("skip_video"):
            skip("video_analyse", "vom Nutzer übersprungen")
        else:
            gruppe_a.append(("video_analyse", lambda: pipeline.run_video(video_pfad)))

        # Inhaltsanalyse sequenziell — ihr JSON wird von fast allen Modulen
        # gelesen. Vorgeladene Modelle (spaCy, Zero-Shot, Sentiment) durchreichen.
        schritt(
            "inhalt_analyse",
            lambda: pipeline.run_inhalt(
                transkript_pfad, nlp=NLP, zero_shot=ZERO_SHOT, sentiment=SENTIMENT
            ),
        )

        # Auf Gruppe A warten und Audio-Pfad holen
        parallel_schritte(gruppe_a)
        audio_pfad = _audio_box[0] if _audio_box else None

        # ------- Gruppe B: pausen braucht inhalt-JSON (jetzt fertig) -------
        schritt("pausen_analyse", lambda: pipeline.run_pausen(transkript_pfad))

        # ------- Gruppe C: alle übrigen — pausen + inhalt sind jetzt da -------
        gruppe_c = [
            ("sprechfluss_analyse",  lambda: pipeline.run_sprechfluss(transkript_pfad)),
            ("sprechtempo_analyse",  lambda: pipeline.run_sprechtempo(transkript_pfad)),
            ("fuellwoerter_analyse", lambda: pipeline.run_fuellwoerter(transkript_pfad)),
        ]
        if audio_pfad:
            gruppe_c += [
                ("lautstaerke_analyse",     lambda: pipeline.run_lautstaerke(audio_pfad)),
                ("pitch_variation_analyse", lambda: pipeline.run_pitch(audio_pfad)),
            ]
            if flags.get("skip_emotion"):
                skip("emotionale_variation", "vom Nutzer übersprungen")
            else:
                # Vorgeladenes Wav2Vec2-Modell durchreichen.
                gruppe_c.append((
                    "emotionale_variation",
                    lambda: pipeline.run_emotion(audio_pfad, emotion_model=EMOTION_MODEL),
                ))
        else:
            skip("lautstaerke_analyse",     "keine Audio-Datei")
            skip("pitch_variation_analyse", "keine Audio-Datei")
            skip("emotionale_variation",    "keine Audio-Datei")

        parallel_schritte(gruppe_c)

        schritt("gesamtscore", pipeline.run_gesamtscore)

        with JOBS_LOCK:
            job["status"] = "done"
            gesamt_pfad = pipeline.MODUL_OUTPUTS["gesamt"]
            if gesamt_pfad.exists():
                daten = json.loads(gesamt_pfad.read_text(encoding="utf-8"))
                job["gesamtscore"] = daten.get("gesamtscore")
                job["modalitaeten"] = daten.get("modalitaets_scores")

    except Exception as e:
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(e)
    finally:
        _speichere_verlauf_eintrag(job)
        with JOBS_LOCK:
            AKTIVER_JOB = None


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="präsentation_ai")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/pipeline-schritte")
def pipeline_schritte():
    return [{"key": k, "label": l} for k, l in PIPELINE_SCHRITTE]


@app.post("/api/analyze")
async def analyze(
    video: UploadFile = File(...),
    skip_video: bool = Form(False),
    skip_emotion: bool = Form(False),
):
    global AKTIVER_JOB

    with JOBS_LOCK:
        if AKTIVER_JOB is not None and JOBS.get(AKTIVER_JOB, {}).get("status") == "running":
            raise HTTPException(409, "Es läuft bereits eine Analyse. Bitte warten, bis sie fertig ist.")

    if not video.filename:
        raise HTTPException(400, "Keine Datei erhalten.")

    suffix = Path(video.filename).suffix or ".mp4"
    ziel_name = f"{uuid.uuid4().hex[:8]}_{Path(video.filename).stem}{suffix}"
    ziel_pfad = UPLOADS_DIR / ziel_name
    with ziel_pfad.open("wb") as f:
        shutil.copyfileobj(video.file, f)

    flags = {"skip_video": skip_video, "skip_emotion": skip_emotion}
    job = _neuer_job(video.filename, flags)
    JOBS[job["id"]] = job
    with JOBS_LOCK:
        AKTIVER_JOB = job["id"]

    thread = threading.Thread(target=_run_pipeline, args=(job["id"], ziel_pfad, flags), daemon=True)
    thread.start()

    return {"job_id": job["id"]}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job unbekannt (Server evtl. seit dem Lauf neugestartet).")
    with JOBS_LOCK:
        return dict(job)


@app.get("/api/verlauf")
def verlauf():
    reg = _lade_verlauf()
    reg.sort(key=lambda e: e["created_at"], reverse=True)
    return reg


@app.get("/api/report/gesamt")
def report_gesamt():
    pfad = pipeline.MODUL_OUTPUTS["gesamt"]
    if not pfad.exists():
        raise HTTPException(404, "Noch kein Gesamtreport vorhanden.")
    return JSONResponse(json.loads(pfad.read_text(encoding="utf-8")))


def _neuester_report(modul: str, ebene: str = "kurz") -> Path:
    """
    Sucht den neuesten Report einer Ebene ("kurz" oder "detail").
    Module ohne eigene Ebenen (z.B. inhalt, gesamt) ignorieren `ebene`
    und liefern einfach die neueste Datei im Ordner.
    """
    if modul == "inhalt":
        ordner = pipeline.REPORTS_ROOT / "inhalt"
        if not ordner.exists():
            raise HTTPException(404, f"Kein Report-Ordner für 'inhalt'.")
        gefiltert = sorted(ordner.glob(f"inhalt_analyse_{ebene}_*.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        dateien = gefiltert if gefiltert else sorted(
            ordner.glob("inhalt_analyse_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        unterordner = MODUL_REPORT_ORDNER.get(modul, modul)
        ordner = pipeline.REPORTS_ROOT / unterordner
        if not ordner.exists():
            raise HTTPException(404, f"Kein Report-Ordner für '{modul}'.")
        # Erst nach Ebene filtern (z.B. "pausen_kurz_*.txt"); falls das
        # Modul noch nicht auf kurz/detail umgestellt ist, auf alle *.txt
        # zurückfallen, damit alte Module weiter funktionieren.
        gefiltert = sorted(ordner.glob(f"*_{ebene}_*.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        dateien = gefiltert if gefiltert else sorted(
            ordner.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dateien:
        raise HTTPException(404, f"Noch kein Report für '{modul}'.")
    return dateien[0]


@app.get("/api/report/{modul}/txt")
def report_modul_txt(modul: str, ebene: str = "kurz"):
    pfad = _neuester_report(modul, ebene)
    return {"filename": pfad.name, "content": pfad.read_text(encoding="utf-8"), "ebene": ebene}


@app.get("/api/report/{modul}/download")
def report_modul_download(modul: str, ebene: str = "kurz"):
    pfad = _neuester_report(modul, ebene)
    return FileResponse(pfad, filename=pfad.name, media_type="text/plain")


# ============================================================================
# DATEIEN-API  (alle Outputs, Reports, Transkripte)
# ============================================================================

def _datei_eintrag(pfad: Path, kategorie: str, label: str) -> dict:
    stat = pfad.stat()
    return {
        "kategorie": kategorie,
        "label":     label,
        "filename":  pfad.name,
        "groesse":   stat.st_size,
        "geaendert": stat.st_mtime,
    }


@app.get("/api/dateien")
def alle_dateien():
    """
    Listet alle herunterladbaren Ausgabedateien auf:
      - Transkripte (.txt)
      - Reports pro Modul (.txt)
      - JSON-Zwischenoutputs
    """
    eintraege = []

    # ── Transkripte
    transkript_dir = pipeline.TRANSKRIPTE_DIR
    if transkript_dir.exists():
        for p in sorted(transkript_dir.glob("*_transkript.txt"), key=lambda x: x.stat().st_mtime, reverse=True):
            eintraege.append(_datei_eintrag(p, "transkript", p.name))

    # ── Reports — alle Unterordner
    if pipeline.REPORTS_ROOT.exists():
        # Reports in Unterordnern (inkl. inhalt/)
        for unterordner in sorted(pipeline.REPORTS_ROOT.iterdir()):
            if unterordner.is_dir():
                for p in sorted(unterordner.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True):
                    eintraege.append(_datei_eintrag(p, f"report_{unterordner.name}", p.name))

    # ── JSON-Zwischenoutputs
    if pipeline.ZWISCHEN_OUTPUT.exists():
        for p in sorted(pipeline.ZWISCHEN_OUTPUT.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name == "ui_verlauf.json":
                continue  # interner Status, nicht relevant für den Nutzer
            eintraege.append(_datei_eintrag(p, "json", p.name))

    return eintraege


@app.get("/api/dateien/download")
def datei_download(pfad: str):
    """Lädt eine beliebige Ausgabedatei herunter (pfad relativ zum Projekt-Root)."""
    # Sicherheit: nur Dateien innerhalb des Projekt-Roots erlauben
    ziel = (APP_ROOT / pfad).resolve()
    if not str(ziel).startswith(str(APP_ROOT.resolve())):
        raise HTTPException(403, "Zugriff verweigert.")
    if not ziel.exists() or not ziel.is_file():
        raise HTTPException(404, "Datei nicht gefunden.")
    media = "application/json" if ziel.suffix == ".json" else "text/plain"
    return FileResponse(ziel, filename=ziel.name, media_type=media)


@app.get("/api/dateien/lesen")
def datei_lesen(pfad: str):
    """Gibt den Textinhalt einer Datei zurück (nur .txt und .json)."""
    ziel = (APP_ROOT / pfad).resolve()
    if not str(ziel).startswith(str(APP_ROOT.resolve())):
        raise HTTPException(403, "Zugriff verweigert.")
    if not ziel.exists() or not ziel.is_file():
        raise HTTPException(404, "Datei nicht gefunden.")
    if ziel.suffix not in (".txt", ".json"):
        raise HTTPException(400, "Nur .txt und .json können angezeigt werden.")
    return {"filename": ziel.name, "content": ziel.read_text(encoding="utf-8")}


if __name__ == "__main__":
    import uvicorn

    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    print("\n  präsentation_ai — läuft lokal auf http://127.0.0.1:8000")
    print("  (Zum Beenden: Strg+C in diesem Fenster)\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
