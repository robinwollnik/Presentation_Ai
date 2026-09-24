#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotionale_variation_analyse.py
===============================
Erkennt Emotionen in der Stimme mittels wav2vec2 und bewertet ob die
emotionale Variation zum Inhalt und zur Struktur passt.

Input:
  - Audio-Datei
  - inhalt_analyse_output.json (für Kernbotschaften + emotionaler_ton)

Output:
  - zwischen_output/emotionale_variation_analyse_output.json
  - reports/emotion/emotion_report_[TIMESTAMP].txt"""

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
    import torch
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    warnings.warn("torch nicht installiert. pip install torch")

try:
    from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False
    warnings.warn("transformers nicht installiert. pip install transformers")

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
FENSTER_S = 3.0           # 3 Sekunden
HOP_S = 2.0               # 1 Sekunde Overlap → 2s Hop

# Arousal-Wechsel
AROUSAL_WECHSEL_SCHWELLE = 0.15   # Delta > 0.15 = spürbarer Wechsel

# D1 Skala
D1_OPTIMAL_MIN = 3.0
D1_OPTIMAL_MAX = 5.0
D1_AKZEPTABEL_MIN = 1.5
D1_AKZEPTABEL_MAX = 8.0

# D2 Valence-Bereiche je Ton-Label
VALENCE_INSPIRIEREND_MIN = 0.55
VALENCE_SACHLICH_MIN = 0.40
VALENCE_SACHLICH_MAX = 0.60
VALENCE_ERNST_MAX = 0.45

# D3 Dominance
DOMINANCE_ANHEBUNG_GUT = 0.15
DOMINANCE_ANHEBUNG_LEICHT = 0.05

# Scoring
GEWICHT_D1 = 0.40
GEWICHT_D2 = 0.30
GEWICHT_D3 = 0.30

MODEL_NAME = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class EmotionsSegment:
    """Ein 3s-Segment mit Emotionswerten."""
    start_s: float
    end_s: float
    arousal: float
    valence: float
    dominance: float

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "arousal": round(self.arousal, 4),
            "valence": round(self.valence, 4),
            "dominance": round(self.dominance, 4),
        }


@dataclass
class KernbotschaftEmotion:
    """Emotionsdaten für eine Kernbotschaft."""
    text: str
    start_s: float
    end_s: float
    mean_arousal: float
    mean_valence: float
    mean_dominance: float
    valence_passend: bool
    valence_erwartet_min: float
    valence_erwartet_max: float

    def to_dict(self) -> dict:
        return {
            "text": self.text[:80] + "..." if len(self.text) > 80 else self.text,
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "mean_arousal": round(self.mean_arousal, 4),
            "mean_valence": round(self.mean_valence, 4),
            "mean_dominance": round(self.mean_dominance, 4),
            "valence_passend": self.valence_passend,
            "valence_erwartet": f"[{self.valence_erwartet_min:.2f}, {self.valence_erwartet_max:.2f}]",
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
# MODELL-LADEN
# =============================================================================

class EmotionModel:
    """Wrapper für das audEERING wav2vec2 Emotionsmodell."""

    def __init__(self):
        if not HAS_TORCH or not HAS_TRANSFORMERS or not HAS_LIBROSA:
            raise ImportError(
                "Benötigte Pakete fehlen. Installieren:\n"
                "  pip install torch transformers librosa soundfile"
            )

        print(f"[emotion] Lade Modell: {MODEL_NAME}")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[emotion] Device: {self.device}")

        self.feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(MODEL_NAME)
        self.model = Wav2Vec2ForSequenceClassification.from_pretrained(MODEL_NAME)
        self.model.to(self.device)
        self.model.eval()

        # Labels: Das Modell gibt Arousal, Valence, Dominance zurück
        # Die ID-Zuordnung ist im Modell-Config gespeichert
        self.id2label = self.model.config.id2label
        print(f"[emotion] Modell geladen. Labels: {list(self.id2label.values())}")

    def predict(self, audio: np.ndarray, sr: int) -> Tuple[float, float, float]:
        """
        Predict Arousal, Valence, Dominance für ein Audio-Segment.

        Returns:
            (arousal, valence, dominance) — jeweils 0..1
        """
        # Resample falls nötig
        if sr != SAMPLE_RATE:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)

        # Feature Extraction
        inputs = self.feature_extractor(
            audio,
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt",
            padding=True
        )

        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model(**inputs)

        # Logits zu Wahrscheinlichkeiten
        logits = outputs.logits.cpu().numpy()[0]

        # Das Modell gibt direkt Arousal/Valence/Dominance als Regression aus
        # Normalerweise sind die Outputs bereits skaliert (0-1 oder -1 bis 1)
        # Wir müssen die genaue Skalierung prüfen

        # Für das audEERING Modell: 3 Outputs = [arousal, valence, dominance]
        # Typischerweise im Bereich [0, 1] oder [-1, 1]
        # Wir normalisieren auf [0, 1]

        arousal = float(self._normalize(logits[0]))
        valence = float(self._normalize(logits[1]))
        dominance = float(self._normalize(logits[2]))

        return arousal, valence, dominance

    def _normalize(self, value: float) -> float:
        """Normalisiert Modell-Output auf [0, 1]."""
        # Das audEERING Modell gibt typischerweise Werte im Bereich [-1, 1] aus
        # Wir mappen auf [0, 1]
        if value < -1.0:
            value = -1.0
        if value > 1.0:
            value = 1.0
        return (value + 1.0) / 2.0


# =============================================================================
# AUDIO-SEGMENTIERUNG
# =============================================================================

def segmentiere_audio(y: np.ndarray, sr: int) -> List[Tuple[np.ndarray, float, float]]:
    """
    Segmentiert Audio in 3s-Fenster mit 1s Overlap.

    Returns:
        Liste von (segment_audio, start_s, end_s)
    """
    segments = []
    fenster_samples = int(FENSTER_S * sr)
    hop_samples = int(HOP_S * sr)

    start = 0
    while start + fenster_samples <= len(y):
        segment = y[start:start + fenster_samples]
        start_s = start / sr
        end_s = (start + fenster_samples) / sr
        segments.append((segment, start_s, end_s))
        start += hop_samples

    # Letztes Segment (kürzer, aber mindestens 1s)
    remaining = len(y) - start
    if remaining >= sr:  # Mindestens 1 Sekunde
        segment = y[start:]
        # Padding auf 3s mit Nullen
        if len(segment) < fenster_samples:
            segment = np.pad(segment, (0, fenster_samples - len(segment)), mode='constant')
        start_s = start / sr
        end_s = len(y) / sr
        segments.append((segment, start_s, end_s))

    return segments


# =============================================================================
# EMOTIONS-ANALYSE
# =============================================================================

def analyse_emotionen(
    audio_pfad: Path,
    model: EmotionModel
) -> Tuple[List[EmotionsSegment], np.ndarray, int]:
    """
    Analysiert das gesamte Audio in 3s-Segmenten.

    Returns:
        (segmente, audio_array, sample_rate)
    """
    y, sr = librosa.load(str(audio_pfad), sr=SAMPLE_RATE, mono=True)

    raw_segments = segmentiere_audio(y, sr)
    print(f"[emotion] {len(raw_segments)} Segmente (3s-Fenster, 1s Overlap)")

    emotion_segments = []
    for i, (seg_audio, start_s, end_s) in enumerate(raw_segments):
        arousal, valence, dominance = model.predict(seg_audio, sr)
        emotion_segments.append(EmotionsSegment(
            start_s=start_s,
            end_s=end_s,
            arousal=arousal,
            valence=valence,
            dominance=dominance
        ))
        if (i + 1) % 10 == 0:
            print(f"[emotion] {i + 1}/{len(raw_segments)} Segmente verarbeitet...")

    return emotion_segments, y, sr


# =============================================================================
# KERNBOTSCHAFT-ZUORDNUNG
# =============================================================================

def extrahiere_kernbotschaften(inhalt_data: Optional[Dict]) -> List[Dict]:
    """Extrahiert Kernbotschaften mit Text und Zeit."""
    kbs = []
    if not inhalt_data or "kernbotschaften" not in inhalt_data:
        return kbs

    for kb in inhalt_data["kernbotschaften"]:
        start = kb.get("start_ms", kb.get("start"))
        end = kb.get("end_ms", kb.get("end"))
        text = kb.get("text", "")

        if isinstance(start, str):
            start = zeitstr_to_s(start)
        if isinstance(end, str):
            end = zeitstr_to_s(end)
        if start is None or end is None:
            continue

        start_s = float(start) / 1000.0 if float(start) > 1000 else float(start)
        end_s = float(end) / 1000.0 if float(end) > 1000 else float(end)

        kbs.append({"text": text, "start_s": start_s, "end_s": end_s})

    return kbs


def hole_ton_label(inhalt_data: Optional[Dict]) -> str:
    """Extrahiert den emotionalen Ton aus der Inhaltsanalyse."""
    if not inhalt_data:
        return "sachlich"  # Default

    # Mögliche Felder: emotionaler_ton, ton, sentiment, etc.
    ton = inhalt_data.get("emotionaler_ton", inhalt_data.get("ton", "sachlich"))
    if isinstance(ton, str):
        ton = ton.lower()
        if "inspiri" in ton or "motiv" in ton:
            return "inspirierend"
        elif "ernst" in ton or "seriös" in ton or "grav" in ton:
            return "ernst"
        else:
            return "sachlich"
    return "sachlich"


def ordne_segmente_zu_kb(
    emotion_segments: List[EmotionsSegment],
    kernbotschaften: List[Dict]
) -> List[KernbotschaftEmotion]:
    """Ordnet Emotions-Segmente den Kernbotschaften zu und berechnet Mittelwerte."""
    ergebnisse = []

    for kb in kernbotschaften:
        kb_start = kb["start_s"]
        kb_end = kb["end_s"]

        # Segmente, die mit der KB überlappen
        passende = [s for s in emotion_segments
                    if s.start_s < kb_end and s.end_s > kb_start]

        if not passende:
            continue

        mean_arousal = float(np.mean([s.arousal for s in passende]))
        mean_valence = float(np.mean([s.valence for s in passende]))
        mean_dominance = float(np.mean([s.dominance for s in passende]))

        ergebnisse.append(KernbotschaftEmotion(
            text=kb["text"],
            start_s=kb_start,
            end_s=kb_end,
            mean_arousal=mean_arousal,
            mean_valence=mean_valence,
            mean_dominance=mean_dominance,
            valence_passend=False,  # Wird später gesetzt
            valence_erwartet_min=0.0,
            valence_erwartet_max=1.0,
        ))

    return ergebnisse


# =============================================================================
# SCORING
# =============================================================================

def berechne_d1_wechsel_rate(emotion_segments: List[EmotionsSegment], dauer_min: float) -> Tuple[int, float, str]:
    """
    D1: Arousal-Wechsel-Rate (40%)
    Fix v2: Wechsel = |arousal[i+1] - arousal[i]| > 0.15
    """
    if len(emotion_segments) < 2:
        return 40, 0.0, "Monoton (weniger als 2 Segmente)"

    wechsel = 0
    for i in range(len(emotion_segments) - 1):
        delta = abs(emotion_segments[i + 1].arousal - emotion_segments[i].arousal)
        if delta > AROUSAL_WECHSEL_SCHWELLE:
            wechsel += 1

    if dauer_min > 0:
        rate = wechsel / dauer_min
    else:
        rate = 0.0

    if D1_OPTIMAL_MIN <= rate <= D1_OPTIMAL_MAX:
        punkte = 100
        bewertung = "TED-Optimum"
    elif (D1_AKZEPTABEL_MIN <= rate < D1_OPTIMAL_MIN) or (D1_OPTIMAL_MAX < rate <= D1_AKZEPTABEL_MAX):
        punkte = 75
        bewertung = "Akzeptabel"
    elif rate < D1_AKZEPTABEL_MIN:
        punkte = 40
        bewertung = "Monoton"
    else:  # > 8
        punkte = 40
        bewertung = "Sprunghaft"

    return punkte, rate, bewertung


def berechne_d2_valence_passung(
    kb_emotionen: List[KernbotschaftEmotion],
    ton_label: str
) -> Tuple[int, float, str]:
    """
    D2: Valence-Passung Kernbotschaften (30%)
    Fix v2: Operationalisiert mit erwarteten Bereichen pro Ton-Label.
    """
    if not kb_emotionen:
        return 100, 1.0, "Keine Kernbotschaften"

    # Erwartete Bereiche definieren
    if ton_label == "inspirierend":
        erwartet_min, erwartet_max = VALENCE_INSPIRIEREND_MIN, 1.0
    elif ton_label == "ernst":
        erwartet_min, erwartet_max = 0.0, VALENCE_ERNST_MAX
    else:  # sachlich
        erwartet_min, erwartet_max = VALENCE_SACHLICH_MIN, VALENCE_SACHLICH_MAX

    passend = 0
    for kb in kb_emotionen:
        kb.valence_erwartet_min = erwartet_min
        kb.valence_erwartet_max = erwartet_max
        if erwartet_min <= kb.mean_valence <= erwartet_max:
            kb.valence_passend = True
            passend += 1
        else:
            kb.valence_passend = False

    anteil = passend / len(kb_emotionen)

    if anteil >= 0.70:
        punkte = 100
        bewertung = "Konsistent"
    elif anteil >= 0.50:
        punkte = 70
        bewertung = "Teilweise"
    else:
        punkte = 30
        bewertung = "Inkonsistent"

    return punkte, anteil, bewertung


def berechne_d3_dominance(
    emotion_segments: List[EmotionsSegment],
    kb_emotionen: List[KernbotschaftEmotion]
) -> Tuple[int, float, str]:
    """
    D3: Dominance bei Kernbotschaften (30%)
    Ist Dominance bei KB höher als im Durchschnitt?
    """
    if not kb_emotionen:
        return 100, 0.0, "Keine Kernbotschaften"

    # Globaler Durchschnitt
    global_dom = float(np.mean([s.dominance for s in emotion_segments]))
    kb_dom = float(np.mean([kb.mean_dominance for kb in kb_emotionen]))

    anhebung = kb_dom - global_dom

    if anhebung >= DOMINANCE_ANHEBUNG_GUT:
        punkte = 100
        bewertung = "Klare Erhöhung"
    elif anhebung >= DOMINANCE_ANHEBUNG_LEICHT:
        punkte = 70
        bewertung = "Leicht"
    else:
        punkte = 30
        bewertung = "Keine Betonung"

    return punkte, anhebung, bewertung


def berechne_gesamtscore(d1: int, d2: int, d3: int) -> int:
    score = d1 * GEWICHT_D1 + d2 * GEWICHT_D2 + d3 * GEWICHT_D3
    return int(round(score))


# =============================================================================
# REPORT
# =============================================================================


def _emotion_fallback_tipps(score: int) -> List[str]:
    if score >= 75:
        return ["Deine emotionale Variation ist bereits überzeugend. Achte weiter "
                "darauf, deine Kernaussagen bewusst mit mehr Stimmkraft zu sprechen."]
    if score >= 50:
        return ["Wähle 2-3 Stellen in deinem Text aus, an denen du bewusst "
                "energischer oder ruhiger klingen willst — kleine, gezielte "
                "Wechsel wirken schon spürbar lebendiger."]
    return ["Fang klein an: markiere im Text 2 Stellen, an denen du bewusst "
            "energischer wirst, und übe genau diese zuerst laut."]


# =============================================================================
# REPORT — KURZFASSUNG
# =============================================================================

def generiere_kurz_report(
    d1_score: int, d1_rate: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d3_score: int, d3_anhebung: float, d3_text: str,
    gesamt_score: int,
    ton_label: str,
    audio_name: str,
    audio_dauer_s: float,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"
    z = ru.kurz_header("EMOTIONALE VARIATION", audio_name, dauer_str, f"Ton (Inhalt): {ton_label}")

    if audio_dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({audio_dauer_s:.0f} Sek.) — Details dazu in der")
        z.append("    ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine emotionale Variation ist überzeugend und gut zum Inhalt passend.",
        "Deine emotionale Variation ist ausbaufähig — einzelne Bereiche wirken noch zu flach oder unpassend zum Ton des Inhalts.",
        "Deine emotionale Variation ist deutlich zu gering oder passt nicht zum Inhalt.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Abwechslung im Aktivierungsniveau", 40, d1_score, d1_text)
    z += ru.dimension_zeile_kurz("Passung zum Präsentationston", 30, d2_score, d2_text)
    z += ru.dimension_zeile_kurz("Stimmkraft bei Kernaussagen", 30, d3_score, d3_text)
    z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    for i, zeile in enumerate(_emotion_fallback_tipps(gesamt_score), 1):
        umbrochen = ru.wrap_text(zeile) if len(zeile) > 64 else [zeile]
        z.append(f"  {i}. {umbrochen[0]}")
        z += [f"     {folgezeile}" for folgezeile in umbrochen[1:]]
    z.append("")
    z.append("  Wo genau im Video deine Stimme am gleichförmigsten klingt, und")
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
    emotion_segments: List[EmotionsSegment],
    kb_emotionen: List[KernbotschaftEmotion],
    d1_score: int, d1_rate: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d3_score: int, d3_anhebung: float, d3_text: str,
    gesamt_score: int,
    ton_label: str,
    audio_name: str,
    audio_dauer_s: float,
) -> str:
    dauer_min = audio_dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"

    z = ru.detail_header("EMOTIONALE VARIATION", audio_name, dauer_str, f"Ton (Inhalt): {ton_label}")
    z += ru.build_toc([
        "Gesamtergebnis",
        "Abwechslung im Aktivierungsniveau — Begründung & Fundstellen",
        "Passung zum Präsentationston — Begründung & Fundstellen",
        "Stimmkraft bei Kernaussagen — Begründung & Fundstellen",
        "Hintergrund & Referenzwerte",
    ])
    z.append("  Ein KI-Modell hört sich deine Stimme in 3-Sekunden-Fenstern an und")
    z.append("  misst drei Dinge: wie energetisch du klingst, ob dein Ton zum Inhalt")
    z.append("  passt, und wie selbstsicher du wirkst.")
    z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine emotionale Variation ist überzeugend und gut zum Inhalt passend.",
        "Deine emotionale Variation ist ausbaufähig — einzelne Bereiche wirken noch zu flach oder unpassend zum Ton des Inhalts.",
        "Deine emotionale Variation ist deutlich zu gering oder passt nicht zum Inhalt.",
    )
    z += ru.kleine_stichprobe_warnung(audio_dauer_s)

    # ── D1 — Abwechslung im Aktivierungsniveau ────────────────────────────────
    d1_befunde = [{"arousal": s.arousal, "start_ms": s.start_s * 1000} for s in emotion_segments]
    arousal_werte = [s.arousal for s in emotion_segments]
    arousal_std = float(np.std(arousal_werte)) if len(arousal_werte) > 1 else 0.0

    for b in d1_befunde:
        b["global_monoton"] = arousal_std < 0.05

    if emotion_segments:
        min_seg = min(emotion_segments, key=lambda s: s.arousal)
        max_seg = max(emotion_segments, key=lambda s: s.arousal)
    fundstellen_d1 = []
    if emotion_segments:
        fundstellen_d1.append(ru.fundstelle_zeile(
            min_seg.start_s * 1000, f"Ruhigste Stelle bis {s_to_zeitstr(min_seg.end_s)}",
            f"Aktivierung {min_seg.arousal:.2f}"))
        if max_seg.start_s != min_seg.start_s:
            fundstellen_d1.append(ru.fundstelle_zeile(
                max_seg.start_s * 1000, f"Lebendigste Stelle bis {s_to_zeitstr(max_seg.end_s)}",
                f"Aktivierung {max_seg.arousal:.2f}"))
    else:
        fundstellen_d1 = ["  Keine Zeitfenster gemessen."]

    D1_ACHSEN = [
        ru.Achse(
            "durchgehende_monotonie", "praesenz", prioritaet=1,
            merkmal_key="global_monoton", merkmal_wert=True, min_evidenz=2,
            befund_template=f"Deine Stimme klingt über die komplette Aufnahme fast identisch energiegeladen (Schwankung nur {arousal_std:.3f}).",
            ursache_template="Das betrifft nicht eine einzelne Stelle, sondern alles.",
            uebung_template=(
                "\nSchritt 1: Markiere im Text 2 Stellen, an denen du "
                "bewusst energischer klingen willst.\n"
                "Schritt 2: Sprich genau diese 2 Stellen mit deutlich mehr "
                "Energie, 5x hintereinander.\n"
                "Schritt 3: Baue sie zurück in den Text ein und sprich die "
                "Präsentation am Stück durch."
            ),
        ),
        ru.Achse(
            "zeittrend_arousal", "trend", prioritaet=2, zeit_key="start_ms",
            befund_template="Deine Energie {richtung_text}.",
            ursache_template="{richtung_ursache}",
            uebung_template="{richtung_uebung}",
        ),
    ]
    for achse in D1_ACHSEN:
        if achse.name == "zeittrend_arousal":
            probe = ru._pruefe_trend(d1_befunde, achse, audio_dauer_s * 1000)
            if probe and getattr(probe, "richtung", "") == "anfang":
                achse.befund_template = "Deine Energie konzentriert sich auf die erste Hälfte der Aufnahme."
                achse.ursache_template = "Das kann bedeuten, dass du zum Ende hin an Energie verlierst."
                achse.uebung_template = (
                    "\nSchritt 1: Sprich nur den zweiten Teil deiner "
                    "Präsentation, bewusst mit mehr Energie als beim ersten "
                    "Versuch.\n"
                    "Schritt 2: Plane an einer Stelle im zweiten Teil einen "
                    "bewusst energiegeladenen Moment ein.\n"
                    "Schritt 3: Sprich die komplette Präsentation am Stück "
                    "durch und achte auf gleichbleibende Energie."
                )
            elif probe:
                achse.befund_template = "Deine Energie steigt Richtung Ende der Aufnahme an."
                achse.ursache_template = "Das kann bedeuten, dass du erst warm wirst, während der Anfang zu ruhig war."
                achse.uebung_template = (
                    "\nSchritt 1: Sprich nur die ersten 2 Sätze, bewusst mit "
                    "mehr Energie von Anfang an.\n"
                    "Schritt 2: Wiederhole 3x, bis sich der energische "
                    "Einstieg natürlich anfühlt.\n"
                    "Schritt 3: Sprich die ganze Präsentation durch."
                )

    for b in d1_befunde:
        b["zeit_str"] = ru.video_zeit(b["start_ms"])

    tipp_d1 = ru.erkenne_muster_v2(
        d1_befunde, D1_ACHSEN, gesamt_dauer_ms=audio_dauer_s * 1000, max_tipps=1,
        fall_a_text=["Zu wenige ausreichend lange Abschnitte für eine verlässliche "
                     "Einschätzung der Energie-Variation."],
        einzelfund_template=(
            "Bei {zeit_str} wechselt deine Energie merklich (Aktivierung "
            "{arousal:.2f}). Das ist unauffällig."
        ),
        fall_c_einleitung="Deine Energie wechselt an mehreren Stellen, aber unregelmäßig — das wirkt insgesamt lebendig, nicht eintönig:",
    )

    z += ru.dimension_block_detail(
        "Abwechslung im Aktivierungsniveau", 40, d1_score,
        was_gemessen=["Wie energetisch deine Stimme klingt, und wie oft sich",
                      "dieses Energie-Niveau spürbar verändert."],
        warum=[f"{d1_rate:.2f} spürbare Wechsel pro Minute.",
               "Referenz: erfahrene Redner wechseln 3-5x pro Minute.",
               f"Bei dir: {d1_text}"],
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 — Passung zum Präsentationston ─────────────────────────────────────
    fundstellen_d2 = []
    for kb in kb_emotionen[:5]:
        status = "passt" if kb.valence_passend else "passt nicht"
        fundstellen_d2.append(ru.fundstelle_zeile(
            kb.start_s * 1000, kb.text, f"Stimmung {kb.mean_valence:.2f} — {status}"))
    if not fundstellen_d2:
        fundstellen_d2 = ["  Keine ausreichend langen Kernaussagen für eine Messung."]

    d2_befunde = [{"satz": kb.text, "valence": kb.mean_valence, "passend": kb.valence_passend,
                   "start_ms": kb.start_s * 1000} for kb in kb_emotionen]

    D2_ACHSEN = [
        ru.Achse(
            "lokale_valence_abweichung", "anteil", prioritaet=1,
            merkmal_key="passend", merkmal_wert=False, min_evidenz=2, schwelle=0.4,
            befund_template="Bei {anteil:.0%} deiner Kernaussagen passt die Stimmung nicht ganz zum sonst erkannten Ton deiner Präsentation.",
            ursache_template="Uneinheitliche Stimmung kann beim Publikum ein widersprüchliches Gefühl hinterlassen.",
            uebung_template=(
                "\nSchritt 1: Höre dir jede betroffene Stelle einzeln an "
                "und notiere, welche Stimmung du hörst.\n"
                "Schritt 2: Sprich die Stelle neu ein, bewusst im Ton "
                "deiner restlichen Präsentation.\n"
                "Schritt 3: Baue sie zurück in den Kontext ein und höre "
                "den Übergang."
            ),
        ),
    ]

    for b in d2_befunde:
        if b["passend"]:
            b["passend_text"] = "gut"
            b["passend_uebung"] = ""
        else:
            b["passend_text"] = "nicht ganz"
            b["passend_uebung"] = (
                "\nSchritt 1: Höre dir diese Stelle an und notiere, welche "
                "Stimmung du hörst.\n"
                "Schritt 2: Sprich die Stelle neu ein, bewusst im Ton "
                "deiner restlichen Präsentation.\n"
                "Schritt 3: Baue sie zurück in den Kontext ein und höre "
                "den Übergang."
            )

    tipp_d2 = ru.erkenne_muster_v2(
        d2_befunde, D2_ACHSEN, max_tipps=1,
        fall_a_text=["Zu wenige Abschnitte für eine verlässliche Einschätzung der Ton-Passung gefunden."],
        einzelfund_template=(
            "Bei '{satz}' passt deine Stimmung {passend_text} zum sonstigen Ton "
            "deiner Präsentation.{passend_uebung}"
        ),
        fall_c_einleitung="Mehrere Stellen mit Abweichungen vom sonstigen Ton, ohne gemeinsames Muster:",
    )

    z += ru.dimension_block_detail(
        "Passung zum Präsentationston", 30, d2_score,
        was_gemessen=["Ob die Stimmung deiner Kernaussagen zum insgesamt erkannten",
                      "Grundton deiner Präsentation passt."],
        warum=[f"{d2_anteil:.0%} deiner Kernbotschaften liegen im erwarteten Bereich.",
               f"Bei dir: {d2_text}"],
        fundstellen=fundstellen_d2,
        tipp=tipp_d2,
    )

    # ── D3 — Stimmkraft bei Kernaussagen ───────────────────────────────────────
    fundstellen_d3 = []
    for kb in kb_emotionen[:5]:
        fundstellen_d3.append(ru.fundstelle_zeile(
            kb.start_s * 1000, kb.text, f"Stimmkraft {kb.mean_dominance:.2f}"))
    if not fundstellen_d3:
        fundstellen_d3 = ["  Keine ausreichend lange Kernaussage gefunden."]

    gesamt_dominance_ref = float(np.mean([s.dominance for s in emotion_segments])) if emotion_segments else 0.5
    d3_befunde = [{"satz": kb.text, "dominance": kb.mean_dominance,
                   "anhebung": kb.mean_dominance - gesamt_dominance_ref,
                   "ohne_anhebung": (kb.mean_dominance - gesamt_dominance_ref) < 0.02,
                   "start_ms": kb.start_s * 1000} for kb in kb_emotionen]

    D3_ACHSEN = [
        ru.Achse(
            "kernaussage_ohne_anhebung", "anteil", prioritaet=1,
            merkmal_key="ohne_anhebung", merkmal_wert=True, min_evidenz=2, schwelle=0.5,
            befund_template="Deine Kernaussagen zeigen kaum zusätzliche Stimmkraft gegenüber dem Rest.",
            ursache_template="Sie klingen genauso selbstsicher oder unsicher wie der Rest — keine zusätzliche Präsenz.",
            uebung_template=(
                "\nSchritt 1: Sprich diese Kernaussagen mit bewusst mehr "
                "Nachdruck, aufrecht stehend, 5x pro Satz.\n"
                "Schritt 2: Sprich den Satz davor normal, dann die "
                "Kernaussage mit Nachdruck — übe den Übergang 3x.\n"
                "Schritt 3: Nimm dich auf und höre, ob die Stimmkraft jetzt "
                "hörbar zunimmt."
            ),
        ),
        ru.Achse(
            "kernaussage_durchgehend_selbstsicher", "anteil", prioritaet=2,
            merkmal_key="ohne_anhebung", merkmal_wert=False, min_evidenz=2, schwelle=0.8,
            befund_template="Alle {n} deiner Kernaussagen klingen konsistent selbstsicherer als der Rest.",
            ursache_template="Genau diese Stimmkraft signalisiert Überzeugung beim Publikum.",
            uebung_template="Behalte diesen Nachdruck bei — hier gibt es nichts zu verbessern.",
        ),
    ]

    for b in d3_befunde:
        if b["ohne_anhebung"]:
            b["einordnung"] = (
                "klingt genauso wie der Rest — hier fehlt die zusätzliche "
                "Stimmkraft.\n"
                "Schritt 1: Sprich genau diesen Satz 5x, mit bewusst mehr "
                "Nachdruck, aufrecht stehend.\n"
                "Schritt 2: Sprich den Satz davor normal, dann diesen mit "
                "Nachdruck — 3x den Übergang üben.\n"
                "Schritt 3: Nimm dich auf und höre den Unterschied"
            )
        else:
            b["einordnung"] = "klingt deutlich selbstsicherer als der Rest — sehr gut umgesetzt"

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde, D3_ACHSEN, max_tipps=1,
        fall_a_text=["Keine wichtige Aussage lang genug für eine verlässliche Messung der Stimmkraft."],
        einzelfund_template=(
            "Diese Kernaussage {einordnung} (Anhebung {anhebung:+.3f})."
        ),
        fall_c_einleitung="Deine Kernaussagen sind unterschiedlich selbstsicher vorgetragen — bei manchen mehr Nachdruck, bei anderen kaum:",
    )

    z += ru.dimension_block_detail(
        "Stimmkraft bei Kernaussagen", 30, d3_score,
        was_gemessen=["Ob deine Stimme bei der Kernaussage selbstsicherer/präsenter",
                      "klingt als im Durchschnitt — das signalisiert Überzeugung."],
        warum=[f"Anhebung: {d3_anhebung:+.3f} gegenüber dem Gesamtdurchschnitt.",
               f"Bei dir: {d3_text}"],
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Modell: wav2vec2 (KI-Modell, hört Emotion direkt aus der Stimme, nicht")
    z.append("  aus dem Text). Skala jeweils 0.0 (sehr niedrig) bis 1.0 (sehr hoch),")
    z.append("  0.5 = mittel.")
    z.append("  Referenz: erfahrene Redner wechseln 3-5x pro Minute die Energie.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    return "\n".join(z)

# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def analyse_emotionale_variation(
    audio_pfad: Path,
    inhalt_pfad: Optional[Path] = None,
    output_json_pfad: Optional[Path] = None,
    output_txt_kurz_pfad: Optional[Path] = None,
    output_txt_detail_pfad: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Haupt-Einstiegspunkt.
    """
    if not HAS_TORCH or not HAS_TRANSFORMERS or not HAS_LIBROSA:
        raise ImportError(
            "Benötigte Pakete fehlen. Installieren:\n"
            "  pip install torch transformers librosa soundfile"
        )

    print(f"[emotion] Starte Analyse: {audio_pfad.name}")

    # 1. Modell laden
    model = EmotionModel()

    # 2. Emotionen analysieren
    emotion_segments, y, sr = analyse_emotionen(audio_pfad, model)
    audio_dauer_s = len(y) / sr
    dauer_min = audio_dauer_s / 60.0

    # 3. Inhaltsanalyse laden
    inhalt_data = lade_json(inhalt_pfad) if inhalt_pfad else None
    ton_label = hole_ton_label(inhalt_data)
    kernbotschaften = extrahiere_kernbotschaften(inhalt_data)

    # 4. KB zuordnen
    kb_emotionen = ordne_segmente_zu_kb(emotion_segments, kernbotschaften)
    print(f"[emotion] {len(kb_emotionen)} Kernbotschaften mit Emotionsdaten versehen.")

    # 5. Scoring
    d1_score, d1_rate, d1_text = berechne_d1_wechsel_rate(emotion_segments, dauer_min)
    d2_score, d2_anteil, d2_text = berechne_d2_valence_passung(kb_emotionen, ton_label)
    d3_score, d3_anhebung, d3_text = berechne_d3_dominance(emotion_segments, kb_emotionen)
    gesamt_score = berechne_gesamtscore(d1_score, d2_score, d3_score)

    print(f"[emotion] Scoring: D1={d1_score}, D2={d2_score}, D3={d3_score}, Gesamt={gesamt_score}")

    # 6. Output
    output_data = {
        "modul": "emotionale_variation_analyse",
        "version": "2.0",
        "timestamp": datetime.now().isoformat(),
        "input": str(audio_pfad),
        "meta": {
            "audio_dauer_s": round(audio_dauer_s, 3),
            "model": MODEL_NAME,
            "fenster_s": FENSTER_S,
            "hop_s": HOP_S,
            "segmente_anzahl": len(emotion_segments),
            "ton_label": ton_label,
        },
        "segmente": [s.to_dict() for s in emotion_segments],
        "kernbotschaften": [k.to_dict() for k in kb_emotionen],
        "scoring": {
            "d1_arousal_wechsel": {
                "gewichtung": GEWICHT_D1,
                "punkte": d1_score,
                "bewertung": d1_text,
                "wechsel_rate_pro_min": round(d1_rate, 2),
                "wechsel_schwelle": AROUSAL_WECHSEL_SCHWELLE
            },
            "d2_valence_passung": {
                "gewichtung": GEWICHT_D2,
                "punkte": d2_score,
                "bewertung": d2_text,
                "anteil_passend": round(d2_anteil, 4),
                "ton_label": ton_label
            },
            "d3_dominance_anhebung": {
                "gewichtung": GEWICHT_D3,
                "punkte": d3_score,
                "bewertung": d3_text,
                "anhebung": round(d3_anhebung, 4)
            },
            "gesamtscore": gesamt_score
        }
    }

    if output_json_pfad:
        output_json_pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_pfad, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2,
                      default=lambda x: float(x) if isinstance(x, np.floating) else x)
        print(f"[emotion] JSON gespeichert: {output_json_pfad}")

    if output_txt_kurz_pfad:
        output_txt_kurz_pfad.parent.mkdir(parents=True, exist_ok=True)
        kurz_report = generiere_kurz_report(
            d1_score, d1_rate, d1_text,
            d2_score, d2_anteil, d2_text,
            d3_score, d3_anhebung, d3_text,
            gesamt_score, ton_label,
            audio_pfad.name, audio_dauer_s
        )
        with open(output_txt_kurz_pfad, "w", encoding="utf-8") as f:
            f.write(kurz_report)
        print(f"[emotion] Kurz-Report gespeichert: {output_txt_kurz_pfad}")

    if output_txt_detail_pfad:
        output_txt_detail_pfad.parent.mkdir(parents=True, exist_ok=True)
        detail_report = generiere_detail_report(
            emotion_segments, kb_emotionen,
            d1_score, d1_rate, d1_text,
            d2_score, d2_anteil, d2_text,
            d3_score, d3_anhebung, d3_text,
            gesamt_score, ton_label,
            audio_pfad.name, audio_dauer_s
        )
        with open(output_txt_detail_pfad, "w", encoding="utf-8") as f:
            f.write(detail_report)
        print(f"[emotion] Detail-Report gespeichert: {output_txt_detail_pfad}")

    print(f"[emotion] Fertig. Gesamt-Score: {gesamt_score}/100")
    return output_data


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Emotionale Variation Analyse für Präsentationsbewertungs-AI")
    parser.add_argument("audio", type=str, help="Pfad zur Audio-Datei")
    parser.add_argument("--inhalt", type=str, default=None, help="Pfad zu inhalt_analyse_output.json")
    parser.add_argument("--output-json", type=str, default="zwischen_output/emotionale_variation_analyse_output.json")
    parser.add_argument("--output-txt", type=str, default=None)

    args = parser.parse_args()

    audio = Path(args.audio)
    inhalt = Path(args.inhalt) if args.inhalt else None
    out_json = Path(args.output_json)

    if args.output_txt:
        out_txt = Path(args.output_txt)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_txt = Path("reports/emotion") / f"emotion_report_{ts}.txt"

    if not audio.exists():
        print(f"[FEHLER] Audio nicht gefunden: {audio}")
        exit(1)

    try:
        analyse_emotionale_variation(audio, inhalt, out_json, out_txt)
    except Exception as e:
        print(f"[FEHLER] {e}")
        raise
