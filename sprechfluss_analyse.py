#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sprechfluss_analyse.py
======================
Erkennt Wiederholungen und Abbrüche im Redefluss.
Bewertet kontextuell: Disfluenzen bei Kernbotschaften wiegen schwerer.

Input:
  - Transkript: "Wort HH:MM:SS.mmm HH:MM:SS.mmm"
  - inhalt_analyse_output.json (optional, für Satzgrenzen + Kernbotschaften)
  - pausen_analyse_output.json (optional, für Stocker-Info, NICHT für Scoring)

Output:
  - zwischen_output/sprechfluss_analyse_output.json
  - reports/sprechfluss/sprechfluss_report_[TIMESTAMP].txt"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

import report_utils as ru


# =============================================================================
# KONSTANTEN
# =============================================================================

GEWICHT_D1 = 0.40
GEWICHT_D2 = 0.30
GEWICHT_D3 = 0.30

# D1: Wiederholungs- + Abbruch-Rate pro Minute
# Studien: Bosker et al. 2013, TED-Niveau
D1_PERFECT = 0.0
D1_SEHR_FLUESSIG = 0.5
D1_FLUESSIG = 1.0
D1_AUFFAELLIG = 2.0

# D2: Kernbotschafts-Fluenz (Anteil sauber)
D2_PERFECT = 1.0
D2_SEHR_GUT = 0.80
D2_AKZEPTABEL = 0.60
D2_AUFFAELLIG = 0.40

# D3: Cluster-Anteil (Sätze mit >1 Disfluenz / Sätze mit ≥1 Disfluenz)
D3_ISOLIERT = 0.10
D3_GELEGENTLICH = 0.25
D3_HAEUFIG = 0.50


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class Wort:
    text: str
    start_ms: float
    end_ms: float
    index: int = 0


@dataclass
class Satz:
    index: int
    text: str
    start_ms: float
    end_ms: float
    woerter: List[Wort] = field(default_factory=list)
    wortanzahl: int = 0
    ist_kernbotschaft: bool = False


@dataclass
class Disfluenz:
    """Ein Disfluenz-Ereignis: Wiederholung oder Abbruch."""
    typ: str                    # "wiederholung" oder "abbruch"
    position: int               # Wort-Index
    satz_index: int
    wort: str
    wort_gereinigt: str
    start_ms: float
    end_ms: float
    kontext: str = ""           # z.B. "in Kernbotschaft"

    def to_dict(self) -> dict:
        return {
            "typ": self.typ,
            "position": self.position,
            "satz_index": self.satz_index,
            "wort": self.wort,
            "wort_gereinigt": self.wort_gereinigt,
            "start_ms": round(self.start_ms, 3),
            "end_ms": round(self.end_ms, 3),
            "kontext": self.kontext,
        }


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def zeitstr_to_ms(zeit_str: str) -> float:
    """Parst Zeitstempel zu ms."""
    zeit_str = zeit_str.strip()
    if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}$", zeit_str):
        h, m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", zeit_str):
        m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(m) * 60000 + int(s) * 1000 + int(ms)
    for fmt in ("%H:%M:%S.%f", "%M:%S.%f", "%H:%M:%S", "%M:%S"):
        try:
            dt = datetime.strptime(zeit_str, fmt)
            return (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000 + dt.microsecond // 1000
        except ValueError:
            continue
    raise ValueError(f"Unbekanntes Zeitformat: {zeit_str}")


def ms_to_zeitstr(ms: float) -> str:
    """ms zu lesbarem Zeitstempel."""
    ms = max(0, ms)
    total_sec = int(ms // 1000)
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    millis = int(ms % 1000)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{millis:03d}"
    return f"{m:02d}:{s:02d}.{millis:03d}"


def parse_transkript(transkript_pfad: Path) -> List[Wort]:
    """Parst Transkript im Format: Wort HH:MM:SS.mmm HH:MM:SS.mmm"""
    woerter = []
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)$")

    with open(transkript_pfad, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                wort_text, start_str, end_str = m.groups()
            else:
                teile = line.split()
                if len(teile) >= 3:
                    wort_text, start_str, end_str = teile[0], teile[-2], teile[-1]
                else:
                    continue
            try:
                start_ms = zeitstr_to_ms(start_str)
                end_ms = zeitstr_to_ms(end_str)
            except ValueError:
                continue
            woerter.append(Wort(text=wort_text, start_ms=start_ms, end_ms=end_ms, index=len(woerter)))

    woerter.sort(key=lambda w: w.start_ms)
    for i, w in enumerate(woerter):
        w.index = i
    return woerter


def lade_json(pfad: Path) -> Optional[Dict]:
    if not pfad.exists():
        return None
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Konnte {pfad} nicht laden: {e}")
        return None


def extrahiere_saetze(woerter: List[Wort], inhalt_data: Optional[Dict]) -> List[Satz]:
    """Extrahiert Sätze aus Inhaltsanalyse oder heuristisch."""
    saetze = []

    if inhalt_data and "satzgrenzen" in inhalt_data:
        raw_saetze = inhalt_data["satzgrenzen"]
        for i, raw in enumerate(raw_saetze):
            start_ms = raw.get("start_ms", raw.get("start"))
            end_ms = raw.get("end_ms", raw.get("end"))
            if isinstance(start_ms, str):
                start_ms = zeitstr_to_ms(start_ms)
            if isinstance(end_ms, str):
                end_ms = zeitstr_to_ms(end_ms)
            if start_ms is None or end_ms is None:
                continue
            satz_woerter = [w for w in woerter if start_ms <= w.start_ms < end_ms]
            saetze.append(Satz(
                index=i,
                text=raw.get("text", ""),
                start_ms=float(start_ms),
                end_ms=float(end_ms),
                woerter=satz_woerter,
                wortanzahl=len(satz_woerter)
            ))

    if not saetze:
        # Heuristik: Satzende bei . ! ?
        aktuelle = []
        idx = 0
        for w in woerter:
            aktuelle.append(w)
            if w.text.rstrip().endswith((".", "!", "?")) and len(aktuelle) > 1:
                saetze.append(Satz(
                    index=idx,
                    text=" ".join(x.text for x in aktuelle),
                    start_ms=aktuelle[0].start_ms,
                    end_ms=aktuelle[-1].end_ms,
                    woerter=list(aktuelle),
                    wortanzahl=len(aktuelle)
                ))
                aktuelle = []
                idx += 1
        if aktuelle:
            saetze.append(Satz(
                index=idx,
                text=" ".join(x.text for x in aktuelle),
                start_ms=aktuelle[0].start_ms,
                end_ms=aktuelle[-1].end_ms,
                woerter=aktuelle,
                wortanzahl=len(aktuelle)
            ))

    return saetze


def markiere_kernbotschaften(saetze: List[Satz], inhalt_data: Optional[Dict]) -> None:
    if not inhalt_data or "kernbotschaften" not in inhalt_data:
        return
    for kb in inhalt_data["kernbotschaften"]:
        kb_start = kb.get("start_ms", kb.get("start"))
        kb_end = kb.get("end_ms", kb.get("end"))
        if isinstance(kb_start, str):
            kb_start = zeitstr_to_ms(kb_start)
        if isinstance(kb_end, str):
            kb_end = zeitstr_to_ms(kb_end)
        if kb_start is None or kb_end is None:
            continue
        for s in saetze:
            if s.start_ms <= float(kb_end) and s.end_ms >= float(kb_start):
                s.ist_kernbotschaft = True


# =============================================================================
# DISFLUENZ-ERKENNUNG
# =============================================================================

def bereinige_wort(wort: str) -> str:
    """Entfernt Interpunktion für den Vergleich.

    Fix: Die Vorgänger-Version verwendete r"[^\\w\\-äöüÄÖÜß]", wodurch die
    doppelten Backslashes und der '-' zwischen '\\' und 'ä' versehentlich eine
    Range von ord(92) bis ord(228) bildeten. Das behielt alle
    Kleinbuchstaben, strippte aber Grossbuchstaben — satzinitiale
    Wiederholungen wie "Also also" wurden nicht als Match erkannt.
    Jetzt: nur einfache Backslashes.
    """
    return re.sub(r"[^\w\-äöüÄÖÜß]", "", wort, flags=re.UNICODE).lower()


def ist_abbruch(wort: str) -> bool:
    """
    Prüft ob ein Wort ein Abbruch ist.

    Fix: Die Heuristik `"-" in wort and len(wort) < 6` markierte auch
    normale Wörter mit Bindestrich fälschlich als Abbruch (z.B. "e-Mail",
    "5-fach", "T-Shirt"). Whisper-Transkripte enthalten praktisch keine
    Fragment-Marker ausser "wort-" am Wortende. Deshalb nur noch: Wort
    endet auf "-" (ohne dass weiteres folgt).
    """
    stripped = wort.rstrip(".,;:!?\"'()[]")
    return stripped.endswith("-") and len(stripped) > 1


def erkenne_disfluenzen(woerter: List[Wort], saetze: List[Satz]) -> List[Disfluenz]:
    """
    Erkennt Wiederholungen und Abbrüche.

    Wiederholung: wort_N == wort_N+1 (case-insensitiv, ohne Interpunktion)
    Abbruch: Wort endet auf "-" oder Fragment-Heuristik
    """
    disfluenzen = []

    # Schneller Lookup: Wort-Index -> Satz
    wort_zu_satz = {}
    for s in saetze:
        for w in s.woerter:
            wort_zu_satz[w.index] = s

    # 1. Wiederholungen erkennen
    for i in range(len(woerter) - 1):
        w1 = woerter[i]
        w2 = woerter[i + 1]

        w1_clean = bereinige_wort(w1.text)
        w2_clean = bereinige_wort(w2.text)

        if w1_clean and w1_clean == w2_clean and len(w1_clean) > 1:
            s = wort_zu_satz.get(w1.index)
            kontext = "in Kernbotschaft" if s and s.ist_kernbotschaft else ""
            disfluenzen.append(Disfluenz(
                typ="wiederholung",
                position=i,
                satz_index=s.index if s else -1,
                wort=w1.text,
                wort_gereinigt=w1_clean,
                start_ms=w1.start_ms,
                end_ms=w2.end_ms,
                kontext=kontext
            ))

    # 2. Abbrüche erkennen
    for w in woerter:
        if ist_abbruch(w.text):
            s = wort_zu_satz.get(w.index)
            kontext = "in Kernbotschaft" if s and s.ist_kernbotschaft else ""
            disfluenzen.append(Disfluenz(
                typ="abbruch",
                position=w.index,
                satz_index=s.index if s else -1,
                wort=w.text,
                wort_gereinigt=bereinige_wort(w.text),
                start_ms=w.start_ms,
                end_ms=w.end_ms,
                kontext=kontext
            ))

    # Sortieren nach Position
    disfluenzen.sort(key=lambda d: d.position)
    return disfluenzen


# =============================================================================
# SCORING
# =============================================================================

def berechne_d1(disfluenzen: List[Disfluenz], dauer_min: float) -> Tuple[int, float, str]:
    """
    D1: Wiederholungs- + Abbruch-Rate (40%)
    Ereignisse pro Minute.
    """
    anzahl = len(disfluenzen)
    if dauer_min > 0:
        rate = anzahl / dauer_min
    else:
        rate = 0.0

    if rate == 0:
        punkte = 100
        bewertung = "Perfekt"
    elif rate < D1_SEHR_FLUESSIG:
        punkte = 90
        bewertung = "Sehr flüssig"
    elif rate < D1_FLUESSIG:
        punkte = 70
        bewertung = "Flüssig"
    elif rate < D1_AUFFAELLIG:
        punkte = 40
        bewertung = "Auffällig"
    else:
        punkte = 20
        bewertung = "Störend"

    return punkte, rate, bewertung


def berechne_d2(saetze: List[Satz], disfluenzen: List[Disfluenz]) -> Tuple[int, float, str]:
    """
    D2: Kernbotschafts-Fluenz (30%)
    Prozent der Kernbotschaften ohne Wiederholung/Abbruch.
    """
    kernbotschaften = [s for s in saetze if s.ist_kernbotschaft]
    if not kernbotschaften:
        return 100, 1.0, "Keine Kernbotschaften gefunden"

    # Disfluenzen nach Satz-Index gruppieren
    disfluenz_saetze = set(d.satz_index for d in disfluenzen)

    sauber = sum(1 for s in kernbotschaften if s.index not in disfluenz_saetze)
    anteil = sauber / len(kernbotschaften)

    if anteil >= D2_PERFECT:
        punkte = 100
        bewertung = "Perfekt"
    elif anteil >= D2_SEHR_GUT:
        punkte = 85
        bewertung = "Sehr gut"
    elif anteil >= D2_AKZEPTABEL:
        punkte = 65
        bewertung = "Akzeptabel"
    elif anteil >= D2_AUFFAELLIG:
        punkte = 40
        bewertung = "Auffällig"
    else:
        punkte = 20
        bewertung = "Kritisch"

    return punkte, anteil, bewertung


def berechne_d3(saetze: List[Satz], disfluenzen: List[Disfluenz]) -> Tuple[int, float, str]:
    """
    D3: Länge der Disfluenz-Cluster (30%)
    Anteil der Sätze mit >1 Disfluenz-Ereignis an allen Sätzen mit ≥1 Ereignis.
    """
    if not disfluenzen:
        return 100, 0.0, "Keine Disfluenzen"

    # Gruppiere Disfluenzen nach Satz
    satz_disfluenzen: Dict[int, int] = {}
    for d in disfluenzen:
        satz_disfluenzen[d.satz_index] = satz_disfluenzen.get(d.satz_index, 0) + 1

    saetze_mit_disfluenz = sum(1 for count in satz_disfluenzen.values() if count >= 1)
    saetze_mit_cluster = sum(1 for count in satz_disfluenzen.values() if count > 1)

    if saetze_mit_disfluenz == 0:
        return 100, 0.0, "Keine betroffenen Sätze"

    cluster_anteil = saetze_mit_cluster / saetze_mit_disfluenz

    if cluster_anteil < D3_ISOLIERT:
        punkte = 100
        bewertung = "Isolierte Aussetzer"
    elif cluster_anteil < D3_GELEGENTLICH:
        punkte = 75
        bewertung = "Gelegentliche Cluster"
    elif cluster_anteil < D3_HAEUFIG:
        punkte = 45
        bewertung = "Häufige Cluster"
    else:
        punkte = 20
        bewertung = "Sprachplanungs-Problem"

    return punkte, cluster_anteil, bewertung


def berechne_gesamtscore(d1: int, d2: int, d3: int) -> int:
    score = d1 * GEWICHT_D1 + d2 * GEWICHT_D2 + d3 * GEWICHT_D3
    return int(round(score))


# =============================================================================
# STOCKER-INFO (nur informativ, Fix v2)
# =============================================================================

def lade_stocker_info(pausen_data: Optional[Dict]) -> Dict[str, Any]:
    """Liest Stocker-Statistiken aus pausen_analyse_output.json — nur für den Report."""
    if not pausen_data:
        return {"verfuegbar": False, "stocker_anzahl": 0, "stocker_rate": 0.0}

    statistiken = pausen_data.get("statistiken", {})
    return {
        "verfuegbar": True,
        "stocker_anzahl": statistiken.get("anzahl_stocker", 0),
        "stocker_rate": statistiken.get("stocker_rate_pro_min", 0.0),
        "stocker_details": [
            p for p in pausen_data.get("pausen", [])
            if p.get("typ") in ("kleiner_stocker", "stocker", "stocker_lang", "zu_lang")
        ]
    }


# =============================================================================
# REPORT
# =============================================================================


def _satz_text(saetze, satz_index: int) -> str:
    if 0 <= satz_index < len(saetze):
        return saetze[satz_index].text
    return ""


# =============================================================================
# REPORT — KURZFASSUNG
# =============================================================================

def generiere_kurz_report(
    disfluenzen: List[Disfluenz],
    d1_score: int, d1_rate: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d3_score: int, d3_anteil: float, d3_text: str,
    gesamt_score: int,
    dauer_min: float,
    transkript_name: str,
    stocker_info: Dict[str, Any],
) -> str:
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"
    z = ru.kurz_header("SPRECHFLUSS", transkript_name, dauer_str)

    if dauer_min * 60 < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({dauer_min*60:.0f} Sek.) — Details dazu in der")
        z.append("    ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Dein Redefluss ist überzeugend — kaum Wiederholungen oder Abbrüche.",
        "Dein Redefluss ist ausbaufähig. An einzelnen Stellen stockt die Rede.",
        "Dein Redefluss ist deutlich beeinträchtigt.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Sprechfehler-Rate", 40, d1_score, d1_text)
    z += ru.dimension_zeile_kurz("Kernbotschafts-Fluenz", 30, d2_score, d2_text)
    z += ru.dimension_zeile_kurz("Gehäufte Fehler im Satz", 30, d3_score, d3_text)
    z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    z += _empfehlungen(gesamt_score)
    z.append("")
    z.append("  Wo genau im Video jede Wiederholung/jeder Abbruch fällt, steht im")
    z.append("  ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)
    return "\n".join(z)


def _empfehlungen(gesamt_score: int) -> List[str]:
    z = []
    if gesamt_score >= 75:
        z.append("  Bereits überzeugend — weiter so:")
        z.append("  1. Nimm dich gelegentlich auf und höre gezielt auf Restunsicherheiten.")
        z.append("  2. Übe neue Inhalte frei, ohne Skript, um Wiederholungen zu reduzieren.")
    elif gesamt_score >= 50:
        z.append("  1. Markiere im Skript die Stellen, an denen du häufig hängenbleibst.")
        z.append("  2. Lerne deine Kernaussagen auswendig — gesicherter Inhalt wird")
        z.append("     automatisch flüssiger gesprochen.")
    else:
        z.append("  1. Vertiefe die Inhalte, bevor du sie präsentierst — unsicheres")
        z.append("     Wissen ist die Hauptursache für Abbrüche und Wiederholungen.")
        z.append("  2. Arbeite mit einer klaren Gliederung statt ausformuliertem Text.")
    return z


# =============================================================================
# REPORT — DETAILANSICHT
# =============================================================================

def generiere_detail_report(
    disfluenzen: List[Disfluenz],
    saetze: List["Satz"],
    d1_score: int, d1_rate: float, d1_text: str,
    d2_score: int, d2_anteil: float, d2_text: str,
    d3_score: int, d3_anteil: float, d3_text: str,
    gesamt_score: int,
    dauer_min: float,
    transkript_name: str,
    stocker_info: Dict[str, Any],
) -> str:
    dauer_s = dauer_min * 60
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"

    z = ru.detail_header("SPRECHFLUSS", transkript_name, dauer_str)
    z += ru.build_toc([
        "Gesamtergebnis",
        "Sprechfehler-Rate — Begründung & Fundstellen",
        "Kernbotschafts-Fluenz — Begründung & Fundstellen",
        "Gehäufte Fehler im Satz — Begründung & Fundstellen",
        "Kurze Pausen / Stocken (informativ)",
        "Hintergrund & Referenzwerte",
    ])

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Dein Redefluss ist überzeugend — kaum Wiederholungen oder Abbrüche.",
        "Dein Redefluss ist ausbaufähig. An einzelnen Stellen stockt die Rede.",
        "Dein Redefluss ist deutlich beeinträchtigt.",
    )
    z += ru.kleine_stichprobe_warnung(dauer_s)

    wiederholungen = [d for d in disfluenzen if d.typ == "wiederholung"]
    abbrueche = [d for d in disfluenzen if d.typ == "abbruch"]

    # ── D1 — Sprechfehler-Rate ────────────────────────────────────────────────
    fundstellen_d1 = []
    for d in disfluenzen[:8]:
        satz = ru.markiere_stelle_im_satz(_satz_text(saetze, d.satz_index), d.wort, "⚠")
        zusatz = f"{d.typ}" + (", in Kernaussage" if d.kontext else "")
        fundstellen_d1.append(ru.fundstelle_zeile(d.start_ms, satz, zusatz))
    if len(disfluenzen) > 8:
        fundstellen_d1.append(f"  ... und {len(disfluenzen) - 8} weitere Stellen im Video.")
    if not fundstellen_d1:
        fundstellen_d1 = ["  Keine Wiederholungen oder Abbrüche erkannt."]

    warum_d1 = [
        f"{len(wiederholungen)} Wortwiederholungen und {len(abbrueche)} Satzabbrüche",
        f"in {dauer_s:.0f} Sekunden — hochgerechnet auf eine Minute: {d1_rate:.1f}",
        "Ereignisse pro Minute.",
        "Faustregel: 0 Ereignisse/Min ist das Ziel — jede Wiederholung fällt auf.",
        f"Bei dir: {d1_text}",
    ]
    if dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        warum_d1.append("")
        warum_d1.append("⚠ Bei dieser kurzen Aufnahme reicht 1 Ereignis, um den")
        warum_d1.append("  Minutenwert stark zu verzerren.")

    # Befund-Liste für die Muster-Engine
    d1_befunde = []
    for d in disfluenzen:
        idx = d.satz_index
        satz_position = "anfang"
        if 0 <= idx < len(saetze):
            woerter_texte = saetze[idx].text.split()
            try:
                wort_idx = woerter_texte.index(d.wort)
                satz_position = "anfang" if wort_idx <= 1 else "sonstwo"
            except ValueError:
                pass
        d1_befunde.append({
            "typ": d.typ,
            "satz": _satz_text(saetze, idx),
            "satz_position": satz_position,
            "start_ms": d.start_ms,
        })

    D1_ACHSEN = [
        ru.Achse(
            "typ_dominant_abbruch", "anteil", prioritaet=1,
            merkmal_key="typ", merkmal_wert="abbruch", min_evidenz=2, schwelle=0.65,
            befund_template="Bei dir kommen deutlich mehr Satzabbrüche vor als Wortwiederholungen ({anteil:.0%} der Fälle).",
            ursache_template="Das deutet eher darauf hin, dass du dir beim Satzbau selbst noch unsicher bist, als bei einzelnen Wörtern.",
            uebung_template=(
                "\nSchritt 1: Lies dir alle betroffenen Sätze einmal in "
                "Ruhe schriftlich durch.\n"
                "Schritt 2: Sprich jeden Satz einmal ganz zu Ende laut, "
                "auch wenn es holprig klingt.\n"
                "Schritt 3: Sprich alle betroffenen Sätze nacheinander am "
                "Stück, bis keiner mehr abbricht."
            ),
        ),
        ru.Achse(
            "typ_dominant_wiederholung", "anteil", prioritaet=1,
            merkmal_key="typ", merkmal_wert="wiederholung", min_evidenz=2, schwelle=0.65,
            befund_template="Bei dir kommen deutlich mehr Wortwiederholungen vor als Satzabbrüche ({anteil:.0%} der Fälle).",
            ursache_template="Das ist meist reine Wortfindung — du weißt, was du sagen willst, das Wort kommt nur nicht sofort.",
            uebung_template=(
                "\nSchritt 1: Sprich jedes betroffene Wort einzeln, 5x "
                "laut hintereinander.\n"
                "Schritt 2: Baue es in einen kurzen Testsatz ein, 3x "
                "wiederholen.\n"
                "Schritt 3: Sprich die echten Sätze mit diesem Wort aus "
                "deinem Text, je 3x."
            ),
        ),
        ru.Achse(
            "satzanfang_haeufung", "anteil", prioritaet=2,
            merkmal_key="satz_position", merkmal_wert="anfang", min_evidenz=2, schwelle=0.5,
            befund_template="Deine Versprecher häufen sich am Satzanfang ({anteil:.0%}).",
            ursache_template="Das deutet auf einen unsicheren Einstieg in neue Gedanken hin.",
            uebung_template=(
                "\nSchritt 1: Schreibe dir die ersten 3 Wörter jedes "
                "betroffenen Satzes auf.\n"
                "Schritt 2: Sprich jede Dreiergruppe 5x laut, bevor du "
                "zum nächsten Satz übergehst.\n"
                "Schritt 3: Sprich die ganzen Sätze am Stück, mit Fokus "
                "nur auf den Anfang."
            ),
        ),
        ru.Achse(
            "zeittrend", "trend", prioritaet=3, zeit_key="start_ms",
            befund_template="Deine Versprecher häufen sich in der {richtung_text}.",
            ursache_template="{richtung_ursache}",
            uebung_template="{richtung_uebung}",
        ),
    ]
    for achse in D1_ACHSEN:
        if achse.name == "zeittrend":
            probe = ru._pruefe_trend(d1_befunde, achse, dauer_s * 1000)
            if probe and getattr(probe, "richtung", "") == "anfang":
                achse.befund_template = "Deine Versprecher häufen sich in der ERSTEN Hälfte der Aufnahme."
                achse.ursache_template = "Das kann auf einen unsicheren Einstieg hindeuten."
                achse.uebung_template = (
                    "\nSchritt 1: Sprich die ersten 2 Minuten deines Texts "
                    "einmal komplett zum Aufwärmen laut durch.\n"
                    "Schritt 2: Atme kurz durch, dann starte die eigentliche "
                    "Aufnahme.\n"
                    "Schritt 3: Nimm speziell den Anfang nochmal separat auf "
                    "und vergleiche."
                )
            elif probe:
                achse.befund_template = "Deine Versprecher häufen sich in der ZWEITEN Hälfte der Aufnahme."
                achse.ursache_template = "Das kann mit nachlassender Konzentration oder Ermüdung zusammenhängen."
                achse.uebung_template = (
                    "\nSchritt 1: Übe gezielt den zweiten Teil deiner "
                    "Präsentation separat, ausgeruht.\n"
                    "Schritt 2: Plane bei längeren Präsentationen eine kurze "
                    "Pause vor dem letzten Drittel ein.\n"
                    "Schritt 3: Sprich die komplette Präsentation am Stück "
                    "und achte gezielt auf den zweiten Teil."
                )

    tipp_d1 = ru.erkenne_muster_v2(
        d1_befunde, D1_ACHSEN, gesamt_dauer_ms=dauer_s * 1000, max_tipps=1,
        fall_a_text=["Keine Wortwiederholungen oder Satzabbrüche gefunden — dein "
                     "Redefluss ist in dieser Aufnahme sehr sauber."],
        einzelfund_template=(
            "Bei '{satz}' bist du ins Stocken geraten ({typ}).\n"
            "Schritt 1: Sprich diesen Satz alleine, 3x laut am Stück.\n"
            "Schritt 2: Sprich den Satz davor und diesen zusammen, "
            "3x hintereinander.\n"
            "Schritt 3: Nimm dich auf — kommt die Stelle jetzt flüssig?"
        ),
        fall_c_einleitung="Mehrere Versprecher, aber ohne erkennbares gemeinsames Muster in Typ, Position oder Zeitpunkt — das ist natürliche Streuung:",
    )

    z += ru.dimension_block_detail(
        "Sprechfehler-Rate", 40, d1_score,
        was_gemessen=["Wie oft du pro Minute Wörter wiederholst oder Sätze",
                      "abbrichst — jede Wiederholung und jeder Abbruch zählt als",
                      "ein Ereignis."],
        warum=warum_d1,
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 — Kernbotschafts-Fluenz ────────────────────────────────────────────
    kern_saetze = [s for s in saetze if s.ist_kernbotschaft]
    unsauber = [s for s in kern_saetze if any(d.satz_index == s.index for d in disfluenzen)]
    fundstellen_d2 = []
    for s in unsauber[:5]:
        fundstellen_d2.append(ru.fundstelle_zeile(s.start_ms, s.text, "nicht sauber gesprochen"))
    if not fundstellen_d2:
        fundstellen_d2 = ["  Alle Kernaussagen wurden sauber gesprochen — nichts zum"
                           " Nachschauen." if kern_saetze else
                           "  Keine Kernbotschaften erkannt."]

    d2_befunde = [{"satz": s.text, "start_ms": s.start_ms} for s in unsauber]

    D2_ACHSEN = [
        ru.Achse(
            "mehrere_kernaussagen_betroffen", "anteil", prioritaet=1,
            merkmal_key="_immer", merkmal_wert=True, min_evidenz=2, schwelle=0.0,
            befund_template="Bei {n} deiner wichtigsten Aussagen kam es zu einer Wiederholung oder einem Abbruch.",
            ursache_template="Genau diese Stellen sollten möglichst flüssig sitzen, weil das Publikum dort am aufmerksamsten zuhört.",
            uebung_template=(
                "\nSchritt 1: Schreibe dir jede betroffene Kernaussage "
                "einzeln auf.\n"
                "Schritt 2: Sprich jede 8x laut, bis sie auswendig sitzt, "
                "statt sie frei zu formulieren.\n"
                "Schritt 3: Baue den Satz davor wieder ein und übe den "
                "Übergang 3x."
            ),
        ),
    ]
    for b in d2_befunde:
        b["_immer"] = True

    tipp_d2 = ru.erkenne_muster_v2(
        d2_befunde, D2_ACHSEN, max_tipps=1,
        fall_a_text=["Keine deiner wichtigen Aussagen war von einem Versprecher "
                     "betroffen — sehr gut, genau an den wichtigsten Stellen bist du sicher."],
        einzelfund_template=(
            "Bei '{satz}' — einer deiner Kernaussagen — bist du ins Stocken "
            "geraten. Das ist die Stelle, die am meisten sitzen sollte.\n"
            "Schritt 1: Schreibe dir genau diesen Satz einzeln auf.\n"
            "Schritt 2: Sprich ihn 8x laut, bis er automatisch kommt.\n"
            "Schritt 3: Baue den Satz davor wieder ein und übe den ganzen "
            "Übergang 3x am Stück."
        ),
        fall_c_einleitung="Von deinen Kernaussagen sind mehrere betroffen, ohne dass sich ein gemeinsamer Grund erkennen lässt:",
    )

    z += ru.dimension_block_detail(
        "Kernbotschafts-Fluenz", 30, d2_score,
        was_gemessen=["Ob deine wichtigsten Aussagen ohne Unterbrechung gesprochen",
                      "wurden. 'Sauber' heißt: ohne Wiederholung oder Abbruch."],
        warum=[f"{d2_anteil:.0%} deiner Kernbotschaften wurden sauber gesprochen",
               f"({len(kern_saetze) - len(unsauber)} von {len(kern_saetze)}).",
               f"Bei dir: {d2_text}"],
        fundstellen=fundstellen_d2,
        tipp=tipp_d2,
    )

    # ── D3 — Gehäufte Fehler im Satz ──────────────────────────────────────────
    satz_fehler = {}
    for d in disfluenzen:
        satz_fehler[d.satz_index] = satz_fehler.get(d.satz_index, 0) + 1
    gehaeuft = [(i, c) for i, c in satz_fehler.items() if c > 1]
    fundstellen_d3 = []
    for i, c in gehaeuft[:5]:
        fundstellen_d3.append(ru.fundstelle_zeile(
            saetze[i].start_ms if 0 <= i < len(saetze) else 0,
            _satz_text(saetze, i), f"{c} Fehler in diesem Satz"))
    if not fundstellen_d3:
        fundstellen_d3 = ["  Keine Sätze mit gehäuften Fehlern."]

    d3_befunde = [{"satz": _satz_text(saetze, i), "anzahl": c,
                   "start_ms": saetze[i].start_ms if 0 <= i < len(saetze) else 0}
                  for i, c in gehaeuft]

    D3_ACHSEN = [
        ru.Achse(
            "schwer_gehaeuft", "anteil", prioritaet=1,
            merkmal_key="stark_betroffen", merkmal_wert=True, min_evidenz=2, schwelle=0.3,
            befund_template="Bei {n} deiner betroffenen Sätze sind es sogar 3 oder mehr Fehler auf einmal.",
            ursache_template="Das ist ein deutliches Zeichen, dass genau diese Sätze inhaltlich noch nicht sicher genug sitzen — nicht nur ein kleiner Ausrutscher.",
            uebung_template=(
                "\nSchritt 1: Lies dir den Inhalt dieser Sätze nochmal in "
                "Ruhe durch — verstehst du, was du damit sagen willst?\n"
                "Schritt 2: Formuliere den Gedanken einmal in eigenen, "
                "einfacheren Worten.\n"
                "Schritt 3: Sprich die neue Version 5x laut, bevor du zur "
                "Originalformulierung zurückkehrst."
            ),
        ),
        ru.Achse(
            "konzentriert_statt_verteilt", "anteil", prioritaet=2,
            merkmal_key="_immer", merkmal_wert=True, min_evidenz=2, schwelle=0.0,
            befund_template="Deine Fehler häufen sich an {n} einzelnen, besonders schwierigen Sätzen, statt gleichmäßig verteilt zu sein.",
            ursache_template="Das ist eigentlich ein gutes Zeichen — du musst nicht überall üben.",
            uebung_template=(
                "\nSchritt 1: Liste dir genau diese wenigen Sätze auf.\n"
                "Schritt 2: Übe nur diese Sätze, je 5x, statt den ganzen "
                "Text zu wiederholen.\n"
                "Schritt 3: Baue sie zurück in den Gesamttext ein und "
                "sprich alles am Stück durch."
            ),
        ),
    ]
    for b in d3_befunde:
        b["_immer"] = True
        b["stark_betroffen"] = b["anzahl"] >= 3

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde, D3_ACHSEN, max_tipps=1,
        fall_a_text=["Keine Sätze mit mehreren gleichzeitigen Fehlern gefunden."],
        einzelfund_template=(
            "Bei '{satz}' sind dir {anzahl} Fehler in einem einzigen Satz "
            "passiert. Das ist meist ein Zeichen, dass genau dieser Gedanke "
            "inhaltlich noch nicht ganz sicher sitzt.\n"
            "Schritt 1: Lies dir den Inhalt des Satzes nochmal durch.\n"
            "Schritt 2: Sprich ihn 3x hintereinander laut.\n"
            "Schritt 3: Baue ihn zurück in den Kontext ein und sprich den "
            "ganzen Abschnitt durch."
        ),
        fall_c_einleitung="Mehrere Sätze mit gehäuften Fehlern, ohne dass sich ein gemeinsamer Grund erkennen lässt:",
    )

    z += ru.dimension_block_detail(
        "Gehäufte Fehler im Satz", 30, d3_score,
        was_gemessen=["Anteil der betroffenen Sätze, die mehr als einen Sprechfehler",
                      "gleichzeitig haben — ein Zeichen für eine besonders unsichere",
                      "Textstelle."],
        warum=[f"{d3_anteil:.0%} der betroffenen Sätze haben gehäufte Fehler.",
               f"Bei dir: {d3_text}"],
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Stocker-Info ──────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  4. KURZE PAUSEN / STOCKEN (informativ)")
    z.append(ru.SEP2)
    z.append("  Diese Werte kommen aus dem Pausen-Modul und werden dort bewertet —")
    z.append("  hier nur zur Einordnung:")
    z.append("")
    if stocker_info["verfuegbar"]:
        z.append(f"  Stocker-Anzahl: {stocker_info['stocker_anzahl']}")
        z.append(f"  Stocker-Rate:   {stocker_info['stocker_rate']:.2f}/Min")
        z.append("  (Bewertet und erklärt im Pausen-Report.)")
    else:
        z.append("  Kein Pausen-Report verfügbar — Stocker-Daten nicht geladen.")
    z.append("")

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Sprechfehler sind Unterbrechungen im Redefluss: Wiederholungen")
    z.append("  (dasselbe Wort mehrfach) und Abbrüche (ein Satz wird nicht zu Ende")
    z.append("  gesprochen). Beide stören den Zuhörer und signalisieren Unsicherheit.")
    z.append("  Optimum: 0 Ereignisse/Min.")
    z.append("  Quellen: Bosker et al. (2013) · Clark & Fox Tree (2002) · Laserna (2014)")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    return "\n".join(z)



# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def analyse_sprechfluss(
    transkript_pfad: Path,
    inhalt_pfad: Optional[Path] = None,
    pausen_pfad: Optional[Path] = None,
    output_json_pfad: Optional[Path] = None,
    output_txt_kurz_pfad: Optional[Path] = None,
    output_txt_detail_pfad: Optional[Path] = None,
) -> Dict[str, Any]:

    print(f"[sprechfluss] Starte Analyse: {transkript_pfad.name}")

    # 1. Daten laden
    woerter = parse_transkript(transkript_pfad)
    if not woerter:
        raise ValueError("Keine Wörter im Transkript.")
    print(f"[sprechfluss] {len(woerter)} Wörter geladen.")

    inhalt_data = lade_json(inhalt_pfad) if inhalt_pfad else None
    pausen_data = lade_json(pausen_pfad) if pausen_pfad else None

    # 2. Sätze extrahieren
    saetze = extrahiere_saetze(woerter, inhalt_data)
    markiere_kernbotschaften(saetze, inhalt_data)
    print(f"[sprechfluss] {len(saetze)} Sätze, {sum(1 for s in saetze if s.ist_kernbotschaft)} Kernbotschaften.")

    # 3. Disfluenzen erkennen
    disfluenzen = erkenne_disfluenzen(woerter, saetze)
    wiederholungen = [d for d in disfluenzen if d.typ == "wiederholung"]
    abbrueche = [d for d in disfluenzen if d.typ == "abbruch"]
    print(f"[sprechfluss] {len(disfluenzen)} Disfluenzen: {len(wiederholungen)} Wiederholungen, {len(abbrueche)} Abbrüche.")

    # 4. Dauer & Scoring
    gesamt_dauer_ms = woerter[-1].end_ms - woerter[0].start_ms
    dauer_min = gesamt_dauer_ms / 60000.0
    if dauer_min <= 0:
        dauer_min = 1.0

    d1_score, d1_rate, d1_text = berechne_d1(disfluenzen, dauer_min)
    d2_score, d2_anteil, d2_text = berechne_d2(saetze, disfluenzen)
    d3_score, d3_anteil, d3_text = berechne_d3(saetze, disfluenzen)
    gesamt_score = berechne_gesamtscore(d1_score, d2_score, d3_score)

    print(f"[sprechfluss] Scoring: D1={d1_score}, D2={d2_score}, D3={d3_score}, Gesamt={gesamt_score}")

    # 5. Stocker-Info (nur informativ)
    stocker_info = lade_stocker_info(pausen_data)

    # 6. Output
    output_data = {
        "modul": "sprechfluss_analyse",
        "version": "2.0",
        "timestamp": datetime.now().isoformat(),
        "input": str(transkript_pfad),
        "meta": {
            "woerter_gesamt": len(woerter),
            "saetze_gesamt": len(saetze),
            "praesentationsdauer_min": round(dauer_min, 3),
        },
        "disfluenzen": [d.to_dict() for d in disfluenzen],
        "statistiken": {
            "anzahl_disfluenzen": len(disfluenzen),
            "anzahl_wiederholungen": len(wiederholungen),
            "anzahl_abbrueche": len(abbrueche),
            "disfluenz_rate_pro_min": round(d1_rate, 2),
        },
        "stocker_info": {
            "aus_pausen_analyse": stocker_info["verfuegbar"],
            "stocker_anzahl": stocker_info["stocker_anzahl"],
            "stocker_rate": stocker_info["stocker_rate"],
            "hinweis": "Stocker werden ausschließlich in pausen_analyse.py gescored (Fix v2)."
        },
        "scoring": {
            "d1_wiederholung_abbruch_rate": {
                "gewichtung": GEWICHT_D1,
                "punkte": d1_score,
                "bewertung": d1_text,
                "rate_pro_min": round(d1_rate, 2)
            },
            "d2_kernbotschafts_fluenz": {
                "gewichtung": GEWICHT_D2,
                "punkte": d2_score,
                "bewertung": d2_text,
                "anteil_sauber": round(d2_anteil, 4)
            },
            "d3_disfluenz_cluster": {
                "gewichtung": GEWICHT_D3,
                "punkte": d3_score,
                "bewertung": d3_text,
                "cluster_anteil": round(d3_anteil, 4)
            },
            "gesamtscore": gesamt_score
        }
    }

    if output_json_pfad:
        output_json_pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_pfad, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"[sprechfluss] JSON gespeichert: {output_json_pfad}")

    if output_txt_kurz_pfad:
        output_txt_kurz_pfad.parent.mkdir(parents=True, exist_ok=True)
        kurz_report = generiere_kurz_report(
            disfluenzen,
            d1_score, d1_rate, d1_text,
            d2_score, d2_anteil, d2_text,
            d3_score, d3_anteil, d3_text,
            gesamt_score, dauer_min,
            transkript_pfad.name,
            stocker_info
        )
        with open(output_txt_kurz_pfad, "w", encoding="utf-8") as f:
            f.write(kurz_report)
        print(f"[sprechfluss] Kurz-Report gespeichert: {output_txt_kurz_pfad}")

    if output_txt_detail_pfad:
        output_txt_detail_pfad.parent.mkdir(parents=True, exist_ok=True)
        detail_report = generiere_detail_report(
            disfluenzen, saetze,
            d1_score, d1_rate, d1_text,
            d2_score, d2_anteil, d2_text,
            d3_score, d3_anteil, d3_text,
            gesamt_score, dauer_min,
            transkript_pfad.name,
            stocker_info
        )
        with open(output_txt_detail_pfad, "w", encoding="utf-8") as f:
            f.write(detail_report)
        print(f"[sprechfluss] Detail-Report gespeichert: {output_txt_detail_pfad}")

    print(f"[sprechfluss] Fertig. Gesamt-Score: {gesamt_score}/100")
    return output_data


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sprechfluss-Analyse für Präsentationsbewertungs-AI")
    parser.add_argument("transkript", type=str, help="Pfad zum Transkript")
    parser.add_argument("--inhalt", type=str, default=None, help="Pfad zu inhalt_analyse_output.json")
    parser.add_argument("--pausen", type=str, default=None, help="Pfad zu pausen_analyse_output.json (informativ)")
    parser.add_argument("--output-json", type=str, default="zwischen_output/sprechfluss_analyse_output.json")
    parser.add_argument("--output-txt-kurz", type=str, default=None)
    parser.add_argument("--output-txt-detail", type=str, default=None)

    args = parser.parse_args()

    transkript = Path(args.transkript)
    inhalt = Path(args.inhalt) if args.inhalt else None
    pausen = Path(args.pausen) if args.pausen else None
    out_json = Path(args.output_json)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_txt_kurz = Path(args.output_txt_kurz) if args.output_txt_kurz else Path("reports/sprechfluss") / f"sprechfluss_kurz_{ts}.txt"
    out_txt_detail = Path(args.output_txt_detail) if args.output_txt_detail else Path("reports/sprechfluss") / f"sprechfluss_detail_{ts}.txt"

    if not transkript.exists():
        print(f"[FEHLER] Transkript nicht gefunden: {transkript}")
        exit(1)

    try:
        analyse_sprechfluss(transkript, inhalt, pausen, out_json, out_txt_kurz, out_txt_detail)
    except Exception as e:
        print(f"[FEHLER] {e}")
        raise
