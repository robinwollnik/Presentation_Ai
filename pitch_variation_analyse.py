#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pitch_variation_analyse.py
==========================
Misst Tonhöhen-Variation (F0) in Semitones.
Bewertet Endkonturen (Fragen steigend, Aussagen fallend) und ob
Kernbotschaften stärker moduliert sind.

Input:
  - Audio-Datei
  - inhalt_analyse_output.json (für Sätze, Kernbotschaften, Satzzeichen)

Output:
  - zwischen_output/pitch_variation_analyse_output.json
  - reports/pitch/pitch_report_[TIMESTAMP].txt"""

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

try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False
    warnings.warn("librosa nicht installiert. pip install librosa soundfile")


# =============================================================================
# KONSTANTEN
# =============================================================================

SAMPLE_RATE = 16000
HOP_LENGTH_MS = 20           # 20 ms Hop-Length (wie im Dokument)

# Semitone-Schwellen
ST_MONOTON = 1.5             # SD < 1.5 ST = monoton
ST_OPTIMAL_MIN = 3.0
ST_OPTIMAL_MAX = 6.0
ST_AUFFAELLIG_MIN = 2.0
ST_AUFFAELLIG_MAX = 8.0
ST_CHAOTISCH_MIN = 10.0

# Endkontur
ENDKONTUR_DAUER_MS = 300     # Letzte 300 ms
ENDKONTUR_STEIGEND = 2.0     # ≥ +2 ST
ENDKONTUR_FALLEND = -2.0     # ≤ −2 ST

# Monoton-Passagen
MONOTON_PASSAGE_S = 8.0      # > 8 Sekunden
MONOTON_PASSAGE_ST = 1.5     # Variation < 1.5 ST

# D2 Edge Case
D2_MIN_SAETZE = 5            # Mindestens 5 Sätze nach 800ms-Filter
D2_INSUFFICIENT_SCORE = 70

# Scoring
GEWICHT_D1 = 0.40
GEWICHT_D2 = 0.30
GEWICHT_D3 = 0.30


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class SatzPitch:
    """Ein Satz mit zugeordneten Pitch-Daten."""
    index: int
    text: str
    start_s: float
    end_s: float
    ist_kernbotschaft: bool = False
    ist_frage: bool = False
    ist_aussage: bool = False
    dauer_ms: float = 0.0

    # Pitch-Daten (nur voiced Frames)
    f0_hz: np.ndarray = field(default_factory=lambda: np.array([]))
    f0_semitones: np.ndarray = field(default_factory=lambda: np.array([]))
    times_s: np.ndarray = field(default_factory=lambda: np.array([]))

    # Endkontur
    endkontur_st: float = 0.0
    endkontur_korrekt: bool = False

    # Statistik
    mean_st: float = 0.0
    std_st: float = 0.0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text[:60] + "..." if len(self.text) > 60 else self.text,
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "dauer_ms": round(self.dauer_ms, 1),
            "ist_kernbotschaft": self.ist_kernbotschaft,
            "ist_frage": self.ist_frage,
            "ist_aussage": self.ist_aussage,
            "endkontur_st": round(self.endkontur_st, 2),
            "endkontur_korrekt": self.endkontur_korrekt,
            "mean_st": round(self.mean_st, 2),
            "std_st": round(self.std_st, 2),
            "voiced_frames": len(self.f0_hz),
        }


@dataclass
class MonotonPassage:
    """Eine zusammenhängende monotone Passage > 8s."""
    start_s: float
    end_s: float
    dauer_s: float
    std_st: float

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "dauer_s": round(self.dauer_s, 2),
            "std_st": round(self.std_st, 2),
        }


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def zeitstr_to_s(zeit_str: str) -> float:
    zeit_str = zeit_str.strip()
    if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}$", zeit_str):
        h, m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", zeit_str):
        m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(m) * 60 + int(s) + int(ms) / 1000.0
    try:
        val = float(zeit_str)
        return val / 1000.0 if val > 10000 else val
    except ValueError:
        raise ValueError(f"Unbekanntes Zeitformat: {zeit_str}")


def s_to_zeitstr(sekunden: float) -> str:
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
# AUDIO & PITCH
# =============================================================================

def lade_audio(audio_pfad: Path) -> Tuple[np.ndarray, int]:
    if not HAS_LIBROSA:
        raise ImportError("librosa nicht installiert. pip install librosa soundfile")
    print(f"[pitch] Lade Audio: {audio_pfad.name}")
    y, sr = librosa.load(str(audio_pfad), sr=SAMPLE_RATE, mono=True)
    print(f"[pitch] Audio: {len(y)/SAMPLE_RATE:.2f}s @ {SAMPLE_RATE}Hz")
    return y, sr


def berechne_f0(y: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    F0-Extraktion mit librosa.pyin.

    Returns:
        f0: F0-Werte in Hz (0.0 für unvoiced)
        voiced_flag: Boolean-Array
        times_s: Zeitstempel pro Frame
    """
    hop_length = int(sr * HOP_LENGTH_MS / 1000)

    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=librosa.note_to_hz('C2'),   # ~65 Hz
        fmax=librosa.note_to_hz('C7'),   # ~2093 Hz
        sr=sr,
        hop_length=hop_length,
        frame_length=hop_length * 2      # Standard: 2× Hop
    )

    times_s = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop_length)

    return f0, voiced_flag, times_s


def hz_to_semitones(f0_hz: np.ndarray, baseline_hz: float) -> np.ndarray:
    """Hz → Semitones relativ zur Baseline."""
    # Nur voiced Frames (f0 > 0)
    semitones = np.zeros_like(f0_hz)
    mask = f0_hz > 0
    semitones[mask] = 12.0 * np.log2(f0_hz[mask] / baseline_hz)
    return semitones


# =============================================================================
# SATZ-EXTRAKTION & ZUORDNUNG
# =============================================================================

def extrahiere_saetze(inhalt_data: Optional[Dict]) -> List[SatzPitch]:
    """Extrahiert Sätze aus Inhaltsanalyse mit Satzzeichen-Erkennung."""
    saetze = []
    if not inhalt_data or "satzgrenzen" not in inhalt_data:
        return saetze

    for i, raw in enumerate(inhalt_data["satzgrenzen"]):
        start = raw.get("start_ms", raw.get("start"))
        end = raw.get("end_ms", raw.get("end"))
        text = raw.get("text", "")

        if isinstance(start, str):
            start = zeitstr_to_s(start)
        if isinstance(end, str):
            end = zeitstr_to_s(end)
        if start is None or end is None:
            continue

        start_s = float(start) / 1000.0 if float(start) > 1000 else float(start)
        end_s = float(end) / 1000.0 if float(end) > 1000 else float(end)

        # Satzzeichen erkennen
        text_stripped = text.strip()
        ist_frage = text_stripped.endswith("?")
        ist_aussage = text_stripped.endswith((".", "!"))

        saetze.append(SatzPitch(
            index=i,
            text=text,
            start_s=start_s,
            end_s=end_s,
            ist_frage=ist_frage,
            ist_aussage=ist_aussage,
            dauer_ms=(end_s - start_s) * 1000.0
        ))

    return saetze


def markiere_kernbotschaften(saetze: List[SatzPitch], inhalt_data: Optional[Dict]) -> None:
    if not inhalt_data or "kernbotschaften" not in inhalt_data:
        return

    for kb in inhalt_data["kernbotschaften"]:
        kb_start = kb.get("start_ms", kb.get("start"))
        kb_end = kb.get("end_ms", kb.get("end"))
        if isinstance(kb_start, str):
            kb_start = zeitstr_to_s(kb_start)
        if isinstance(kb_end, str):
            kb_end = zeitstr_to_s(kb_end)
        if kb_start is None or kb_end is None:
            continue

        kb_start_s = float(kb_start) / 1000.0 if float(kb_start) > 1000 else float(kb_start)
        kb_end_s = float(kb_end) / 1000.0 if float(kb_end) > 1000 else float(kb_end)

        for s in saetze:
            if s.start_s <= kb_end_s and s.end_s >= kb_start_s:
                s.ist_kernbotschaft = True


def ordne_pitch_zu_saetzen(
    saetze: List[SatzPitch],
    f0_hz: np.ndarray,
    f0_st: np.ndarray,
    times_s: np.ndarray
) -> None:
    """Ordnet jedem Satz die Pitch-Frames zu, die in seinen Zeitbereich fallen."""
    for s in saetze:
        maske = (times_s >= s.start_s) & (times_s <= s.end_s) & (f0_hz > 0)
        s.f0_hz = f0_hz[maske].copy()
        s.f0_semitones = f0_st[maske].copy()
        s.times_s = times_s[maske].copy()

        if len(s.f0_semitones) > 0:
            s.mean_st = float(np.mean(s.f0_semitones))
            s.std_st = float(np.std(s.f0_semitones, ddof=1)) if len(s.f0_semitones) > 1 else 0.0


def berechne_endkontur(s: SatzPitch, f0_st: np.ndarray, times_s: np.ndarray, f0_hz: np.ndarray) -> None:
    """
    Berechnet die Endkontur eines Satzes (letzte 300 ms).
    Fix v2: Nur für Sätze ≥ 800 ms.
    """
    if s.dauer_ms < 800:
        s.endkontur_st = 0.0
        s.endkontur_korrekt = False
        return

    # Letzte 300 ms
    end_start = s.end_s - (ENDKONTUR_DAUER_MS / 1000.0)
    maske = (times_s >= end_start) & (times_s <= s.end_s) & (f0_hz > 0)

    end_st = f0_st[maske]
    if len(end_st) < 2:
        s.endkontur_st = 0.0
        s.endkontur_korrekt = False
        return

    # Lineare Regression über die letzten Frames
    x = np.arange(len(end_st))
    if len(x) < 2:
        s.endkontur_st = 0.0
        s.endkontur_korrekt = False
        return

    # Steigung berechnen (einfache Differenz erster/letzter Wert)
    # Alternative: lineare Regression
    slope = (end_st[-1] - end_st[0])  # Delta in ST über 300ms
    s.endkontur_st = float(slope)

    # Prüfe Korrektheit
    if s.ist_frage and slope >= ENDKONTUR_STEIGEND:
        s.endkontur_korrekt = True
    elif s.ist_aussage and slope <= ENDKONTUR_FALLEND:
        s.endkontur_korrekt = True
    else:
        s.endkontur_korrekt = False


# =============================================================================
# MONOTON-PASSAGEN WARNUNG
# =============================================================================

def finde_monoton_passagen(
    f0_st: np.ndarray,
    times_s: np.ndarray,
    f0_hz: np.ndarray
) -> List[MonotonPassage]:
    """
    Findet zusammenhängende Passagen > 8 Sekunden mit Variation < 1.5 ST.
    Gleitendes Fenster über die voiced Frames.
    """
    passagen = []

    # Nur voiced Frames
    voiced_mask = f0_hz > 0
    voiced_times = times_s[voiced_mask]
    voiced_st = f0_st[voiced_mask]

    if len(voiced_st) < 2:
        return passagen

    # Gleitendes Fenster: 8 Sekunden
    # Wir verwenden einen Index-basierten Ansatz
    i = 0
    while i < len(voiced_times):
        # Finde alle Frames innerhalb von 8s ab diesem Startpunkt
        start_t = voiced_times[i]
        end_t = start_t + MONOTON_PASSAGE_S

        j = i
        while j < len(voiced_times) and voiced_times[j] <= end_t:
            j += 1

        window_st = voiced_st[i:j]
        if len(window_st) > 1:
            std = float(np.std(window_st, ddof=1))
            if std < MONOTON_PASSAGE_ST:
                # Prüfe ob wir diese Passage verlängern können
                actual_end = voiced_times[j - 1] if j > 0 else start_t
                dauer = actual_end - start_t
                if dauer >= MONOTON_PASSAGE_S:
                    passagen.append(MonotonPassage(
                        start_s=start_t,
                        end_s=actual_end,
                        dauer_s=dauer,
                        std_st=std
                    ))

        i += 1

    # Überlappende Passagen zusammenfassen (nur die längste pro Region behalten)
    if not passagen:
        return passagen

    # Sortieren und deduplizieren
    passagen.sort(key=lambda p: p.start_s)
    bereinigt = [passagen[0]]
    for p in passagen[1:]:
        last = bereinigt[-1]
        if p.start_s <= last.end_s:
            # Überlappend: verlängere falls nötig
            if p.end_s > last.end_s:
                last.end_s = p.end_s
                last.dauer_s = last.end_s - last.start_s
        else:
            bereinigt.append(p)

    return bereinigt


# =============================================================================
# SCORING
# =============================================================================

def score_d1_gesamtvariation(f0_st: np.ndarray, f0_hz: np.ndarray) -> Tuple[int, float, str]:
    """
    D1: Gesamtvariation in Semitones (40%)
    Standardabweichung aller voiced Frames.
    """
    voiced_st = f0_st[f0_hz > 0]
    if len(voiced_st) < 2:
        return 20, 0.0, "Nicht genug voiced Frames"

    std = float(np.std(voiced_st, ddof=1))

    if ST_OPTIMAL_MIN <= std <= ST_OPTIMAL_MAX:
        punkte = 100
        bewertung = "Optimal (Vortrag)"
    elif (ST_AUFFAELLIG_MIN <= std < ST_OPTIMAL_MIN) or (ST_OPTIMAL_MAX < std <= ST_AUFFAELLIG_MAX):
        punkte = 75
        bewertung = "Akzeptabel"
    elif (ST_MONOTON <= std < ST_AUFFAELLIG_MIN) or (ST_AUFFAELLIG_MAX < std <= ST_CHAOTISCH_MIN):
        punkte = 40
        bewertung = "Auffällig"
    elif std < ST_MONOTON:
        punkte = 20
        bewertung = "Monoton"
    else:  # > 10
        punkte = 20
        bewertung = "Chaotisch"

    return punkte, std, bewertung


def score_d2_endkontur(saetze: List[SatzPitch]) -> Tuple[int, float, str, int, int]:
    """
    D2: End-Kontur-Korrektheit (30%)
    Fix v2: Nur Sätze ≥ 800 ms. Edge Case: < 5 Sätze → 70 Punkte (neutral).
    """
    # Filter: nur Sätze ≥ 800 ms
    gueltige = [s for s in saetze if s.dauer_ms >= 800 and (s.ist_frage or s.ist_aussage)]

    if len(gueltige) < D2_MIN_SAETZE:
        return D2_INSUFFICIENT_SCORE, 0.0, "Insufficient data (weniger als 5 Sätze >= 800ms)", len(gueltige), 0

    korrekt = sum(1 for s in gueltige if s.endkontur_korrekt)
    anteil = korrekt / len(gueltige) if gueltige else 0.0

    if anteil >= 0.80:
        punkte = 100
        bewertung = "Sehr gut"
    elif anteil >= 0.60:
        punkte = 75
        bewertung = "Gut"
    elif anteil >= 0.40:
        punkte = 50
        bewertung = "Verbesserbar"
    else:
        punkte = 25
        bewertung = "Problematisch"

    return punkte, anteil, bewertung, len(gueltige), korrekt


def score_d3_kernbotschafts_variation(saetze: List[SatzPitch], gesamt_std: float) -> Tuple[int, float, str]:
    """
    D3: Kernbotschafts-Variation (30%)
    Verhältnis: Kern-SD / Gesamt-SD
    """
    kern_saetze = [s for s in saetze if s.ist_kernbotschaft and len(s.f0_semitones) > 1]

    if not kern_saetze:
        return 75, 1.0, "Keine Kernbotschaften gefunden"

    # Gesamt-SD der Kernbotschaften (gewichtet nach Frame-Anzahl)
    kern_frames = np.concatenate([s.f0_semitones for s in kern_saetze])
    if len(kern_frames) < 2:
        return 75, 1.0, "Nicht genug Frames in Kernbotschaften"

    kern_std = float(np.std(kern_frames, ddof=1))

    if gesamt_std <= 0:
        return 75, 1.0, "Gesamt-Std = 0"

    verhaeltnis = kern_std / gesamt_std

    if verhaeltnis >= 1.2:
        punkte = 100
        bewertung = "Kernbotschaften stärker moduliert"
    elif verhaeltnis >= 0.9:
        punkte = 75
        bewertung = "Ähnlich wie Rest"
    else:
        punkte = 40
        bewertung = "Kernbotschaften unter-moduliert"

    return punkte, verhaeltnis, bewertung


def berechne_gesamtscore(d1: int, d2: int, d3: int) -> int:
    score = d1 * GEWICHT_D1 + d2 * GEWICHT_D2 + d3 * GEWICHT_D3
    return int(round(score))


# =============================================================================
# REPORT
# =============================================================================


def _pitch_fallback_tipps(score: int) -> List[str]:
    if score >= 75:
        return ["Deine Stimmmelodie ist bereits überzeugend. Lies Texte laut vor "
                "und übertreibe die Melodie bewusst, um deinen Ausdrucksbereich "
                "weiter zu dehnen."]
    if score >= 50:
        return ["Lies denselben Satz mehrmals — mal als Frage, mal als Aussage, "
                "mal enthusiastisch. Das trainiert deine stimmliche Flexibilität."]
    return ["Lies täglich 5 Minuten einen Text laut und übertreibe die Melodie "
            "absichtlich — das ist die wirksamste Methode gegen Monotonie."]


# =============================================================================
# REPORT — KURZFASSUNG
# =============================================================================

def generiere_kurz_report(
    d1_score: int, d1_std: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d2_gesamt: int, d2_korrekt: int,
    d3_score: int, d3_ratio: float, d3_text: str,
    gesamt_score: int,
    saetze: List[SatzPitch],
    monoton_passagen: List[MonotonPassage],
    audio_name: str,
    audio_dauer_s: float,
    insufficient_data: bool,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"
    z = ru.kurz_header("TONHÖHEN-VARIATION", audio_name, dauer_str)

    if audio_dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({audio_dauer_s:.0f} Sek.) — Details dazu in der")
        z.append("    ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine Tonhöhenvariation ist überzeugend — du klingst lebendig und abwechslungsreich.",
        "Deine Tonhöhenvariation ist ausbaufähig — stellenweise klingt die Stimme zu gleichförmig.",
        "Deine Stimme klingt deutlich monoton. Das kann Zuhörer ermüden.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Gesamtvariation", 40, d1_score, d1_text)
    z += ru.dimension_zeile_kurz("Satzmelodie", 30, d2_score, d2_text)
    z += ru.dimension_zeile_kurz("Stimmbetonung bei Kernaussagen", 30, d3_score, d3_text)
    z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    for i, zeile in enumerate(_pitch_fallback_tipps(gesamt_score), 1):
        umbrochen = ru.wrap_text(zeile) if len(zeile) > 64 else [zeile]
        z.append(f"  {i}. {umbrochen[0]}")
        z += [f"     {folgezeile}" for folgezeile in umbrochen[1:]]
    z.append("")
    z.append("  Wo genau im Video deine Stimme monoton oder auffällig klingt, und")
    z.append("  warum diese Punktzahl herauskommt, steht im ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)
    return "\n".join(z)


# =============================================================================
# REPORT — DETAILANSICHT
# =============================================================================

def generiere_detail_report(
    d1_score: int, d1_std: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d2_gesamt: int, d2_korrekt: int,
    d3_score: int, d3_ratio: float, d3_text: str,
    gesamt_score: int,
    saetze: List[SatzPitch],
    monoton_passagen: List[MonotonPassage],
    audio_name: str,
    audio_dauer_s: float,
    insufficient_data: bool,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"

    z = ru.detail_header("TONHÖHEN-VARIATION", audio_name, dauer_str)
    z += ru.build_toc([
        "Gesamtergebnis",
        "Gesamtvariation — Begründung & Fundstellen",
        "Satzmelodie — Begründung & Fundstellen",
        "Stimmbetonung bei Kernaussagen — Begründung & Fundstellen",
        "Alle Sätze im Überblick",
        "Hintergrund & Referenzwerte",
    ])
    z.append("  Halbtöne sind die Maßeinheit für Stimmhöhen-Unterschiede — 12")
    z.append("  Halbtöne ergeben eine Oktave. Eine normale Unterhaltung variiert")
    z.append("  3–6 Halbtöne.")
    z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine Tonhöhenvariation ist überzeugend — du klingst lebendig und abwechslungsreich.",
        "Deine Tonhöhenvariation ist ausbaufähig — stellenweise klingt die Stimme zu gleichförmig.",
        "Deine Stimme klingt deutlich monoton. Das kann Zuhörer ermüden.",
    )
    z += ru.kleine_stichprobe_warnung(audio_dauer_s)

    # ── D1 — Gesamtvariation ──────────────────────────────────────────────────
    d1_befunde = [{"dauer_s": p.dauer_s, "std_st": p.std_st, "start_ms": p.start_s * 1000,
                   "zeit_von": s_to_zeitstr(p.start_s), "zeit_bis": s_to_zeitstr(p.end_s)}
                  for p in monoton_passagen]

    fundstellen_d1 = []
    for p in sorted(monoton_passagen, key=lambda p: -p.dauer_s)[:5]:
        fundstellen_d1.append(ru.fundstelle_zeile(
            p.start_s * 1000, f"Monotone Passage bis {s_to_zeitstr(p.end_s)}",
            f"{p.dauer_s:.1f}s, Streuung nur {p.std_st:.2f} Halbtöne"))
    if not fundstellen_d1:
        fundstellen_d1 = ["  Keine monotone Passage über 8 Sekunden gefunden."]

    D1_ACHSEN = [
        ru.Achse(
            "monotone_passage_lang", "praesenz", prioritaet=1,
            merkmal_key="ist_lang", merkmal_wert=True, min_evidenz=1,
            befund_template="Von {zeit_von} bis {zeit_bis} bleibt deine Stimme {dauer_s:.0f} Sekunden lang fast auf derselben Tonhöhe.",
            ursache_template="Das ist die längste eintönige Stelle in deiner gesamten Aufnahme.",
            uebung_template=(
                "\nSchritt 1: Lies genau diesen Abschnitt einmal übertrieben "
                "sing-sang-artig vor, mit stark schwankender Tonhöhe.\n"
                "Schritt 2: Lies ihn nochmal etwas natürlicher, aber "
                "bewusst mit mehr Auf und Ab als beim ersten Versuch.\n"
                "Schritt 3: Baue den Abschnitt zurück in den Kontext ein "
                "und höre den Unterschied zum Original."
            ),
        ),
    ]
    for b in d1_befunde:
        b["ist_lang"] = True  # alle in monoton_passagen sind per Definition schon >8s

    tipp_d1 = ru.erkenne_muster_v2(
        d1_befunde, D1_ACHSEN, max_tipps=1,
        fall_a_text=["Keine besonders lange eintönige Stelle gefunden — deine "
                     "Tonhöhe variiert insgesamt ausreichend."],
        einzelfund_template=(
            "Es gab eine kurze eintönige Stelle bei {zeit_von}. Das ist "
            "unauffällig, kein Grund zur Sorge."
        ),
        fall_c_einleitung="Es gibt mehrere kürzere eintönige Stellen über die Aufnahme verteilt, ohne dass eine besonders heraussticht:",
    )

    z += ru.dimension_block_detail(
        "Gesamtvariation", 40, d1_score,
        was_gemessen=["Wie stark deine Stimme insgesamt in der Tonhöhe schwankt",
                      "(Standardabweichung in Halbtönen)."],
        warum=[f"Gemessen: {d1_std:.2f} Halbtöne Streuung.",
               "Faustregel: 3-6 Halbtöne = lebendig, unter 1,5 = monoton,",
               "über 8 = übertrieben.",
               f"Bei dir: {d1_text}"],
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 — Satzmelodie ──────────────────────────────────────────────────────
    relevante_saetze = [s for s in saetze if s.dauer_ms >= 800 and (s.ist_frage or s.ist_aussage)]
    fundstellen_d2 = []
    for s in relevante_saetze[:8]:
        status = "korrekt" if s.endkontur_korrekt else "auffällig"
        fundstellen_d2.append(ru.fundstelle_zeile(
            s.start_s * 1000, s.text, f"Endkontur {s.endkontur_st:+.2f} Halbtöne — {status}"))
    if not fundstellen_d2:
        fundstellen_d2 = ["  Zu wenige ausreichend lange Sätze für eine Einzelauswertung."]

    d2_befunde = []
    for s in relevante_saetze:
        if s.endkontur_korrekt:
            continue  # nur tatsächliche Auffälligkeiten sind "Befunde" —
            # korrekte Sätze gehören nicht in die Muster-Suche (sonst würden
            # sie fälschlich als Problem in Fall C/D auftauchen)
        richtung_fehler = None
        if s.ist_aussage and s.endkontur_st > 0:
            richtung_fehler = "steigt_statt_faellt"
        elif s.ist_frage and s.endkontur_st < 0:
            richtung_fehler = "faellt_statt_steigt"
        d2_befunde.append({
            "satz": s.text,
            "korrekt": s.endkontur_korrekt,
            "richtung_fehler": richtung_fehler,
            "start_ms": s.start_s * 1000,
        })

    D2_ACHSEN = [
        ru.Achse(
            "satzmelodie_steigt_statt_faellt", "anteil", prioritaet=1,
            merkmal_key="richtung_fehler", merkmal_wert="steigt_statt_faellt",
            min_evidenz=3, schwelle=0.5,
            befund_template="Bei {anteil:.0%} deiner Aussagesätze steigt deine Stimme am Ende, statt abzusinken.",
            ursache_template="Das klingt für Zuhörer wie eine unfertige oder unsichere Aussage, auch wenn der Inhalt vollständig ist.",
            uebung_template=(
                "\nSchritt 1: Sprich 3 kurze Aussagesätze, bei jedem die "
                "Stimme am Ende bewusst übertrieben tief absenken.\n"
                "Schritt 2: Sprich dieselben Sätze nochmal, etwas "
                "natürlicher, aber weiterhin klar fallend.\n"
                "Schritt 3: Sprich deine echten Sätze aus dem Text mit "
                "dieser Endkontur, je 3x."
            ),
        ),
        ru.Achse(
            "satzmelodie_faellt_statt_steigt", "anteil", prioritaet=1,
            merkmal_key="richtung_fehler", merkmal_wert="faellt_statt_steigt",
            min_evidenz=2, schwelle=0.5,
            befund_template="Bei deinen Fragen sinkt die Stimme am Ende, statt zu steigen.",
            ursache_template="Das kann eine Frage für Zuhörer weniger eindeutig als Frage erkennbar machen.",
            uebung_template=(
                "\nSchritt 1: Sprich 3 kurze Fragen, bei jeder die Stimme "
                "am Ende bewusst übertrieben anheben.\n"
                "Schritt 2: Sprich dieselben Fragen nochmal, etwas "
                "natürlicher, aber weiterhin klar steigend.\n"
                "Schritt 3: Sprich deine echten Fragen aus dem Text mit "
                "dieser Endkontur, je 3x."
            ),
        ),
    ]

    # richtung_beschreibung für ALLE Befunde berechnen — wird bei Fall B
    # (1 Fund) UND Fall C (mehrere, kein Muster) über das Einzelfund-Template
    # gebraucht. Vorher wurde das nur für den n==1-Fall gesetzt, wodurch Fall C
    # den Platzhalter nie gefüllt bekommen hätte.
    for b in d2_befunde:
        rf = b.get("richtung_fehler")
        if rf == "steigt_statt_faellt":
            b["richtung_beschreibung"] = (
                "steigt deine Stimme am Ende, obwohl es sich um eine Aussage handelt"
            )
        elif rf == "faellt_statt_steigt":
            b["richtung_beschreibung"] = (
                "sinkt deine Stimme am Ende, obwohl es sich um eine Frage handelt"
            )
        else:
            b["richtung_beschreibung"] = "ist die Satzmelodie an dieser Stelle auffällig"

    tipp_d2 = ru.erkenne_muster_v2(
        d2_befunde, D2_ACHSEN, max_tipps=1,
        fall_a_text=[f"Zu wenige Sätze (mindestens {D2_MIN_SAETZE} nötig) für eine "
                     "verlässliche Einschätzung der Satzmelodie."],
        einzelfund_template=(
            "Bei '{satz}' {richtung_beschreibung}.\n"
            "Schritt 1: Sprich genau diesen Satz 5x, mit übertrieben "
            "korrekter Endkontur.\n"
            "Schritt 2: Sprich den Satz davor und diesen Satz zusammen, "
            "3x hintereinander.\n"
            "Schritt 3: Nimm dich auf und höre, ob die Endkontur jetzt sitzt."
        ),
        fall_c_einleitung="Mehrere Sätze mit auffälliger Satzmelodie, aber ohne durchgehendes Muster — mal richtig, mal nicht:",
    )

    z += ru.dimension_block_detail(
        "Satzmelodie", 30, d2_score,
        was_gemessen=["Ob deine Stimme am Satzende in die richtige Richtung geht —",
                      "bei Aussagen sollte sie fallen, bei Fragen steigen."],
        warum=([f"Zu wenige Sätze (>= {D2_MIN_SAETZE} nötig) für eine Bewertung."]
               if insufficient_data else
               [f"{d2_korrekt} von {d2_gesamt} Sätzen hatten eine korrekte Satzmelodie",
                f"({d2_anteil:.0%}).",
                f"Bei dir: {d2_text}"]),
        fundstellen=fundstellen_d2,
        tipp=tipp_d2,
    )

    # ── D3 — Stimmbetonung bei Kernaussagen ───────────────────────────────────
    kern_saetze = [s for s in saetze if s.ist_kernbotschaft and s.dauer_ms >= 800]
    fundstellen_d3 = []
    for s in kern_saetze[:5]:
        fundstellen_d3.append(ru.fundstelle_zeile(
            s.start_s * 1000, s.text, f"Streuung {s.std_st:.2f} Halbtöne"))
    if not fundstellen_d3:
        fundstellen_d3 = ["  Keine ausreichend lange Kernaussage gefunden."]

    gesamt_std_ref = d1_std if d1_std else 1.0
    d3_befunde = [{"satz": s.text, "std_st": s.std_st,
                   "ohne_betonung": (s.std_st / gesamt_std_ref) < 1.0 if gesamt_std_ref else True,
                   "start_ms": s.start_s * 1000} for s in kern_saetze]

    D3_ACHSEN = [
        ru.Achse(
            "kernaussage_ohne_betonung", "anteil", prioritaet=1,
            merkmal_key="ohne_betonung", merkmal_wert=True, min_evidenz=2, schwelle=0.5,
            befund_template="Deine Kernaussagen klingen in der Tonhöhe genauso gleichmäßig wie der Rest deiner Präsentation.",
            ursache_template="Keine zusätzliche Betonung durch die Stimme bedeutet: die wichtigste Stelle sticht nicht hörbar heraus.",
            uebung_template=(
                "\nSchritt 1: Sprich nur diese Kernaussagen, mit übertrieben "
                "starker Melodie (viel Auf und Ab), 5x pro Satz.\n"
                "Schritt 2: Sprich den Satz davor normal, dann die "
                "Kernaussage mit mehr Melodie — übe den Übergang 3x.\n"
                "Schritt 3: Nimm dich auf und vergleiche: sticht die "
                "Kernaussage jetzt hörbar heraus?"
            ),
        ),
        ru.Achse(
            "kernaussage_durchgehend_betont", "anteil", prioritaet=2,
            merkmal_key="ohne_betonung", merkmal_wert=False, min_evidenz=2, schwelle=0.8,
            befund_template="Alle {n} deiner Kernaussagen klingen in der Tonhöhe deutlich lebendiger als der Rest.",
            ursache_template="Das ist genau das Muster, das wichtige Aussagen hörbar hervorhebt.",
            uebung_template="Behalte diese Betonung bei — hier gibt es nichts zu verbessern.",
        ),
    ]

    # ratio/einordnung müssen VOR dem Engine-Aufruf gesetzt werden, damit sie
    # sowohl bei Fall B (1 Fund) als auch Fall C (mehrere, kein Muster) über
    # das Einzelfund-Template zur Verfügung stehen.
    for b in d3_befunde:
        b["ratio"] = b["std_st"] / gesamt_std_ref if gesamt_std_ref else 1.0
        if b["ratio"] >= 1.0:
            b["einordnung"] = (
                "deutlich lebendiger in der Tonhöhe als der Rest — sehr "
                "gut umgesetzt, hier gibt es nichts zu verbessern"
            )
        else:
            b["einordnung"] = (
                "genauso gleichmäßig wie der Rest.\n"
                "Schritt 1: Sprich genau diesen Satz 5x, mit übertrieben "
                "starker Melodie.\n"
                "Schritt 2: Sprich den Satz davor normal, dann diesen mit "
                "mehr Melodie — 3x den Übergang üben.\n"
                "Schritt 3: Nimm dich auf und vergleiche mit dem Original"
            )

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde, D3_ACHSEN, max_tipps=1,
        fall_a_text=["Keine ausreichend lange Kernaussage für eine verlässliche Messung gefunden."],
        einzelfund_template="Diese Kernaussage ist {einordnung} (Verhältnis {ratio:.2f}).",
        fall_c_einleitung="Deine Kernaussagen sind unterschiedlich stark betont — bei manchen mehr Melodie, bei anderen kaum:",
    )

    z += ru.dimension_block_detail(
        "Stimmbetonung bei Kernaussagen", 30, d3_score,
        was_gemessen=["Ob du bei deiner wichtigsten Aussage mehr Tonhöhenvariation",
                      "einsetzt als im Rest — das hebt sie hervor."],
        warum=[f"Verhältnis Kernaussage-Variation / Gesamt = {d3_ratio:.2f}.",
               "Ein Wert über 1.0 bedeutet: ausdrucksstärker als sonst.",
               f"Bei dir: {d3_text}"],
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Alle Sätze im Überblick ───────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. ALLE SÄTZE IM ÜBERBLICK")
    z.append(ru.SEP2)
    z.append(f"  {'Nr.':<5}{'Zeit im Video':<16}{'Satz':<40}{'Status'}")
    z.append("  " + "-" * 66)
    for s in saetze:
        if s.dauer_ms < 800:
            status = "(zu kurz)"
        elif s.ist_frage or s.ist_aussage:
            status = "✅ korrekt" if s.endkontur_korrekt else "❌ auffällig"
        else:
            status = "(n/a)"
        kurztext = s.text if len(s.text) <= 30 else s.text[:27] + "..."
        z.append(f"  {s.index:<5}{s_to_zeitstr(s.start_s) + '–' + s_to_zeitstr(s.end_s):<22} "
                  f"\"{kurztext}\"  {status}")
    z.append("")

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  6. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Tonhöhe (Grundfrequenz F0) wird mit dem PYIN-Algorithmus berechnet")
    z.append("  und in Halbtöne relativ zu deiner Median-Frequenz umgerechnet.")
    z.append("  Optimum Gesamtvariation: 3-6 Halbtöne.")
    z.append("  Quellen: Hincks & Edlund (2009) · Hahn (2004) · The Learning Hall (2025)")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    return "\n".join(z)

# HAUPTFUNKTION
# =============================================================================

def analyse_pitch_variation(
    audio_pfad: Path,
    inhalt_pfad: Optional[Path] = None,
    output_json_pfad: Optional[Path] = None,
    output_txt_kurz_pfad: Optional[Path] = None,
    output_txt_detail_pfad: Optional[Path] = None,
) -> Dict[str, Any]:

    if not HAS_LIBROSA:
        raise ImportError("librosa nicht installiert. pip install librosa soundfile")

    print(f"[pitch] Starte Analyse: {audio_pfad.name}")

    # 1. Audio laden
    y, sr = lade_audio(audio_pfad)
    audio_dauer_s = len(y) / sr

    # 2. F0 berechnen
    f0_hz, voiced_flag, times_s = berechne_f0(y, sr)
    print(f"[pitch] {len(f0_hz)} F0-Frames berechnet.")

    # 3. Baseline & Semitones
    voiced_f0 = f0_hz[f0_hz > 0]
    if len(voiced_f0) == 0:
        raise ValueError("Keine voiced Frames gefunden — Audio möglicherweise stumm.")

    baseline_hz = float(np.median(voiced_f0))
    f0_st = hz_to_semitones(f0_hz, baseline_hz)
    print(f"[pitch] Baseline F0 = {baseline_hz:.2f} Hz")

    # 4. Sätze laden
    inhalt_data = lade_json(inhalt_pfad) if inhalt_pfad else None
    saetze = extrahiere_saetze(inhalt_data)
    markiere_kernbotschaften(saetze, inhalt_data)
    print(f"[pitch] {len(saetze)} Sätze geladen.")

    # 5. Pitch zu Sätzen zuordnen
    ordne_pitch_zu_saetzen(saetze, f0_hz, f0_st, times_s)

    # 6. Endkonturen berechnen
    for s in saetze:
        berechne_endkontur(s, f0_st, times_s, f0_hz)

    # 7. Monoton-Passagen finden
    monoton_passagen = finde_monoton_passagen(f0_st, times_s, f0_hz)
    if monoton_passagen:
        print(f"[pitch] {len(monoton_passagen)} Monoton-Passage(n) gefunden.")

    # 8. Scoring
    d1_score, d1_std, d1_text = score_d1_gesamtvariation(f0_st, f0_hz)
    d2_score, d2_anteil, d2_text, d2_gesamt, d2_korrekt = score_d2_endkontur(saetze)
    d3_score, d3_ratio, d3_text = score_d3_kernbotschafts_variation(saetze, d1_std)
    gesamt_score = berechne_gesamtscore(d1_score, d2_score, d3_score)

    insufficient_data = (d2_gesamt < D2_MIN_SAETZE)

    print(f"[pitch] Scoring: D1={d1_score}, D2={d2_score}, D3={d3_score}, Gesamt={gesamt_score}")

    # 9. Output
    output_data = {
        "modul": "pitch_variation_analyse",
        "version": "2.0",
        "timestamp": datetime.now().isoformat(),
        "input": str(audio_pfad),
        "meta": {
            "audio_dauer_s": round(audio_dauer_s, 3),
            "sample_rate": SAMPLE_RATE,
            "hop_length_ms": HOP_LENGTH_MS,
            "baseline_hz": round(baseline_hz, 2),
            "voiced_frames": int(np.sum(f0_hz > 0)),
            "unvoiced_frames": int(np.sum(f0_hz <= 0)),
        },
        "saetze": [s.to_dict() for s in saetze],
        "monoton_passagen": [p.to_dict() for p in monoton_passagen],
        "scoring": {
            "d1_gesamtvariation": {
                "gewichtung": GEWICHT_D1,
                "punkte": d1_score,
                "bewertung": d1_text,
                "std_semitones": round(d1_std, 2)
            },
            "d2_endkontur": {
                "gewichtung": GEWICHT_D2,
                "punkte": d2_score,
                "bewertung": d2_text,
                "anteil_korrekt": round(d2_anteil, 4),
                "saetze_gesamt": d2_gesamt,
                "saetze_korrekt": d2_korrekt,
                "insufficient_data": insufficient_data
            },
            "d3_kernbotschafts_variation": {
                "gewichtung": GEWICHT_D3,
                "punkte": d3_score,
                "bewertung": d3_text,
                "verhaeltnis": round(d3_ratio, 4)
            },
            "gesamtscore": gesamt_score
        }
    }

    if output_json_pfad:
        output_json_pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_pfad, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2,
                      default=lambda x: float(x) if isinstance(x, np.floating) else x)
        print(f"[pitch] JSON gespeichert: {output_json_pfad}")

    if output_txt_kurz_pfad:
        output_txt_kurz_pfad.parent.mkdir(parents=True, exist_ok=True)
        kurz_report = generiere_kurz_report(
            d1_score, d1_std, d1_text,
            d2_score, d2_anteil, d2_text,
            d2_gesamt, d2_korrekt,
            d3_score, d3_ratio, d3_text,
            gesamt_score, saetze, monoton_passagen,
            audio_pfad.name, audio_dauer_s,
            insufficient_data
        )
        with open(output_txt_kurz_pfad, "w", encoding="utf-8") as f:
            f.write(kurz_report)
        print(f"[pitch] Kurz-Report gespeichert: {output_txt_kurz_pfad}")

    if output_txt_detail_pfad:
        output_txt_detail_pfad.parent.mkdir(parents=True, exist_ok=True)
        detail_report = generiere_detail_report(
            d1_score, d1_std, d1_text,
            d2_score, d2_anteil, d2_text,
            d2_gesamt, d2_korrekt,
            d3_score, d3_ratio, d3_text,
            gesamt_score, saetze, monoton_passagen,
            audio_pfad.name, audio_dauer_s,
            insufficient_data
        )
        with open(output_txt_detail_pfad, "w", encoding="utf-8") as f:
            f.write(detail_report)
        print(f"[pitch] Detail-Report gespeichert: {output_txt_detail_pfad}")

    print(f"[pitch] Fertig. Gesamt-Score: {gesamt_score}/100")
    return output_data


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pitch-Variation Analyse für Präsentationsbewertungs-AI")
    parser.add_argument("audio", type=str, help="Pfad zur Audio-Datei")
    parser.add_argument("--inhalt", type=str, default=None, help="Pfad zu inhalt_analyse_output.json")
    parser.add_argument("--output-json", type=str, default="zwischen_output/pitch_variation_analyse_output.json")
    parser.add_argument("--output-txt", type=str, default=None)

    args = parser.parse_args()

    audio = Path(args.audio)
    inhalt = Path(args.inhalt) if args.inhalt else None
    out_json = Path(args.output_json)

    if args.output_txt:
        out_txt = Path(args.output_txt)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_txt = Path("reports/pitch") / f"pitch_report_{ts}.txt"

    if not audio.exists():
        print(f"[FEHLER] Audio nicht gefunden: {audio}")
        exit(1)

    try:
        analyse_pitch_variation(audio, inhalt, out_json, out_txt)
    except Exception as e:
        print(f"[FEHLER] {e}")
        raise
