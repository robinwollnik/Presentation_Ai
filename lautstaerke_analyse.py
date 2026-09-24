#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lautstaerke_analyse.py
======================
Bewertet Lautstärke-Variation und ob Kernbotschaften bewusst lauter gesprochen werden.

Input:
  - Audio-Datei (wav, mp3, etc. — alles was librosa lädt)
  - inhalt_analyse_output.json (für Segmente, Kernbotschaften, Struktur)

Output:
  - zwischen_output/lautstaerke_analyse_output.json
  - reports/lautstaerke/lautstaerke_report_[TIMESTAMP].txt"""

import json
import re
import math
import warnings
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

import numpy as np

import report_utils as ru

# Librosa wird dynamisch importiert (für bessere Fehlermeldung falls nicht installiert)
try:
    import librosa
    import librosa.display
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False
    warnings.warn("librosa nicht installiert. Bitte installieren: pip install librosa soundfile")


# =============================================================================
# KONSTANTEN
# =============================================================================

SAMPLE_RATE = 16000          # 16 kHz Mono
HOP_LENGTH_MS = 10           # 10 ms Hop-Length
FRAME_LENGTH_MS = 20         # 20 ms Fenster

ROLLING_MEDIAN_S = 30        # 30 Sekunden Rolling Median für Baseline

# dB-Referenz (willkürlich, da wir nur relative Differenzen brauchen)
DB_REF = 1.0

# Scoring-Gewichtung
GEWICHT_D1 = 0.40
GEWICHT_D2 = 0.30
GEWICHT_D3 = 0.30


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class Segment:
    """Ein Zeitsegment mit Label (z.B. Kernbotschaft, Einleitung, etc.)."""
    label: str           # z.B. "kernbotschaft", "einleitung", "hauptteil", "schluss", "übergang"
    start_s: float
    end_s: float

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
        }


@dataclass
class LautstaerkeErgebnis:
    """Aggregiertes Ergebnis der Lautstärke-Analyse."""
    rms_db: np.ndarray           # RMS-Werte in dB pro Frame
    times_s: np.ndarray          # Zeitstempel pro Frame
    baseline_db: np.ndarray      # Rolling Median Baseline
    segments: List[Segment]

    d1_score: int
    d1_db_diff: float
    d1_bewertung: str
    d1_kb_details: List[Dict]

    d2_score: int
    d2_std_db: float
    d2_bewertung: str

    d3_score: int
    d3_anteil_im_bereich: float
    d3_bewertung: str
    d3_segment_details: List[Dict]

    gesamtscore: int


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def zeitstr_to_s(zeit_str: str) -> float:
    """Parst Zeitstempel zu Sekunden."""
    zeit_str = zeit_str.strip()
    if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}$", zeit_str):
        h, m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", zeit_str):
        m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(m) * 60 + int(s) + int(ms) / 1000.0
    # Fallback: direkt als Sekunden oder Millisekunden
    try:
        val = float(zeit_str)
        if val > 10000:  # Wahrscheinlich ms
            return val / 1000.0
        return val
    except ValueError:
        raise ValueError(f"Unbekanntes Zeitformat: {zeit_str}")


def s_to_zeitstr(sekunden: float) -> str:
    """Sekunden zu MM:SS.mmm."""
    sekunden = max(0, sekunden)
    m = int(sekunden // 60)
    s = int(sekunden % 60)
    ms = int((sekunden % 1) * 1000)
    return f"{m:02d}:{s:02d}.{ms:03d}"


def lade_json(pfad: Path) -> Optional[Dict]:
    if not pfad.exists():
        return None
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Konnte {pfad} nicht laden: {e}")
        return None


# =============================================================================
# AUDIO-VERARBEITUNG
# =============================================================================

def lade_audio(audio_pfad: Path) -> Tuple[np.ndarray, int]:
    """Lädt Audio mit librosa, konvertiert zu 16kHz Mono."""
    if not HAS_LIBROSA:
        raise ImportError("librosa ist nicht installiert. Installieren mit: pip install librosa soundfile")

    print(f"[lautstaerke] Lade Audio: {audio_pfad.name}")
    y, sr = librosa.load(str(audio_pfad), sr=SAMPLE_RATE, mono=True)
    print(f"[lautstaerke] Audio geladen: {len(y)/SAMPLE_RATE:.2f}s @ {SAMPLE_RATE}Hz, Mono")
    return y, sr


def berechne_rms_db(y: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Berechnet RMS in dB.

    Returns:
        rms_db: RMS-Werte in dB pro Frame
        times_s: Zeitstempel in Sekunden pro Frame (Frame-Center)
    """
    hop_length = int(sr * HOP_LENGTH_MS / 1000)   # 10 ms in Samples
    frame_length = int(sr * FRAME_LENGTH_MS / 1000)  # 20 ms in Samples

    # RMS berechnen
    rms = librosa.feature.rms(
        y=y,
        frame_length=frame_length,
        hop_length=hop_length,
        center=True
    )[0]  # Shape: (n_frames,)

    # In dB umrechnen
    # Vermeide log(0) durch kleinen Offset
    rms_safe = np.maximum(rms, 1e-10)
    rms_db = 20.0 * np.log10(rms_safe / DB_REF)

    # Zeitstempel (Frame-Center)
    times_s = librosa.frames_to_time(
        np.arange(len(rms_db)),
        sr=sr,
        hop_length=hop_length
    )

    return rms_db, times_s


def berechne_rolling_median(rms_db: np.ndarray, sr: int) -> np.ndarray:
    """
    Berechnet Rolling Median über 30 Sekunden als Baseline.
    Kompensiert Mikrofonabstand-Schwankungen.
    """
    hop_length = int(sr * HOP_LENGTH_MS / 1000)
    frames_pro_30s = int(30.0 * sr / hop_length)

    # Mindestens 1 Frame
    frames_pro_30s = max(frames_pro_30s, 1)

    # Rolling Median mit gleitendem Fenster
    baseline = np.zeros_like(rms_db)
    half_window = frames_pro_30s // 2

    for i in range(len(rms_db)):
        start = max(0, i - half_window)
        end = min(len(rms_db), i + half_window + 1)
        baseline[i] = np.median(rms_db[start:end])

    return baseline


# =============================================================================
# SEGMENT-EXTRAKTION
# =============================================================================

def extrahiere_segmente(inhalt_data: Optional[Dict]) -> List[Segment]:
    """
    Extrahiert Segmente aus der Inhaltsanalyse:
    - Kernbotschaften
    - Struktur-Segmente (Einleitung, Hauptteil, Schluss)
    - Übergänge zwischen Segmenten
    """
    segments = []

    if not inhalt_data:
        return segments

    # 1. Kernbotschaften
    for kb in inhalt_data.get("kernbotschaften", []):
        start = kb.get("start_ms", kb.get("start"))
        end = kb.get("end_ms", kb.get("end"))
        if isinstance(start, str):
            start = zeitstr_to_s(start)
        if isinstance(end, str):
            end = zeitstr_to_s(end)
        if start is not None and end is not None:
            segments.append(Segment("kernbotschaft", float(start) / 1000.0, float(end) / 1000.0))

    # 2. Struktur-Segmente
    struktur = inhalt_data.get("struktur", {})
    if isinstance(struktur, dict):
        for key in ["einleitung", "hauptteil", "schluss"]:
            if key in struktur:
                seg = struktur[key]
                start = seg.get("start_ms", seg.get("start"))
                end = seg.get("end_ms", seg.get("end"))
                if isinstance(start, str):
                    start = zeitstr_to_s(start)
                if isinstance(end, str):
                    end = zeitstr_to_s(end)
                if start is not None and end is not None:
                    segments.append(Segment(key, float(start) / 1000.0, float(end) / 1000.0))

        # 3. Übergänge (zwischen Segmenten)
        segment_list = []
        for key in ["einleitung", "hauptteil", "schluss"]:
            if key in struktur:
                seg = struktur[key]
                start = seg.get("start_ms", seg.get("start"))
                end = seg.get("end_ms", seg.get("end"))
                if isinstance(start, str):
                    start = zeitstr_to_s(start)
                if isinstance(end, str):
                    end = zeitstr_to_s(end)
                if start is not None and end is not None:
                    segment_list.append((float(start) / 1000.0, float(end) / 1000.0, key))

        segment_list.sort()
        for i in range(1, len(segment_list)):
            prev_end = segment_list[i - 1][1]
            curr_start = segment_list[i][0]
            if curr_start > prev_end:
                segments.append(Segment("uebergang", prev_end, curr_start))

    return segments


def extrahiere_nebensaetze(inhalt_data: Optional[Dict]) -> List[Segment]:
    """Extrahiert Nicht-Kernbotschaft-Segmente als 'Nebensätze' für die Baseline."""
    segments = []

    if not inhalt_data or "satzgrenzen" not in inhalt_data:
        return segments

    # Alle Sätze minus Kernbotschaften = Nebensätze
    saetze = inhalt_data["satzgrenzen"]
    kern_starts = set()
    kern_ends = set()

    for kb in inhalt_data.get("kernbotschaften", []):
        s = kb.get("start_ms", kb.get("start"))
        e = kb.get("end_ms", kb.get("end"))
        if isinstance(s, str):
            s = zeitstr_to_s(s)
        if isinstance(e, str):
            e = zeitstr_to_s(e)
        if s is not None and e is not None:
            kern_starts.add(float(s) / 1000.0)
            kern_ends.add(float(e) / 1000.0)

    for satz in saetze:
        start = satz.get("start_ms", satz.get("start"))
        end = satz.get("end_ms", satz.get("end"))
        if isinstance(start, str):
            start = zeitstr_to_s(start)
        if isinstance(end, str):
            end = zeitstr_to_s(end)
        if start is None or end is None:
            continue

        start_s = float(start) / 1000.0
        end_s = float(end) / 1000.0

        # Prüfe ob dieser Satz eine Kernbotschaft ist
        ist_kb = False
        for ks, ke in zip(kern_starts, kern_ends):
            if start_s <= ke and end_s >= ks:
                ist_kb = True
                break

        if not ist_kb:
            segments.append(Segment("nebensatz", start_s, end_s))

    return segments


# =============================================================================
# SEGMENT-ANALYSE
# =============================================================================

def maske_fuer_segment(rms_db: np.ndarray, times_s: np.ndarray, segment: Segment) -> np.ndarray:
    """Erzeugt Boolean-Maske für Frames innerhalb eines Segments."""
    return (times_s >= segment.start_s) & (times_s <= segment.end_s)


def mittlerer_db(rms_db: np.ndarray, maske: np.ndarray) -> Optional[float]:
    """Mittlerer dB-Wert für maskierte Frames."""
    werte = rms_db[maske]
    if len(werte) == 0:
        return None
    return float(np.mean(werte))


def std_db(rms_db: np.ndarray, maske: np.ndarray) -> Optional[float]:
    """Standardabweichung in dB für maskierte Frames."""
    werte = rms_db[maske]
    if len(werte) < 2:
        return None
    return float(np.std(werte, ddof=1))


# =============================================================================
# SCORING
# =============================================================================

def score_d1_kernbotschaftsbetonung(
    rms_db: np.ndarray,
    times_s: np.ndarray,
    kernbotschaften: List[Segment],
    nebensaetze: List[Segment]
) -> Tuple[int, float, str, List[Dict]]:
    """
    D1: Kernbotschafts-Betonung (40%)
    dB-Differenz = RMS(Kernbotschaften) − RMS(Nebensätze)

    Gibt zusätzlich pro-Kernbotschaft Details zurück (für "Wo genau" im
    Detail-Report), damit nicht nur der Durchschnitt sichtbar ist.
    """
    # Mittlerer dB der Kernbotschaften
    kb_mask = np.zeros(len(rms_db), dtype=bool)
    for kb in kernbotschaften:
        kb_mask |= maske_fuer_segment(rms_db, times_s, kb)

    kb_mean = mittlerer_db(rms_db, kb_mask)

    # Mittlerer dB der Nebensätze
    ns_mask = np.zeros(len(rms_db), dtype=bool)
    for ns in nebensaetze:
        ns_mask |= maske_fuer_segment(rms_db, times_s, ns)

    # Fallback: Wenn keine Nebensätze definiert, nimm alles außer Kernbotschaften
    if not np.any(ns_mask):
        ns_mask = ~kb_mask

    ns_mean = mittlerer_db(rms_db, ns_mask)

    if kb_mean is None or ns_mean is None:
        return 50, 0.0, "Nicht genug Daten", []

    db_diff = kb_mean - ns_mean

    if db_diff >= 3.0:
        punkte = 100
        bewertung = "Gut betont"
    elif db_diff >= 1.0:
        punkte = 75
        bewertung = "Leicht betont"
    elif db_diff >= -1.0:
        punkte = 50
        bewertung = "Nicht betont"
    else:
        punkte = 20
        bewertung = "Kernbotschaften leiser"

    # Pro-Kernbotschaft-Details, damit der Report auf jede einzelne
    # Kernaussage zeigen kann, statt nur auf den Durchschnitt.
    kb_details = []
    for kb in kernbotschaften:
        einzel_mask = maske_fuer_segment(rms_db, times_s, kb)
        einzel_mean = mittlerer_db(rms_db, einzel_mask)
        if einzel_mean is None:
            continue
        kb_details.append({
            "start_s": kb.start_s,
            "end_s": kb.end_s,
            "db_diff": einzel_mean - ns_mean,
        })

    return punkte, db_diff, bewertung, kb_details


def score_d2_gesamtvariation(rms_db: np.ndarray) -> Tuple[int, float, str]:
    """
    D2: Gesamt-Variation (30%)
    Standardabweichung in dB über die gesamte Präsentation.
    """
    if len(rms_db) < 2:
        return 40, 0.0, "Nicht genug Daten"

    std = float(np.std(rms_db, ddof=1))

    if 3.0 <= std <= 6.0:
        punkte = 100
        bewertung = "Optimal"
    elif 6.0 < std <= 9.0:
        punkte = 85
        bewertung = "Etwas viel"
    elif 1.5 <= std < 3.0:
        punkte = 65
        bewertung = "Gering"
    elif std < 1.5:
        punkte = 40
        bewertung = "Monoton"
    else:  # > 9.0
        punkte = 40
        bewertung = "Chaotisch"

    return punkte, std, bewertung


def score_d3_strukturkonsistenz(
    rms_db: np.ndarray,
    times_s: np.ndarray,
    baseline_db: np.ndarray,
    segments: List[Segment],
    nebensaetze: Optional[List[Segment]] = None,
) -> Tuple[int, float, str, List[Dict]]:
    """
    D3: Struktur-Konsistenz (30%)
    Vergleicht Ist-Mittelwert pro Struktur-Segment mit erwartetem Bereich.

    Erwartete Bereiche (v2-konform, relativ zur Baseline):
    - Einleitung:    Baseline ± 2 dB,    Toleranz 1 dB
    - Hauptteil:     Baseline ± 1 dB,    Toleranz 0.5 dB (Fix: nicht 0)
    - Kernbotschaft: Baseline +3 bis +6 dB, Toleranz 1.5 dB
    - Übergang:      Baseline −1 bis +1 dB, Toleranz 1 dB
    - Schluss:       Baseline ± 3 dB,    Toleranz 1.5 dB

    Baseline (v2-Fix): Median aller Frames im Nebensatz-Zeitbereich.
    Nur wenn keine Nebensätze aus inhalt_analyse verfügbar sind, wird auf
    den globalen Median zurückgefallen.

    Scoring: >=80% -> 100, 60-80% -> 75, 40-60% -> 50, <40% -> 25
    """

    # v2-Fix: Baseline aus Nebensatz-Frames statt Global-Median
    baseline_source = "global_median"
    if nebensaetze:
        ns_mask = np.zeros(len(rms_db), dtype=bool)
        for ns in nebensaetze:
            ns_mask |= maske_fuer_segment(rms_db, times_s, ns)
        if np.any(ns_mask):
            baseline_median = float(np.median(rms_db[ns_mask]))
            baseline_source = "nebensaetze"
        else:
            baseline_median = float(np.median(rms_db))
    else:
        baseline_median = float(np.median(rms_db))

    # Erwartete Bereiche definieren (v2-Fix: hauptteil Toleranz 0.5 statt 0)
    erwartet = {
        "einleitung":     (baseline_median - 2.0, baseline_median + 2.0, 1.0),
        "hauptteil":      (baseline_median - 1.0, baseline_median + 1.0, 0.5),
        "nebensatz":      (baseline_median - 1.0, baseline_median + 1.0, 0.5),
        "kernbotschaft":  (baseline_median + 3.0, baseline_median + 6.0, 1.5),
        "uebergang":      (baseline_median - 1.0, baseline_median + 1.0, 1.0),
        "schluss":        (baseline_median - 3.0, baseline_median + 3.0, 1.5),
    }

    details = []
    im_bereich_count = 0
    gesamt_count = 0

    for seg in segments:
        if seg.label not in erwartet:
            continue

        maske = maske_fuer_segment(rms_db, times_s, seg)
        mean_val = mittlerer_db(rms_db, maske)

        if mean_val is None:
            continue

        min_exp, max_exp, toleranz = erwartet[seg.label]

        # Mit Toleranz prüfen
        in_range = (mean_val >= min_exp - toleranz) and (mean_val <= max_exp + toleranz)

        details.append({
            "label": seg.label,
            "start_s": round(seg.start_s, 3),
            "end_s": round(seg.end_s, 3),
            "ist_db": round(mean_val, 2),
            "erwartet_min": round(min_exp, 2),
            "erwartet_max": round(max_exp, 2),
            "toleranz": toleranz,
            "im_bereich": in_range,
        })

        gesamt_count += 1
        if in_range:
            im_bereich_count += 1

    if gesamt_count == 0:
        return 50, 0.0, "Keine Struktur-Segmente", details

    anteil = im_bereich_count / gesamt_count

    if anteil >= 0.80:
        punkte = 100
        bewertung = "Sehr konsistent"
    elif anteil >= 0.60:
        punkte = 75
        bewertung = "Gut"
    elif anteil >= 0.40:
        punkte = 50
        bewertung = "Verbesserbar"
    else:
        punkte = 25
        bewertung = "Inkonsistent"

    return punkte, anteil, bewertung, details


def berechne_gesamtscore(d1: int, d2: int, d3: int) -> int:
    score = d1 * GEWICHT_D1 + d2 * GEWICHT_D2 + d3 * GEWICHT_D3
    return int(round(score))


# =============================================================================
# REPORT
# =============================================================================


LAUTSTAERKE_ACHSEN = [
    ru.Achse(
        "kernaussage_ohne_betonung", "praesenz", prioritaet=1,
        merkmal_key="ohne_betonung", merkmal_wert=True, min_evidenz=2,
        befund_template="{n} deiner Kernaussagen sind nicht spürbar lauter als der Rest (unter +3 dB).",
        ursache_template="Ohne hörbare Betonung geht die Kernaussage im Redefluss unter.",
        uebung_template=(
            "\nSchritt 1: Sprich nur diese Kernaussagen, jeweils bewusst "
            "deutlich lauter als deine normale Sprechlautstärke, 5x pro Satz.\n"
            "Schritt 2: Sprich den Satz davor normal, dann die Kernaussage "
            "laut — übe den Lautstärke-Sprung 3x.\n"
            "Schritt 3: Nimm dich auf und prüfe: ist der Unterschied klar "
            "hörbar (+3 bis +6 dB)?"
        ),
    ),
    ru.Achse(
        "kernaussage_inkonsistent", "anteil", prioritaet=2,
        merkmal_key="ohne_betonung", merkmal_wert=True, min_evidenz=2, schwelle=0.3,
        befund_template="Deine Kernaussagen sind unterschiedlich stark betont — bei manchen wirst du deutlich lauter, bei anderen kaum.",
        ursache_template="Uneinheitliche Betonung macht es dem Publikum schwerer, deine wichtigsten Punkte zu erkennen.",
        uebung_template=(
            "\nSchritt 1: Liste dir alle Kernaussagen im Text auf.\n"
            "Schritt 2: Sprich jede einzeln mit demselben bewussten "
            "Lautstärke-Sprung, 3x pro Satz.\n"
            "Schritt 3: Sprich die ganze Präsentation durch und achte "
            "gezielt darauf, dass alle gleich stark betont klingen."
        ),
    ),
    ru.Achse(
        "kernaussage_durchgehend_gut", "anteil", prioritaet=3,
        merkmal_key="ohne_betonung", merkmal_wert=False, min_evidenz=2, schwelle=0.8,
        befund_template="Alle {n} deiner Kernaussagen sind konsistent gut betont.",
        ursache_template="Das ist genau das Muster, das eine Präsentation überzeugend klingen lässt.",
        uebung_template="Behalte dieses Timing bei — hier gibt es nichts zu verbessern.",
    ),
    ru.Achse(
        "struktur_abweichung", "anteil", prioritaet=1,
        merkmal_key="im_zielbereich", merkmal_wert=False,
        min_evidenz=2, schwelle=0.4,
        befund_template="{anteil:.0%} deiner Struktur-Segmente (Einleitung/Höhepunkt/Schluss) liegen außerhalb der erwarteten Lautstärke.",
        ursache_template="Das schwächt die hörbare Gliederung deiner Präsentation.",
        uebung_template=(
            "\nSchritt 1: Sprich nur deine Einleitung, bewusst spürbar "
            "lauter/energischer als dein Grundton.\n"
            "Schritt 2: Sprich nur deinen Schluss, bewusst ruhiger und "
            "klarer abgesetzt.\n"
            "Schritt 3: Sprich die ganze Präsentation durch, mit bewusstem "
            "Fokus auf diese Lautstärke-Gliederung."
        ),
    ),
    ru.Achse(
        "zeittrend_leiser", "trend", prioritaet=2, zeit_key="start_ms",
        befund_template="Deine Lautstärke {richtung_text}.",
        ursache_template="{richtung_ursache}",
        uebung_template="{richtung_uebung}",
    ),
]


def _lautstaerke_fallback_tipps(score: int) -> List[str]:
    if score >= 75:
        return ["Halte die bewusste Betonung bei Kernaussagen aufrecht — das ist "
                "eines der stärksten Signale für dein Publikum."]
    if score >= 50:
        return ["Übe gezielt, deine 3-5 wichtigsten Sätze merklich lauter zu "
                "sprechen (+3 bis +6 dB über deinem Durchschnitt)."]
    return ["Sprich grundsätzlich lauter als du denkst, dass du musst — Räume "
            "und Mikrofone schlucken Pegel. Lautes Sprechen kommt aus dem "
            "Zwerchfell, nicht aus der Kehle."]


# =============================================================================
# REPORT — KURZFASSUNG
# =============================================================================

def generiere_kurz_report(
    ergebnis: LautstaerkeErgebnis,
    audio_name: str,
    audio_dauer_s: float,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"
    z = ru.kurz_header("LAUTSTÄRKE", audio_name, dauer_str)

    if audio_dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({audio_dauer_s:.0f} Sek.) — Details dazu in der")
        z.append("    ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        ergebnis.gesamtscore,
        "Deine Lautstärke-Variation ist überzeugend und gut strukturiert.",
        "Deine Lautstärke ist ausbaufähig. Einzelne Bereiche brauchen Arbeit.",
        "Deine Lautstärke weicht deutlich vom Optimum ab — zu monoton oder zu unstrukturiert.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Kernbotschafts-Betonung", 40, ergebnis.d1_score, ergebnis.d1_bewertung)
    z += ru.dimension_zeile_kurz("Gesamt-Variation", 30, ergebnis.d2_score, ergebnis.d2_bewertung)
    z += ru.dimension_zeile_kurz("Struktur-Konsistenz", 30, ergebnis.d3_score, ergebnis.d3_bewertung)
    z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    for i, zeile in enumerate(_lautstaerke_fallback_tipps(ergebnis.gesamtscore), 1):
        umbrochen = ru.wrap_text(zeile) if len(zeile) > 64 else [zeile]
        z.append(f"  {i}. {umbrochen[0]}")
        z += [f"     {folgezeile}" for folgezeile in umbrochen[1:]]
    z.append("")
    z.append("  Wo genau im Video deine Lautstärke schwankt, und warum diese")
    z.append("  Punktzahl herauskommt, steht im ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)
    return "\n".join(z)


# =============================================================================
# REPORT — DETAILANSICHT
# =============================================================================

def generiere_detail_report(
    ergebnis: LautstaerkeErgebnis,
    audio_name: str,
    audio_dauer_s: float,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"

    z = ru.detail_header("LAUTSTÄRKE", audio_name, dauer_str)
    z += ru.build_toc([
        "Gesamtergebnis",
        "Kernbotschafts-Betonung — Begründung & Fundstellen",
        "Gesamt-Variation — Begründung",
        "Struktur-Konsistenz — Begründung & Fundstellen",
        "Hintergrund & Referenzwerte",
    ])
    z.append("  dB (Dezibel) misst Lautstärke — je größer, desto lauter. Deine")
    z.append("  Baseline ist dein durchschnittlicher Pegel über die Aufnahme.")
    z.append("")

    z += ru.gesamtergebnis_block(
        ergebnis.gesamtscore,
        "Deine Lautstärke-Variation ist überzeugend und gut strukturiert.",
        "Deine Lautstärke ist ausbaufähig. Einzelne Bereiche brauchen Arbeit.",
        "Deine Lautstärke weicht deutlich vom Optimum ab — zu monoton oder zu unstrukturiert.",
    )
    z += ru.kleine_stichprobe_warnung(audio_dauer_s)

    # ── D1 — Kernbotschafts-Betonung ─────────────────────────────────────────
    fundstellen_d1 = []
    for kb in ergebnis.d1_kb_details[:6]:
        status = "gut betont" if kb["db_diff"] >= 3.0 else "NICHT betont"
        fundstellen_d1.append(ru.fundstelle_zeile(
            kb["start_s"] * 1000, f"Kernaussage {s_to_zeitstr(kb['start_s'])}–{s_to_zeitstr(kb['end_s'])}",
            f"{kb['db_diff']:+.1f} dB — {status}"))
    if not fundstellen_d1:
        fundstellen_d1 = ["  Keine ausreichend langen Kernaussage-Segmente für eine "
                           "  Einzelauswertung gefunden."]

    d1_befunde = []
    for kb in ergebnis.d1_kb_details:
        ohne_betonung = kb["db_diff"] < 3.0
        d1_befunde.append({
            "ohne_betonung": ohne_betonung,
            "db_diff_text": f"{kb['db_diff']:+.1f} dB",
            "betont_text": "nicht spürbar lauter als sonst" if ohne_betonung else "deutlich lauter als der Rest",
            "einzelfund_uebung": (
                "\nSchritt 1: Sprich genau diesen Satz 5x, bewusst deutlich "
                "lauter als sonst.\n"
                "Schritt 2: Sprich den Satz davor normal, dann diesen Satz "
                "laut — übe den Lautstärke-Sprung 3x.\n"
                "Schritt 3: Nimm dich auf und prüfe: ist der Unterschied "
                "jetzt klar hörbar?"
                if ohne_betonung else
                "Genau richtig gemacht — behalte dieses Timing bei."
            ),
            "start_ms": kb["start_s"] * 1000,
        })
    tipp_d1 = ru.erkenne_muster_v2(
        d1_befunde,
        [a for a in LAUTSTAERKE_ACHSEN if a.name in ("kernaussage_ohne_betonung", "kernaussage_inkonsistent", "kernaussage_durchgehend_gut")],
        gesamt_dauer_ms=audio_dauer_s * 1000, max_tipps=1,
        fall_a_text=[
            "Es gab keine ausreichend langen Kernaussage-Abschnitte für eine "
            "verlässliche Lautstärke-Messung.",
        ],
        einzelfund_template=(
            "Diese Kernaussage ist {betont_text} (Unterschied: "
            "{db_diff_text}). {einzelfund_uebung}"
        ),
        fall_c_einleitung="Deine Kernaussagen weichen unterschiedlich stark ab, ohne einheitliches Muster:",
    )

    z += ru.dimension_block_detail(
        "Kernbotschafts-Betonung", 40, ergebnis.d1_score,
        was_gemessen=["Ob du bei deiner wichtigsten Aussage lauter sprichst als sonst",
                      "— das hebt sie hervor."],
        warum=[f"Deine Kernaussagen sind im Schnitt {ergebnis.d1_db_diff:+.2f} dB lauter",
               "als der Rest. Faustregel: +3 bis +6 dB über der Baseline gilt als",
               "gut hörbare Betonung.",
               f"Bei dir: {ergebnis.d1_bewertung}."],
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 — Gesamt-Variation ─────────────────────────────────────────────────
    z += ru.dimension_block_detail(
        "Gesamt-Variation", 30, ergebnis.d2_score,
        was_gemessen=["Wie stark deine Lautstärke insgesamt schwankt — zu",
                      "gleichförmig wirkt eintönig, zu wild wirkt unruhig."],
        warum=[f"Deine Lautstärke schwankt um {ergebnis.d2_std_db:.2f} dB um deinen",
               "Durchschnitt. Faustregel: 4-9 dB = lebendig, unter 3 dB = eintönig,",
               "über 9 dB = chaotisch.",
               f"Bei dir: {ergebnis.d2_bewertung}."],
        fundstellen=[],
        tipp=["Sprich denselben Satz dreimal hintereinander — leise, normal, laut —",
              "damit dein Körper den Zielbereich kennenlernt."],
    )

    # ── D3 — Struktur-Konsistenz ──────────────────────────────────────────────
    fundstellen_d3 = []
    d3_befunde = []
    for d in ergebnis.d3_segment_details:
        status = "✅ im Zielbereich" if d["im_bereich"] else "❌ Abweichung"
        fundstellen_d3.append(ru.fundstelle_zeile(
            d["start_s"] * 1000, d["label"],
            f"{d['ist_db']:+.1f} dB, erwartet {d['erwartet_min']:+.1f} bis {d['erwartet_max']:+.1f} dB — {status}"))
        d3_befunde.append({"im_zielbereich": d["im_bereich"], "start_ms": d["start_s"] * 1000,
                            "d3_einzelfund_text": (
                                f"Der Abschnitt '{d['label']}' liegt im "
                                "erwarteten Lautstärkebereich."
                                if d["im_bereich"] else
                                f"Der Abschnitt '{d['label']}' liegt außerhalb "
                                "der erwarteten Lautstärke.\n"
                                "Schritt 1: Höre dir diesen Abschnitt nochmal an "
                                "und vergleiche mit dem erwarteten Bereich.\n"
                                "Schritt 2: Sprich ihn neu ein, bewusst lauter "
                                "oder leiser Richtung Zielbereich.\n"
                                "Schritt 3: Baue ihn zurück in die Präsentation "
                                "ein und höre den Übergang."
                            )})
    if not fundstellen_d3:
        fundstellen_d3 = ["  Keine Struktur-Segmente erkannt (vermutlich zu kurze Aufnahme"
                           "  für eine Einleitung/Hauptteil/Schluss-Gliederung)."]

    for achse in LAUTSTAERKE_ACHSEN:
        if achse.name == "zeittrend_leiser":
            probe = ru._pruefe_trend(d3_befunde, achse, audio_dauer_s * 1000)
            if probe and getattr(probe, "richtung", "") == "ende":
                achse.befund_template = "Deine Lautstärke nimmt über die Aufnahme kontinuierlich ab."
                achse.ursache_template = "Das kann an nachlassender Atemstütze liegen — lautes, tragendes Sprechen kommt aus dem Zwerchfell, nicht aus der Kehle, und ermüdet mit der Zeit."
                achse.uebung_template = (
                    "\nSchritt 1: Atme vor dem letzten Drittel deiner "
                    "Präsentation bewusst 3x tief in den Bauch ein.\n"
                    "Schritt 2: Sprich den letzten Abschnitt separat, mit "
                    "bewusster Stütze aus dem Zwerchfell.\n"
                    "Schritt 3: Sprich die ganze Präsentation durch und "
                    "vergleiche die Lautstärke am Anfang und Ende."
                )
            elif probe:
                achse.befund_template = "Deine Lautstärke nimmt zu Beginn der Aufnahme zu."
                achse.ursache_template = "Das kann auf einen zurückhaltenden Einstieg hindeuten."
                achse.uebung_template = (
                    "\nSchritt 1: Sprich nur die ersten 2 Sätze, bewusst "
                    "mit voller Lautstärke von Anfang an.\n"
                    "Schritt 2: Wiederhole 3x, bis sich der laute Einstieg "
                    "natürlich anfühlt.\n"
                    "Schritt 3: Sprich die ganze Präsentation durch."
                )

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde,
        [a for a in LAUTSTAERKE_ACHSEN if a.name in ("struktur_abweichung", "zeittrend_leiser")],
        gesamt_dauer_ms=audio_dauer_s * 1000, max_tipps=1,
        fall_a_text=[
            "Keine Struktur-Segmente (wie Einleitung oder Schluss) erkannt — "
            "vermutlich, weil die Aufnahme zu kurz für eine klare Gliederung ist.",
        ],
        einzelfund_template="{d3_einzelfund_text}",
        fall_c_einleitung="Deine Abschnitte weichen unterschiedlich stark ab, ohne einheitliches Muster:",
    )

    z += ru.dimension_block_detail(
        "Struktur-Konsistenz", 30, ergebnis.d3_score,
        was_gemessen=["Ob strukturell wichtige Abschnitte (Einleitung, Höhepunkt,",
                      "Schluss) in der jeweils erwarteten Lautstärke liegen."],
        warum=[f"{ergebnis.d3_anteil_im_bereich:.0%} deiner Struktur-Segmente liegen im",
               f"erwarteten Bereich. Bei dir: {ergebnis.d3_bewertung}."],
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Lautstärke wird per RMS (durchschnittliche Signalenergie) gemessen")
    z.append("  und in Dezibel umgerechnet. Die Baseline ist ein rollender Median")
    z.append("  über 30 Sekunden.")
    z.append("  Referenz Gesamt-Variation: 4-9 dB = gut, unter 3 dB = eintönig.")
    z.append("  Kernbotschafts-Betonung: +3 bis +6 dB über der Baseline empfohlen.")
    z.append("  Quellen: McAllister & Sundberg · Hincks & Edlund (2009) · Toastmasters")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    return "\n".join(z)

# HAUPTFUNKTION
# =============================================================================

def analyse_lautstaerke(
    audio_pfad: Path,
    inhalt_pfad: Optional[Path] = None,
    output_json_pfad: Optional[Path] = None,
    output_txt_kurz_pfad: Optional[Path] = None,
    output_txt_detail_pfad: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Haupt-Einstiegspunkt für die Lautstärke-Analyse.

    Args:
        audio_pfad: Pfad zur Audio-Datei
        inhalt_pfad: Pfad zu inhalt_analyse_output.json
        output_json_pfad: Zielpfad für JSON
        output_txt_kurz_pfad: Zielpfad für Kurz-Report
        output_txt_detail_pfad: Zielpfad für Detail-Report
    """

    if not HAS_LIBROSA:
        raise ImportError(
            "librosa ist nicht installiert.\n"
            "Installieren mit: pip install librosa soundfile\n"
            "Oder: conda install -c conda-forge librosa"
        )

    print(f"[lautstaerke] Starte Analyse: {audio_pfad.name}")

    # 1. Audio laden
    y, sr = lade_audio(audio_pfad)
    audio_dauer_s = len(y) / sr

    # 2. RMS in dB berechnen
    rms_db, times_s = berechne_rms_db(y, sr)
    print(f"[lautstaerke] {len(rms_db)} Frames berechnet.")

    # 3. Rolling Median Baseline
    baseline_db = berechne_rolling_median(rms_db, sr)
    print(f"[lautstaerke] Rolling Median (30s) berechnet.")

    # 4. Inhaltsanalyse laden
    inhalt_data = lade_json(inhalt_pfad) if inhalt_pfad else None

    # 5. Segmente extrahieren
    segments = extrahiere_segmente(inhalt_data)
    nebensaetze = extrahiere_nebensaetze(inhalt_data)
    kernbotschaften = [s for s in segments if s.label == "kernbotschaft"]

    print(f"[lautstaerke] {len(segments)} Segmente extrahiert "
          f"({len(kernbotschaften)} KB, {len(nebensaetze)} Nebensätze).")

    # 6. Scoring
    d1_score, d1_diff, d1_text, d1_kb_details = score_d1_kernbotschaftsbetonung(
        rms_db, times_s, kernbotschaften, nebensaetze
    )
    d2_score, d2_std, d2_text = score_d2_gesamtvariation(rms_db)
    d3_score, d3_anteil, d3_text, d3_details = score_d3_strukturkonsistenz(
        rms_db, times_s, baseline_db, segments, nebensaetze=nebensaetze
    )
    gesamt_score = berechne_gesamtscore(d1_score, d2_score, d3_score)

    print(f"[lautstaerke] Scoring: D1={d1_score}, D2={d2_score}, D3={d3_score}, Gesamt={gesamt_score}")

    # 7. Ergebnis bauen
    ergebnis = LautstaerkeErgebnis(
        rms_db=rms_db,
        times_s=times_s,
        baseline_db=baseline_db,
        segments=segments,
        d1_score=d1_score,
        d1_db_diff=d1_diff,
        d1_bewertung=d1_text,
        d1_kb_details=d1_kb_details,
        d2_score=d2_score,
        d2_std_db=d2_std,
        d2_bewertung=d2_text,
        d3_score=d3_score,
        d3_anteil_im_bereich=d3_anteil,
        d3_bewertung=d3_text,
        d3_segment_details=d3_details,
        gesamtscore=gesamt_score
    )

    # 8. Output
    output_data = {
        "modul": "lautstaerke_analyse",
        "version": "2.0",
        "timestamp": datetime.now().isoformat(),
        "input": str(audio_pfad),
        "meta": {
            "audio_dauer_s": round(audio_dauer_s, 3),
            "sample_rate": SAMPLE_RATE,
            "frame_length_ms": FRAME_LENGTH_MS,
            "hop_length_ms": HOP_LENGTH_MS,
            "frames_anzahl": len(rms_db),
        },
        "statistiken": {
            "rms_db_mean": round(float(np.mean(rms_db)), 2),
            "rms_db_std": round(float(np.std(rms_db, ddof=1)), 2),
            "rms_db_min": round(float(np.min(rms_db)), 2),
            "rms_db_max": round(float(np.max(rms_db)), 2),
            "baseline_db_mean": round(float(np.mean(baseline_db)), 2),
        },
        "segmente": [s.to_dict() for s in segments],
        "scoring": {
            "d1_kernbotschaftsbetonung": {
                "gewichtung": GEWICHT_D1,
                "punkte": d1_score,
                "bewertung": d1_text,
                "db_differenz": round(d1_diff, 2)
            },
            "d2_gesamtvariation": {
                "gewichtung": GEWICHT_D2,
                "punkte": d2_score,
                "bewertung": d2_text,
                "std_db": round(d2_std, 2)
            },
            "d3_strukturkonsistenz": {
                "gewichtung": GEWICHT_D3,
                "punkte": d3_score,
                "bewertung": d3_text,
                "anteil_im_bereich": round(d3_anteil, 4),
                "segment_details": d3_details
            },
            "gesamtscore": gesamt_score
        }
    }

    if output_json_pfad:
        output_json_pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_pfad, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2, default=lambda x: float(x) if isinstance(x, np.floating) else x)
        print(f"[lautstaerke] JSON gespeichert: {output_json_pfad}")

    if output_txt_kurz_pfad:
        output_txt_kurz_pfad.parent.mkdir(parents=True, exist_ok=True)
        kurz_report = generiere_kurz_report(ergebnis, audio_pfad.name, audio_dauer_s)
        with open(output_txt_kurz_pfad, "w", encoding="utf-8") as f:
            f.write(kurz_report)
        print(f"[lautstaerke] Kurz-Report gespeichert: {output_txt_kurz_pfad}")

    if output_txt_detail_pfad:
        output_txt_detail_pfad.parent.mkdir(parents=True, exist_ok=True)
        detail_report = generiere_detail_report(ergebnis, audio_pfad.name, audio_dauer_s)
        with open(output_txt_detail_pfad, "w", encoding="utf-8") as f:
            f.write(detail_report)
        print(f"[lautstaerke] Detail-Report gespeichert: {output_txt_detail_pfad}")

    print(f"[lautstaerke] Fertig. Gesamt-Score: {gesamt_score}/100")
    return output_data


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Lautstärke-Analyse für Präsentationsbewertungs-AI")
    parser.add_argument("audio", type=str, help="Pfad zur Audio-Datei")
    parser.add_argument("--inhalt", type=str, default=None, help="Pfad zu inhalt_analyse_output.json")
    parser.add_argument("--output-json", type=str, default="zwischen_output/lautstaerke_analyse_output.json")
    parser.add_argument("--output-txt-kurz", type=str, default=None)
    parser.add_argument("--output-txt-detail", type=str, default=None)

    args = parser.parse_args()

    audio = Path(args.audio)
    inhalt = Path(args.inhalt) if args.inhalt else None
    out_json = Path(args.output_json)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_txt_kurz = Path(args.output_txt_kurz) if args.output_txt_kurz else Path("reports/lautstaerke") / f"lautstaerke_kurz_{ts}.txt"
    out_txt_detail = Path(args.output_txt_detail) if args.output_txt_detail else Path("reports/lautstaerke") / f"lautstaerke_detail_{ts}.txt"

    if not audio.exists():
        print(f"[FEHLER] Audio nicht gefunden: {audio}")
        exit(1)

    try:
        analyse_lautstaerke(audio, inhalt, out_json, out_txt_kurz, out_txt_detail)
    except Exception as e:
        print(f"[FEHLER] {e}")
        raise
