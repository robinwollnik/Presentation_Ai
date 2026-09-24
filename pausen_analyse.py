#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pausen_analyse.py
=================
Erkennt Pausen aus Zeitstempel-Lücken zwischen Wörtern.
Klassifiziert in 8 Kategorien mit kontextueller Bewertung.
Einzige Quelle für Stocker-Erkennung (Fix v2: keine Doppelzählung)."""

import json
import os
import re
import math
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

import report_utils as ru


# =============================================================================
# KONSTANTEN — studien-basiert, siehe Dokument Abschnitt 6.2
# =============================================================================

# Schwellenwerte in Millisekunden
MS_IGNORIEREN = 150              # < 150 ms: unter Wahrnehmungsschwelle
MS_SEGMENT_GRENZE = 10000        # > 10 s: Applaus, Cuts, Fragen — ausschließen
MS_KLEINER_STOCKER = 300         # 150--300 ms: kurzer Stocker
MS_STOCKER = 800                 # 300--800 ms: Stocker
MS_STOCKER_LANG = 2000           # 800--2000 ms: langer Stocker innerhalb Satz
MS_ZU_LANG = 4000                # > 4000 ms: zu lang
MS_ATEM_MIN = 500                # 500 ms
MS_ATEM_MAX = 1500               # 1500 ms
MS_RHETORISCH = 800              # ≥ 800 ms gilt als rhetorisch relevant
MS_WIRKUNG_MIN = 2000            # 2000 ms
MS_WIRKUNG_MAX = 4000            # 4000 ms
MS_SINNPAUSE_MAX = 2000          # 2000 ms (Obergrenze Sinnpause)

SATZ_LAENGE_LANG = 15            # > 15 Wörter = langer Satz (Atempause-Trigger)

# Scoring-Gewichtung 40/30/30
GEWICHT_D1 = 0.40
GEWICHT_D2 = 0.30
GEWICHT_D3 = 0.30

# Pfade (relativ zum Projekt-Root)
DEFAULT_INPUT_DIR = Path("zwischen_output")
DEFAULT_OUTPUT_DIR = Path("zwischen_output")
DEFAULT_REPORT_DIR = Path("reports/pausen")


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class Wort:
    """Ein Wort mit Start-/End-Zeitstempel aus dem Transkript."""
    text: str
    start_ms: float
    end_ms: float
    index: int = 0

    @property
    def dauer_ms(self) -> float:
        return self.end_ms - self.start_ms


@dataclass
class Satz:
    """Ein Satz, abgeleitet aus inhalt_analyse_output.json."""
    index: int
    text: str
    start_ms: float
    end_ms: float
    woerter: List[Wort] = field(default_factory=list)
    wortanzahl: int = 0
    ist_kernbotschaft: bool = False
    ist_struktur_uebergang: bool = False
    ist_lang: bool = False  # > 15 Wörter


@dataclass
class Pause:
    """Eine erkannte Pause zwischen zwei Wörtern."""
    start_ms: float          # Endzeit des vorherigen Worts
    end_ms: float            # Startzeit des nächsten Worts
    dauer_ms: float
    typ: str                 # Klassifikation

    # Kontext-Informationen
    vorheriges_wort: str = ""
    naechstes_wort: str = ""
    innerhalb_satz: bool = False
    nach_langem_satz: bool = False
    an_struktur_uebergang: bool = False
    vor_kernbotschaft: bool = False
    nach_kernbotschaft: bool = False
    satz_index: int = -1

    @property
    def ist_stocker(self) -> bool:
        return self.typ in ("kleiner_stocker", "stocker", "stocker_lang", "zu_lang")

    @property
    def ist_rhetorisch(self) -> bool:
        return self.typ in ("sinnpause", "wirkungspause", "strukturpause")

    @property
    def ist_zaehlbar(self) -> bool:
        """Wird in D3 (Pausen/Min) gezählt."""
        return self.typ not in ("ignorieren", "segment_grenze")

    def to_dict(self) -> dict:
        return {
            "start_ms": round(self.start_ms, 3),
            "end_ms": round(self.end_ms, 3),
            "dauer_ms": round(self.dauer_ms, 3),
            "typ": self.typ,
            "kontext": {
                "vorheriges_wort": self.vorheriges_wort,
                "naechstes_wort": self.naechstes_wort,
                "innerhalb_satz": self.innerhalb_satz,
                "nach_langem_satz": self.nach_langem_satz,
                "an_struktur_uebergang": self.an_struktur_uebergang,
                "vor_kernbotschaft": self.vor_kernbotschaft,
                "nach_kernbotschaft": self.nach_kernbotschaft,
            }
        }


@dataclass
class KernbotschaftCheck:
    """Ergebnis des Kernbotschaft-Pausen-Checks (Abschnitt 6.5)."""
    kernbotschaft_text: str
    start_ms: float
    end_ms: float
    pause_davor: Optional[Pause] = None
    pause_danach: Optional[Pause] = None
    hat_rhetorische_pause: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.kernbotschaft_text,
            "start_ms": round(self.start_ms, 3),
            "end_ms": round(self.end_ms, 3),
            "hat_rhetorische_pause": self.hat_rhetorische_pause,
            "pause_davor_typ": self.pause_davor.typ if self.pause_davor else None,
            "pause_danach_typ": self.pause_danach.typ if self.pause_danach else None,
        }


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def _diagnose_stocker(b: dict) -> str:
    """
    Bestimmt PRO EINZELNER Fundstelle die wahrscheinlichste Ursache — priorisiert,
    nur die zutreffendste wird gezeigt. Gibt einen fertigen Text mit Diagnose-Satz
    UND 3 konkreten Übungsschritten zurück (kleinster Baustein -> zusammensetzen
    -> kontrollieren).
    """
    if b.get("kernaussage_nahe"):
        return (
            "das liegt direkt an deiner wichtigsten Aussage — dort steigt die "
            "Nervosität am meisten, weil du weißt, dass diese Stelle zählt.\n"
            "Schritt 1: Schreibe dir nur diesen einen Satz auf einen Zettel.\n"
            "Schritt 2: Sprich ihn 8x laut hintereinander, jedes Mal etwas ruhiger.\n"
            "Schritt 3: Baue den Satz davor und danach wieder ein und sprich den "
            "ganzen Übergang 3x am Stück."
        )
    if b.get("struktur_uebergang"):
        return (
            "das liegt an einem Übergang zwischen zwei Abschnitten deiner "
            "Präsentation — der erste Satz nach einer Zäsur sitzt oft noch "
            "nicht auswendig.\n"
            "Schritt 1: Sprich den letzten Satz vor dem Übergang und den ersten "
            "danach direkt hintereinander, 5x.\n"
            "Schritt 2: Füge eine bewusste kurze Pause zwischen beiden ein und "
            "wiederhole 3x.\n"
            "Schritt 3: Sprich den ganzen Übergang inklusive einem Satz davor "
            "und danach 3x im Kontext."
        )
    if b.get("wort_lang"):
        return (
            f"das Wort '{b['naechstes_wort']}' hat mehrere Silben und ist "
            "dadurch schwerer flüssig auszusprechen.\n"
            f"Schritt 1: Sprich '{b['naechstes_wort']}' einzeln und langsam, 5x "
            "hintereinander.\n"
            f"Schritt 2: Sprich das Wort im normalen Sprechtempo, nochmal 5x.\n"
            "Schritt 3: Baue es wieder in den ganzen Satz ein und sprich ihn 3x."
        )
    if b.get("satz_position") == "anfang":
        return (
            "die Stelle liegt am Satzanfang — ein neuer Gedanke beginnt, und "
            "der Einstieg sitzt noch nicht sicher.\n"
            "Schritt 1: Sprich nur die ersten 2-3 Wörter des Satzes, 5x laut.\n"
            "Schritt 2: Sprich den ganzen Satz 3x am Stück.\n"
            "Schritt 3: Sprich den Satz davor und diesen Satz direkt "
            "hintereinander, um den Übergang zu üben."
        )
    return (
        "hier lässt sich keine eindeutige Ursache erkennen — das ist normale, "
        "gelegentliche Wortfindung, kein systematisches Problem.\n"
        "Schritt 1: Sprich den ganzen Satz 3x laut am Stück.\n"
        "Schritt 2: Nimm dich auf und höre, ob die Stelle beim dritten Mal "
        "flüssiger kommt.\n"
        "Schritt 3: Wiederhole an einem anderen Tag, falls die Stelle noch "
        "nicht sitzt — manche Stellen brauchen mehr als eine Übungssitzung."
    )


def zeitstr_to_ms(zeit_str: str) -> float:
    """
    Parst "HH:MM:SS.mmm" oder "MM:SS.mmm" zu Millisekunden.
    Robust gegen führende Nullen und verschiedene Formate.
    """
    zeit_str = zeit_str.strip()

    # Versuche HH:MM:SS.mmm
    if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}$", zeit_str):
        h, m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

    # Versuche MM:SS.mmm
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", zeit_str):
        m, s_ms = zeit_str.split(":")
        s, ms = s_ms.split(".")
        return int(m) * 60000 + int(s) * 1000 + int(ms)

    # Fallback: Versuche mit datetime
    for fmt in ("%H:%M:%S.%f", "%M:%S.%f", "%H:%M:%S", "%M:%S"):
        try:
            dt = datetime.strptime(zeit_str, fmt)
            return (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000 + dt.microsecond // 1000
        except ValueError:
            continue

    raise ValueError(f"Unbekanntes Zeitformat: {zeit_str}")


def ms_to_zeitstr(ms: float) -> str:
    """Millisekunden zu "MM:SS.mmm" oder "HH:MM:SS.mmm"."""
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
    """
    Parst Transkript im Format:
        Wort HH:MM:SS.mmm HH:MM:SS.mmm

    Returns:
        Liste von Wort-Objekten, sortiert nach Startzeit.
    """
    woerter = []
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)$")

    with open(transkript_pfad, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            m = pattern.match(line)
            if not m:
                # Versuche es mit variablen Leerzeichen/Tabs
                teile = line.split()
                if len(teile) >= 3:
                    wort_text = teile[0]
                    start_str = teile[-2]
                    end_str = teile[-1]
                else:
                    print(f"[WARN] Zeile {i} übersprungen (Format): {line[:60]}")
                    continue
            else:
                wort_text, start_str, end_str = m.groups()

            try:
                start_ms = zeitstr_to_ms(start_str)
                end_ms = zeitstr_to_ms(end_str)
            except ValueError as e:
                print(f"[WARN] Zeile {i} übersprungen (Zeit): {e}")
                continue

            woerter.append(Wort(
                text=wort_text,
                start_ms=start_ms,
                end_ms=end_ms,
                index=len(woerter)
            ))

    # Sicherstellen, dass sortiert ist
    woerter.sort(key=lambda w: w.start_ms)
    for i, w in enumerate(woerter):
        w.index = i

    return woerter


def lade_inhalt_analyse(pfad: Path) -> Optional[Dict]:
    """Lädt inhalt_analyse_output.json, falls vorhanden."""
    if not pfad.exists():
        return None
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[WARN] Konnte inhalt_analyse nicht laden: {e}")
        return None


def extrahiere_saetze(
    woerter: List[Wort],
    inhalt_data: Optional[Dict]
) -> List[Satz]:
    """
    Extrahiert Sätze aus inhalt_analyse_output.json.
    Fallback: Wenn keine Inhaltsanalyse vorhanden, heuristisch nach Satzzeichen.
    """
    saetze = []

    if inhalt_data and "satzgrenzen" in inhalt_data:
        # Erwarte Format: [{"index": 0, "text": "...", "start_ms": 123, "end_ms": 456}, ...]
        # oder mit Zeitstempel-Strings
        raw_saetze = inhalt_data["satzgrenzen"]

        for i, raw in enumerate(raw_saetze):
            start_ms = raw.get("start_ms")
            end_ms = raw.get("end_ms")

            # Fallback: Konvertiere Strings
            if isinstance(start_ms, str):
                start_ms = zeitstr_to_ms(start_ms)
            if isinstance(end_ms, str):
                end_ms = zeitstr_to_ms(end_ms)

            if start_ms is None or end_ms is None:
                continue

            # Wörter diesem Satz zuordnen
            satz_woerter = [w for w in woerter if start_ms <= w.start_ms < end_ms]

            saetze.append(Satz(
                index=i,
                text=raw.get("text", ""),
                start_ms=float(start_ms),
                end_ms=float(end_ms),
                woerter=satz_woerter,
                wortanzahl=len(satz_woerter),
                ist_lang=len(satz_woerter) > SATZ_LAENGE_LANG
            ))

    if not saetze:
        # Fallback: Heuristisch nach .!? aufteilen
        print("[INFO] Keine Satzgrenzen aus Inhaltsanalyse — verwende Heuristik.")
        satz_endzeichen = re.compile(r"[.!?]+$")
        aktuelle_woerter = []
        satz_idx = 0

        for w in woerter:
            aktuelle_woerter.append(w)
            if satz_endzeichen.search(w.text) or w.text.endswith((".", "!", "?")):
                start_ms = aktuelle_woerter[0].start_ms
                end_ms = aktuelle_woerter[-1].end_ms
                saetze.append(Satz(
                    index=satz_idx,
                    text=" ".join(x.text for x in aktuelle_woerter),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    woerter=list(aktuelle_woerter),
                    wortanzahl=len(aktuelle_woerter),
                    ist_lang=len(aktuelle_woerter) > SATZ_LAENGE_LANG
                ))
                aktuelle_woerter = []
                satz_idx += 1

        # Restwörter als letzten Satz
        if aktuelle_woerter:
            saetze.append(Satz(
                index=satz_idx,
                text=" ".join(x.text for x in aktuelle_woerter),
                start_ms=aktuelle_woerter[0].start_ms,
                end_ms=aktuelle_woerter[-1].end_ms,
                woerter=aktuelle_woerter,
                wortanzahl=len(aktuelle_woerter),
                ist_lang=len(aktuelle_woerter) > SATZ_LAENGE_LANG
            ))

    return saetze


def markiere_kernbotschaften(
    saetze: List[Satz],
    inhalt_data: Optional[Dict]
) -> None:
    """Markiert Sätze als Kernbotschaften, falls im Inhalts-Output vorhanden."""
    if not inhalt_data or "kernbotschaften" not in inhalt_data:
        return

    kernbotschaften = inhalt_data["kernbotschaften"]

    for kb in kernbotschaften:
        kb_start = kb.get("start_ms")
        kb_end = kb.get("end_ms")

        if isinstance(kb_start, str):
            kb_start = zeitstr_to_ms(kb_start)
        if isinstance(kb_end, str):
            kb_end = zeitstr_to_ms(kb_end)

        if kb_start is None or kb_end is None:
            continue

        for s in saetze:
            # Überschneidung prüfen
            if s.start_ms <= float(kb_end) and s.end_ms >= float(kb_start):
                s.ist_kernbotschaft = True


def markiere_struktur_uebergaenge(
    saetze: List[Satz],
    inhalt_data: Optional[Dict]
) -> None:
    """Markiert Sätze an Struktur-Übergängen (Einleitung→Hauptteil, Hauptteil→Schluss)."""
    if not inhalt_data or "struktur" not in inhalt_data:
        return

    struktur = inhalt_data["struktur"]

    # Erwarte Format: {"einleitung": {"start_ms": 0, "end_ms": 12000}, "hauptteil": {...}, "schluss": {...}}
    segmente = []
    for key in ["einleitung", "hauptteil", "schluss"]:
        if key in struktur:
            seg = struktur[key]
            s_ms = seg.get("start_ms", seg.get("start"))
            e_ms = seg.get("end_ms", seg.get("end"))

            if isinstance(s_ms, str):
                s_ms = zeitstr_to_ms(s_ms)
            if isinstance(e_ms, str):
                e_ms = zeitstr_to_ms(e_ms)

            if s_ms is not None and e_ms is not None:
                segmente.append((float(s_ms), float(e_ms), key))

    # Markiere den ersten Satz nach jedem Segment-Wechsel
    segmente.sort()
    for i in range(1, len(segmente)):
        uebergang_ms = segmente[i][0]  # Start des neuen Segments
        for s in saetze:
            # Satz liegt direkt am Übergang oder kurz danach
            if abs(s.start_ms - uebergang_ms) < 500 or (s.start_ms >= uebergang_ms and s.start_ms < uebergang_ms + 1000):
                s.ist_struktur_uebergang = True


# =============================================================================
# KERN-LOGIK: PAUSEN-ERKENNUNG & KLASSIFIKATION
# =============================================================================

def finde_pausen(
    woerter: List[Wort],
    saetze: List[Satz]
) -> List[Pause]:
    """
    Findet alle Pausen zwischen aufeinanderfolgenden Wörtern.
    Berechnet die Lücke: start_ms(Wort_N+1) - end_ms(Wort_N).
    """
    pausen = []

    # Schneller Lookup: Welcher Satz enthält welches Wort?
    wort_zu_satz = {}
    for s in saetze:
        for w in s.woerter:
            wort_zu_satz[w.index] = s

    for i in range(len(woerter) - 1):
        w1 = woerter[i]
        w2 = woerter[i + 1]

        luecke = w2.start_ms - w1.end_ms

        # Negative Lücken (Überlappungen) ignorieren wir als Artefakt
        if luecke < 0:
            continue

        s1 = wort_zu_satz.get(w1.index)
        s2 = wort_zu_satz.get(w2.index)

        innerhalb_satz = (s1 is not None and s2 is not None and s1.index == s2.index)
        nach_langem_satz = (s1 is not None and s1.ist_lang) if not innerhalb_satz else False
        an_struktur = (s2 is not None and s2.ist_struktur_uebergang) if s2 else False
        vor_kb = (s2 is not None and s2.ist_kernbotschaft) if s2 else False
        nach_kb = (s1 is not None and s1.ist_kernbotschaft) if s1 else False

        pause = Pause(
            start_ms=w1.end_ms,
            end_ms=w2.start_ms,
            dauer_ms=luecke,
            typ="unklassifiziert",
            vorheriges_wort=w1.text,
            naechstes_wort=w2.text,
            innerhalb_satz=innerhalb_satz,
            nach_langem_satz=nach_langem_satz,
            an_struktur_uebergang=an_struktur,
            vor_kernbotschaft=vor_kb,
            nach_kernbotschaft=nach_kb,
            satz_index=s1.index if s1 else -1
        )

        pausen.append(pause)

    return pausen


def klassifiziere_pause(p: Pause) -> str:
    """
    Klassifiziert eine Pause nach der Prioritäts-Matrix aus Abschnitt 6.3.
    ERSTE PASSENDE REGEL GEWINNT.

    Reihenfolge (exakt wie im Dokument):
    1. < 150 ms → ignorieren
    2. > 10 s → segment_grenze
    3. innerhalb Satz + 150--300 ms → kleiner_stocker
    4. innerhalb Satz + 300--800 ms → stocker
    5. innerhalb Satz + 800--2000 ms → stocker_lang
    6. innerhalb Satz + > 2000 ms → zu_lang
    7. nach langem Satz (>15 W.) + 500--1500 ms → atempause (VORRANG)
    8. an Struktur-Übergang + ≥ 800 ms → strukturpause
    9. vor/nach Kernbotschaft + 800--2000 ms → sinnpause
    10. vor/nach Kernbotschaft + 2000--4000 ms → wirkungspause
    11. zwischen Sätzen + 150--300 ms → natürlich_kurz
    12. zwischen Sätzen + 300--2000 ms → natürlich
    13. zwischen Sätzen + > 4000 ms → zu_lang
    14. Fallback → natürlich (oder stocker wenn innerhalb)
    """
    d = p.dauer_ms

    # 1. Unter Wahrnehmungsschwelle
    if d < MS_IGNORIEREN:
        return "ignorieren"

    # 2. Segment-Grenze (Applaus, Cut, Frage)
    if d > MS_SEGMENT_GRENZE:
        return "segment_grenze"

    # === INNERHALB SATZ ===
    if p.innerhalb_satz:
        if MS_IGNORIEREN <= d < MS_KLEINER_STOCKER:
            return "kleiner_stocker"
        if MS_KLEINER_STOCKER <= d < MS_STOCKER:
            return "stocker"
        if MS_STOCKER <= d < MS_STOCKER_LANG:
            return "stocker_lang"
        if d >= MS_STOCKER_LANG:
            return "zu_lang"
        return "stocker"  # Fallback

    # === ZWISCHEN SÄTZEN ===
    # 7. Atempause hat VORRANG nach langem Satz
    if p.nach_langem_satz and MS_ATEM_MIN <= d <= MS_ATEM_MAX:
        return "atempause"

    # 8. Struktur-Übergang
    if p.an_struktur_uebergang and d >= MS_RHETORISCH:
        return "strukturpause"

    # 9. Sinnpause vor/nach Kernbotschaft
    if (p.vor_kernbotschaft or p.nach_kernbotschaft) and MS_RHETORISCH <= d <= MS_SINNPAUSE_MAX:
        return "sinnpause"

    # 10. Wirkungspause vor/nach Kernbotschaft
    if (p.vor_kernbotschaft or p.nach_kernbotschaft) and MS_WIRKUNG_MIN <= d <= MS_WIRKUNG_MAX:
        return "wirkungspause"

    # 11. Natürlich kurz
    if MS_IGNORIEREN <= d < MS_KLEINER_STOCKER:
        return "natuerlich_kurz"

    # 12. Natürlich
    if MS_KLEINER_STOCKER <= d <= MS_STOCKER_LANG:
        return "natuerlich"

    # 13. Zu lang zwischen Sätzen
    if d > MS_ZU_LANG:
        return "zu_lang"

    # Fallback
    return "natuerlich"


def klassifiziere_alle_pausen(pausen: List[Pause]) -> None:
    """Wendet die Klassifikation auf alle Pausen an."""
    for p in pausen:
        p.typ = klassifiziere_pause(p)


# =============================================================================
# KERNBOTSCHAFT-CHECK (Abschnitt 6.5)
# =============================================================================

def pruefe_kernbotschaften(
    saetze: List[Satz],
    pausen: List[Pause]
) -> List[KernbotschaftCheck]:
    """
    Prüft für jede Kernbotschaft: existiert eine rhetorische Pause (≥ 800 ms)
    davor oder danach?
    """
    ergebnisse = []

    for s in saetze:
        if not s.ist_kernbotschaft:
            continue

        # Suche Pause direkt davor (endet bei Satz-Start)
        pause_davor = None
        pause_danach = None

        # Finde Pause, die direkt vor diesem Satz endet
        for p in pausen:
            if abs(p.end_ms - s.start_ms) < 50:  # 50 ms Toleranz
                pause_davor = p
                break

        # Finde Pause, die direkt nach diesem Satz beginnt
        for p in pausen:
            if abs(p.start_ms - s.end_ms) < 50:
                pause_danach = p
                break

        # Alternative: Suche in einem kleinen Fenster
        if pause_davor is None:
            kandidaten = [p for p in pausen if p.end_ms <= s.start_ms and s.start_ms - p.end_ms < 2000]
            if kandidaten:
                pause_davor = max(kandidaten, key=lambda p: p.dauer_ms)

        if pause_danach is None:
            kandidaten = [p for p in pausen if p.start_ms >= s.end_ms and p.start_ms - s.end_ms < 2000]
            if kandidaten:
                pause_danach = min(kandidaten, key=lambda p: p.start_ms - s.end_ms)

        hat_rhetorisch = False
        if pause_davor and pause_davor.dauer_ms >= MS_RHETORISCH:
            hat_rhetorisch = True
        if pause_danach and pause_danach.dauer_ms >= MS_RHETORISCH:
            hat_rhetorisch = True

        ergebnisse.append(KernbotschaftCheck(
            kernbotschaft_text=s.text[:80] + "..." if len(s.text) > 80 else s.text,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            pause_davor=pause_davor,
            pause_danach=pause_danach,
            hat_rhetorische_pause=hat_rhetorisch
        ))

    return ergebnisse


# =============================================================================
# SCORING (Abschnitt 6.4)
# =============================================================================

def berechne_d1_rhetorische_qualitaet(pausen: List[Pause]) -> Tuple[int, float, str]:
    """
    D1: Rhetorische Qualität (40%)
    Ratio = Rhetorische Pausen / (Rhetorische Pausen + Stocker)
    """
    rhetorische = [p for p in pausen if p.ist_rhetorisch]
    stocker = [p for p in pausen if p.ist_stocker]

    anzahl_rhetorisch = len(rhetorische)
    anzahl_stocker = len(stocker)

    gesamt = anzahl_rhetorisch + anzahl_stocker

    if gesamt == 0:
        ratio = 0.0
    else:
        ratio = anzahl_rhetorisch / gesamt

    if ratio >= 0.60:
        punkte = 100
        bewertung = "Exzellent"
    elif ratio >= 0.40:
        punkte = 85
        bewertung = "Gut"
    elif ratio >= 0.20:
        punkte = 65
        bewertung = "Ausbaufähig"
    elif ratio >= 0.05:
        punkte = 40
        bewertung = "Wenig"
    else:
        punkte = 20
        bewertung = "Kaum rhetorisch"

    return punkte, ratio, bewertung


def berechne_d2_stocker_rate(pausen: List[Pause], dauer_min: float) -> Tuple[int, float, str]:
    """
    D2: Stocker-Rate (30%)
    Stocker pro Minute.
    """
    anzahl_stocker = len([p for p in pausen if p.ist_stocker])

    if dauer_min > 0:
        rate = anzahl_stocker / dauer_min
    else:
        rate = 0.0

    if rate < 1.0:
        punkte = 100
        bewertung = "Sehr flüssig"
    elif rate < 3.0:
        punkte = 85
        bewertung = "Flüssig"
    elif rate < 5.0:
        punkte = 65
        bewertung = "Akzeptabel"
    elif rate < 8.0:
        punkte = 40
        bewertung = "Hörbar"
    else:
        punkte = 20
        bewertung = "Störend"

    return punkte, rate, bewertung


def berechne_d3_pausen_haushalt(pausen: List[Pause], dauer_min: float) -> Tuple[int, float, str]:
    """
    D3: Gesamtpausen-Haushalt (30%)
    Pausen/Min (alle zählbaren, also ohne ignorieren und segment_grenze).
    Referenz: 8--15 Pausen/Min für gute Sprecher (O'Connell & Kowal + TED).
    """
    anzahl_zaehlbar = len([p for p in pausen if p.ist_zaehlbar])

    if dauer_min > 0:
        rate = anzahl_zaehlbar / dauer_min
    else:
        rate = 0.0

    if 8.0 <= rate <= 15.0:
        punkte = 100
        bewertung = "Optimal"
    elif 5.0 <= rate < 8.0 or 15.0 < rate <= 20.0:
        punkte = 75
        bewertung = "Etwas ungewöhnlich"
    elif rate < 5.0:
        punkte = 40
        bewertung = "Kaum Pausen (gehetzt)"
    else:  # > 20
        punkte = 40
        bewertung = "Zu viele (stockend)"

    return punkte, rate, bewertung


def berechne_gesamtscore(d1: int, d2: int, d3: int) -> int:
    """Gewichteter Gesamtscore 40/30/30, gerundet."""
    score = d1 * GEWICHT_D1 + d2 * GEWICHT_D2 + d3 * GEWICHT_D3
    return int(round(score))


# =============================================================================
# REPORT-GENERIERUNG
# =============================================================================


# =============================================================================
# REPORT-GENERIERUNG — KURZFASSUNG
# =============================================================================

def generiere_kurz_report(
    pausen: List[Pause],
    kb_checks: List[KernbotschaftCheck],
    d1_score: int, d1_ratio: float, d1_text: str,
    d2_score: int, d2_rate: float, d2_text: str,
    d3_score: int, d3_rate: float, d3_text: str,
    gesamt_score: int,
    dauer_min: float,
    transkript_name: str,
) -> str:
    """Kurzfassung: Score, 1 Satz je Dimension, Top-Empfehlungen. Zum Überfliegen."""

    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"
    z = ru.kurz_header("PAUSEN", transkript_name, dauer_str)

    if dauer_min * 60 < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({dauer_min*60:.0f} Sek.) — Details dazu in der")
        z.append("    ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine Pausen wirken gezielt gesetzt und unterstützen deine Aussagen.",
        "Deine Pausen sind ausbaufähig — teils wirken sie eher wie Stocken.",
        "Deine Pausen wirken eher wie Stocken als wie bewusste Betonung.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Bewusste Pausen", 40, d1_score, d1_text)
    z += ru.dimension_zeile_kurz("Ungewolltes Stocken", 30, d2_score, d2_text)
    z += ru.dimension_zeile_kurz("Gesamtzahl Pausen", 30, d3_score, d3_text)
    z.append("")

    # ── WAS DU KONKRET TUN KANNST ────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    z += _empfehlungen(gesamt_score, d2_rate)
    z.append("")
    z.append("  Alle Fundstellen im Original-Satz, Begründung je Punktzahl und")
    z.append("  Referenzwerte findest du im ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)

    return "\n".join(z)


def _empfehlungen(gesamt_score: int, d2_rate: float) -> List[str]:
    z = []
    if gesamt_score >= 75:
        z.append("  ✅ Stark — hier sind Tipps zum Verfeinern:")
        z.append("  1. Verlängere Wirkungspausen nach deinen wichtigsten Sätzen")
        z.append("     auf 2–3 Sekunden und halte dabei Blickkontakt.")
        z.append("  2. Nimm dich auf und höre gezielt auf Restunsicherheiten.")
    elif gesamt_score >= 50:
        z.append("  🟡 Ausbaufähig — diese Punkte bringen den größten Effekt:")
        if d2_rate >= 3:
            z.append("  1. ⚡ Stocker reduzieren: Übe deine Kernsätze, bis du sie")
            z.append("     ohne Zögern sprechen kannst.")
        else:
            z.append("  1. ⚡ Mehr bewusste Pausen: Markiere deine 3 wichtigsten")
            z.append("     Sätze und setze danach je 1 Sekunde Pause.")
        z.append("  2. 🎯 Pause vor der Kernaussage: 1–2 Sekunden Stille davor.")
    else:
        z.append("  ❌ Handlungsbedarf — starte mit diesen Schritten:")
        z.append("  1. 🔥 Übe die ersten 30 Sekunden deiner Rede auswendig —")
        z.append("     die meisten Stocker sitzen am Anfang.")
        z.append("  2. 🎯 Setze dir 1 bewusste Wirkungspause pro Minute als Ziel.")
    return z


# =============================================================================
# REPORT-GENERIERUNG — DETAILANSICHT
# =============================================================================

def generiere_detail_report(
    pausen: List[Pause],
    saetze: List["Satz"],
    kb_checks: List[KernbotschaftCheck],
    d1_score: int, d1_ratio: float, d1_text: str,
    d2_score: int, d2_rate: float, d2_text: str,
    d3_score: int, d3_rate: float, d3_text: str,
    gesamt_score: int,
    dauer_min: float,
    transkript_name: str,
) -> str:
    """Ausführliche Fassung: Formeln, Fundstellen im Original-Satz, Begründung
    je Punktzahl, Verbesserungsvorschlag je Fundstelle, Inhaltsverzeichnis."""

    dauer_s = dauer_min * 60
    dauer_str = f"{int(dauer_min)} Min {int((dauer_min % 1) * 60)} Sek"

    def satz_text(satz_index: int) -> str:
        if 0 <= satz_index < len(saetze):
            return saetze[satz_index].text
        return ""

    z = ru.detail_header("PAUSEN", transkript_name, dauer_str)
    z += ru.build_toc([
        "Gesamtergebnis",
        "Bewusste Pausen — Begründung & Fundstellen",
        "Ungewolltes Stocken — Begründung & Fundstellen",
        "Gesamtzahl Pausen — Begründung & Fundstellen",
        "Pausen nach Typ (alle)",
        "Kernbotschaft-Check",
        "Hintergrund & Referenzwerte",
    ])

    z += ru.gesamtergebnis_block(
        gesamt_score,
        "Deine Pausen wirken gezielt gesetzt und unterstützen deine Aussagen.",
        "Deine Pausen sind ausbaufähig — teils wirken sie eher wie Stocken.",
        "Deine Pausen wirken eher wie Stocken als wie bewusste Betonung.",
    )
    z += ru.kleine_stichprobe_warnung(dauer_s)

    # ── D1 — Bewusste Pausen ─────────────────────────────────────────────────
    rhet_pausen = [p for p in pausen if p.ist_rhetorisch]
    zaehlbare = [p for p in pausen if p.ist_zaehlbar]

    rhet_befunde = []
    for p in rhet_pausen:
        rhet_befunde.append({
            "vorheriges_wort": p.vorheriges_wort,
            "naechstes_wort": p.naechstes_wort,
            "zeit_str": ru.video_zeit(p.start_ms),
            "kernaussage_nahe": p.vor_kernbotschaft or p.nach_kernbotschaft,
            "start_ms": p.start_ms,
        })

    fundstellen_d1 = []
    for p in rhet_pausen[:5]:
        satz = ru.markiere_stelle_im_satz(satz_text(p.satz_index), p.naechstes_wort, "🎯")
        fundstellen_d1.append(ru.fundstelle_zeile(p.start_ms, satz))
    if not fundstellen_d1:
        fundstellen_d1 = ["  Keine deiner Pausen wurde als bewusst gesetzt erkannt —",
                           "  darum gibt es hier keine Stelle zum Nachschauen."]

    D1_ACHSEN = [
        ru.Achse(
            "bei_kernaussage", "praesenz", prioritaet=1,
            merkmal_key="kernaussage_nahe", merkmal_wert=True, min_evidenz=2, schwelle=0.0,
            befund_template="{n} deiner bewussten Pausen liegen direkt an einer Kernaussage.",
            ursache_template="Das ist genau die richtige Stelle dafür.",
            uebung_template="Nutze dieses Timing auch bei weiteren wichtigen Sätzen.",
        ),
    ]

    tipp_d1 = ru.erkenne_muster_v2(
        rhet_befunde, D1_ACHSEN, max_tipps=1,
        fall_a_text=[
            "Du hast noch keine bewusste Pause verwendet. Das ist der größte "
            "ungenutzte Hebel in deiner Präsentation — schon 1 gezielte Pause "
            "pro Minute wirkt professioneller.\n"
            "Schritt 1: Wähle deine wichtigste Aussage im Text aus.\n"
            "Schritt 2: Sprich sie laut, und zähle danach im Kopf bis 'eins', "
            "bevor du weitersprichst — wiederhole das 3x.\n"
            "Schritt 3: Nimm dich auf und höre nach: wirkt die Pause bewusst "
            "oder gehetzt? Passe die Länge bei Bedarf an.",
        ],
        einzelfund_template=(
            "Bei {zeit_str} hast du vor '{naechstes_wort}' bewusst pausiert — "
            "das betont die folgende Aussage gut.\n"
            "Schritt 1: Höre dir genau diese Stelle nochmal an und merke dir "
            "das Timing.\n"
            "Schritt 2: Suche 2 weitere wichtige Sätze in deinem Text.\n"
            "Schritt 3: Wende dasselbe Timing dort bewusst an und übe es 2-3x."
        ),
        fall_c_einleitung="Mehrere bewusste Pausen an unterschiedlichen Stellen — das wirkt positiv, nicht mechanisch einstudiert:",
    )

    z += ru.dimension_block_detail(
        "Bewusste Pausen", 40, d1_score,
        was_gemessen=[
            "Wie viele deiner Pausen bewusst zur Betonung gesetzt wirken —",
            "statt einfach nur eine Verzögerung zu sein.",
        ],
        warum=[
            f"Von den {len(zaehlbare)} Pausen, die überhaupt lang genug zum",
            f"Zählen waren, war {len(rhet_pausen)} bewusst gesetzt. Das sind {d1_ratio:.0%}.",
            "Faustregel: Ab 30% wirkt das gezielt gesetzt, unter 10% kaum genutzt.",
            f"Bei dir: {d1_text}.",
        ],
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 — Ungewolltes Stocken ─────────────────────────────────────────────
    stocker_pausen = [p for p in pausen if p.ist_stocker]
    fundstellen_d2 = []
    for p in stocker_pausen[:8]:
        satz = ru.markiere_stelle_im_satz(satz_text(p.satz_index), p.naechstes_wort, "⚠")
        einzeltipp = (
            f"Zwischen '{p.vorheriges_wort}' und '{p.naechstes_wort}' bist du ins "
            f"Stocken geraten. Entspann dich kurz vor dieser Stelle, und übe genau "
            f"diesen Satz noch ein paar Mal."
        )
        fundstellen_d2 += ru.fundstelle_mit_einzeltipp(
            p.start_ms, satz, einzeltipp, f"{p.dauer_ms:.0f} ms Stocken")
    if len(stocker_pausen) > 8:
        fundstellen_d2.append(f"  ... und {len(stocker_pausen) - 8} weitere Stellen im Video.")
    if not fundstellen_d2:
        fundstellen_d2 = ["  Keine Stocker erkannt — nichts zum Nachschauen nötig."]

    warum_d2 = [
        f"In den {dauer_s:.0f} Sekunden dieser Aufnahme bist du {len(stocker_pausen)}x",
        "unfreiwillig ins Stocken geraten. Damit man das mit längeren",
        f"Präsentationen vergleichen kann, wird das auf eine ganze Minute",
        f"hochgerechnet: {d2_rate:.1f} mal pro Minute.",
        "Faustregel: unter 3x/Min ist unauffällig, über 8x/Min stört spürbar.",
        f"Bei dir: {d2_text}.",
    ]
    if dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        warum_d2.append("")
        warum_d2.append("⚠ Diese Aufnahme ist sehr kurz — schon 1 einziger Stocker")
        warum_d2.append("  verändert den Minutenwert stark. Nimm die Zahl oben nur")
        warum_d2.append("  als groben Anhaltspunkt, nicht als exaktes Ergebnis.")

    # Fundstellen für Muster-Erkennung aufbereiten — alle 5 Achsen aus dem
    # Konzept: Position im Satz, Wort, Struktur-Übergang, Kernaussagen-Nähe,
    # Zeitpunkt (für Trend)
    stocker_befunde = []
    for p in stocker_pausen:
        position = "sonstwo"
        if 0 <= p.satz_index < len(saetze):
            s = saetze[p.satz_index]
            woerter_texte = [w.text for w in s.woerter] if s.woerter else s.text.split()
            try:
                idx = woerter_texte.index(p.naechstes_wort)
                position = "anfang" if idx <= 1 else "sonstwo"
            except ValueError:
                pass
        silben = ru.zaehle_silben(p.naechstes_wort)
        stocker_befunde.append({
            "wort": p.naechstes_wort,
            "vorheriges_wort": p.vorheriges_wort,
            "naechstes_wort": p.naechstes_wort,
            "zeit_str": ru.video_zeit(p.start_ms),
            "satz_position": position,
            "wort_lang": silben >= 3,
            "silben": silben,
            "struktur_uebergang": p.an_struktur_uebergang,
            "kernaussage_nahe": p.vor_kernbotschaft or p.nach_kernbotschaft,
            "start_ms": p.start_ms,
        })

    # Diagnose (Ursache + 3 Schritte) muss VOR dem Engine-Aufruf berechnet
    # werden, damit sie in Fall B (1 Fund) und Fall C (mehrere, kein Muster)
    # über das Einzelfund-Template zur Verfügung steht.
    for b in stocker_befunde:
        b["diagnose"] = _diagnose_stocker(b)

    PAUSEN_ACHSEN = [
        ru.Achse(
            "kernaussage_nahe", "praesenz", prioritaet=1,
            merkmal_key="kernaussage_nahe", merkmal_wert=True, min_evidenz=1,
            befund_template="Direkt vor oder nach einer Kernaussage findest du {n} Stocker.",
            ursache_template="Genau dort wirkt Stocken am stärksten, weil es die",
            uebung_template="wichtigste Stelle deiner Präsentation ist. Übe exakt diesen Übergang separat.",
        ),
        ru.Achse(
            "struktur_uebergang", "praesenz", prioritaet=2,
            merkmal_key="struktur_uebergang", merkmal_wert=True, min_evidenz=1,
            befund_template="Ein Teil deiner Stocker liegt an einem Struktur-Übergang (z.B. Einleitung → Hauptteil).",
            ursache_template="Das ist typisch, wenn der erste Satz nach einer Zäsur nicht auswendig sitzt.",
            uebung_template="Übe genau den ersten Satz nach jedem Übergang separat, bis er automatisch kommt.",
        ),
        ru.Achse(
            "satzanfang", "anteil", prioritaet=3,
            merkmal_key="satz_position", merkmal_wert="anfang",
            min_evidenz=2, schwelle=0.5,
            befund_template="Bei dir sitzen {anteil:.0%} deiner Stocker direkt am Satzanfang.",
            ursache_template="Das deutet auf unsichere Übergänge zwischen Sätzen hin, nicht auf einzelne schwierige Wörter.",
            uebung_template="Übe bewusst die ersten 2-3 Wörter jedes neuen Satzes.",
        ),
        ru.Achse(
            "wort_wiederholt", "wiederholung", prioritaet=4,
            merkmal_key="wort", min_anzahl=2,
            befund_template="Das Wort '{wort}' taucht bei dir {anzahl}x als Stolperstelle auf.",
            ursache_template="Ein wiederkehrendes Wort lässt sich gezielt einüben.",
            uebung_template="Sprich genau dieses Wort isoliert und mehrmals laut, bis es flüssig kommt.",
        ),
        ru.Achse(
            "zeittrend", "trend", prioritaet=5, zeit_key="start_ms",
            befund_template="Auffällig viele Stocker liegen in der {richtung_text}.",
            ursache_template="{richtung_ursache}",
            uebung_template="{richtung_uebung}",
        ),
    ]

    # Die "trend"-Achse braucht zusammengesetzte Richtungstexte, die von
    # "anfang"/"ende" abhängen — dafür wird das Template dynamisch ergänzt.
    for achse in PAUSEN_ACHSEN:
        if achse.name == "zeittrend":
            treffer_probe = ru._pruefe_trend(stocker_befunde, achse, dauer_s * 1000)
            if treffer_probe and getattr(treffer_probe, "richtung", "") == "anfang":
                achse.befund_template = "Auffällig viele Stocker liegen in der ERSTEN Hälfte der Aufnahme."
                achse.ursache_template = "Das deutet auf Nervosität am Anfang hin, die sich mit der Zeit legt."
                achse.uebung_template = "Ein kurzes Warm-up vor der eigentlichen Aufnahme könnte helfen."
            elif treffer_probe:
                achse.befund_template = "Auffällig viele Stocker liegen in der ZWEITEN Hälfte der Aufnahme."
                achse.ursache_template = "Das kann auf nachlassende Konzentration oder weniger gut vorbereiteten Inhalt gegen Ende hindeuten."
                achse.uebung_template = "Übe besonders den zweiten Teil deiner Präsentation nochmal separat."

    fall_a_text_d2 = [
        "Keine Stocker erkannt — nichts zum Nachschauen nötig. Dein Redefluss "
        "ist an dieser Stelle bereits sehr sicher.",
    ]
    einzelfund_template_d2 = (
        "Zwischen '{vorheriges_wort}' und '{naechstes_wort}' bist du ins "
        "Stocken geraten — {diagnose}"
    )
    fall_c_einleitung_d2 = (
        "Du hast mehrere Stocker an verstreuten, nicht wiederkehrenden "
        "Stellen — kein einzelnes Wort oder Ort sticht heraus:"
    )
    tipp_d2_texte = ru.erkenne_muster_v2(
        stocker_befunde, PAUSEN_ACHSEN, gesamt_dauer_ms=dauer_s * 1000,
        max_tipps=2,
        fall_a_text=fall_a_text_d2,
        einzelfund_template=einzelfund_template_d2,
        fall_c_einleitung=fall_c_einleitung_d2,
    )
    tipp_d2 = tipp_d2_texte

    z += ru.dimension_block_detail(
        "Ungewolltes Stocken", 30, d2_score,
        was_gemessen=[
            "Wie oft du unabsichtlich kurz stockst — also mitten im Satz",
            "unsicher innehältst, ohne dass es wie Absicht klingt.",
        ],
        warum=warum_d2,
        fundstellen=fundstellen_d2,
        tipp=tipp_d2,
    )

    # ── D3 — Gesamtzahl Pausen ───────────────────────────────────────────────
    fundstellen_d3 = []
    for p in zaehlbare[:8]:
        satz = ru.markiere_stelle_im_satz(satz_text(p.satz_index), p.naechstes_wort, "·")
        fundstellen_d3.append(ru.fundstelle_zeile(p.start_ms, satz, f"{p.dauer_ms:.0f} ms Pause"))
    if len(zaehlbare) > 8:
        fundstellen_d3.append(f"  ... und {len(zaehlbare) - 8} weitere Stellen im Video.")

    warum_d3 = [
        f"Insgesamt {len(zaehlbare)} Pausen in {dauer_s:.0f} Sekunden — hochgerechnet",
        f"auf eine Minute wären das {d3_rate:.1f} Pausen.",
        "Faustregel: 5–15 Pausen/Min gelten als natürlicher Redefluss,",
        "über 20/Min wirkt es stockend, unter 5/Min atemlos.",
        f"Bei dir: {d3_text}.",
    ]
    if dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        warum_d3.append("")
        warum_d3.append("⚠ Auch dieser Wert ist bei so einer kurzen Aufnahme nur")
        warum_d3.append("  eine grobe Schätzung, kein exaktes Ergebnis.")

    # rate_kategorie ist eine globale Einordnung (gilt für die ganze Aufnahme,
    # nicht pro Fundstelle) — wird trotzdem in jedes Befund-Dict geschrieben,
    # damit die Achse "zu_wenig"/"zu_viele" darauf zugreifen kann.
    if d3_rate < 5:
        rate_kategorie = "zu_wenig"
    elif d3_rate > 20:
        rate_kategorie = "zu_viel"
    else:
        rate_kategorie = "optimal"

    d3_befunde = []
    for p in zaehlbare:
        d3_befunde.append({
            "vorheriges_wort": p.vorheriges_wort,
            "naechstes_wort": p.naechstes_wort,
            "zeit_str": ru.video_zeit(p.start_ms),
            "rate_kategorie": rate_kategorie,
            "start_ms": p.start_ms,
        })

    D3_ACHSEN = [
        ru.Achse(
            "zu_wenig_pausen", "praesenz", prioritaet=1,
            merkmal_key="rate_kategorie", merkmal_wert="zu_wenig", min_evidenz=1,
            befund_template=f"Mit {d3_rate:.1f} Pausen/Min sprichst du fast ohne Unterbrechung — das wirkt schnell atemlos.",
            ursache_template="Zu wenige Pausen lassen keinen Raum zum Verarbeiten des Gesagten.",
            uebung_template=(
                "\nSchritt 1: Markiere im Text 3 Stellen, an denen ein neuer "
                "Gedanke beginnt.\n"
                "Schritt 2: Sprich bis zu jeder Markierung, mache bewusst 1 "
                "Sekunde Pause, dann sprich weiter.\n"
                "Schritt 3: Sprich den ganzen Abschnitt am Stück mit diesen "
                "3 Pausen — höre dir die Aufnahme danach an."
            ),
        ),
        ru.Achse(
            "zu_viel_pausen", "praesenz", prioritaet=1,
            merkmal_key="rate_kategorie", merkmal_wert="zu_viel", min_evidenz=1,
            befund_template=f"Mit {d3_rate:.1f} Pausen/Min wirkt dein Sprechfluss zerstückelt.",
            ursache_template="Zu viele Unterbrechungen lassen den Vortrag fragmentiert wirken.",
            uebung_template=(
                "\nSchritt 1: Sprich 2 aufeinanderfolgende Sätze ohne jede "
                "Pause dazwischen.\n"
                "Schritt 2: Steigere auf 3, dann 4 Sätze am Stück.\n"
                "Schritt 3: Sprich den ganzen Abschnitt durch und pausiere "
                "bewusst nur an den wirklich wichtigen Stellen."
            ),
        ),
        ru.Achse(
            "pausen_ungleich_verteilt", "trend", prioritaet=2, zeit_key="start_ms",
            befund_template="Deine Pausen häufen sich in der {richtung_text}.",
            ursache_template="{richtung_ursache}",
            uebung_template="{richtung_uebung}",
        ),
    ]
    # Trend-Achse braucht Richtungstext, der von "anfang"/"ende" abhängt
    for achse in D3_ACHSEN:
        if achse.name == "pausen_ungleich_verteilt":
            probe = ru._pruefe_trend(d3_befunde, achse, dauer_s * 1000)
            if probe and getattr(probe, "richtung", "") == "anfang":
                achse.befund_template = "Deine Pausen häufen sich in der ERSTEN Hälfte der Aufnahme."
                achse.ursache_template = "Das kann auf einen unsicheren Einstieg hindeuten."
                achse.uebung_template = "Übe besonders die ersten Sätze nochmal separat."
            elif probe:
                achse.befund_template = "Deine Pausen häufen sich in der ZWEITEN Hälfte der Aufnahme."
                achse.ursache_template = "Das kann auf nachlassende Textsicherheit hindeuten."
                achse.uebung_template = "Übe besonders den zweiten Teil deiner Präsentation nochmal separat."

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde, D3_ACHSEN, gesamt_dauer_ms=dauer_s * 1000, max_tipps=1,
        fall_a_text=[
            "In dieser Aufnahme wurde keine einzige zählbare Pause erkannt — "
            "das ist ungewöhnlich und wirkt gehetzt. Plane bewusst Atempausen "
            "zwischen Sätzen ein.",
        ],
        einzelfund_template=(
            "Bei {zeit_str} liegt eine deiner Pausen. Bei so wenigen Pausen "
            "insgesamt lohnt es sich, bewusst mehr Sprechpausen zwischen "
            "Sinnabschnitten einzubauen."
        ),
        fall_c_einleitung="Deine Pausen sind gleichmäßig über die Aufnahme verteilt, ohne auffällige Häufung — strukturell unauffällig:",
    )

    z += ru.dimension_block_detail(
        "Gesamtzahl Pausen", 30, d3_score,
        was_gemessen=[
            "Wie viele hörbare Pausen insgesamt vorkommen — egal ob bewusst",
            "gesetzt oder unbeabsichtigt.",
        ],
        warum=warum_d3,
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Pausen nach Typ ───────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  4. PAUSEN NACH TYP (ALLE)")
    z.append(ru.SEP2)
    z.append("  Störend (ungewollt) · Neutral (nicht bewertet) · Bewusst (rhetorisch)")
    z.append("")
    typen = {}
    for p in pausen:
        typen[p.typ] = typen.get(p.typ, 0) + 1
    gruppen = {
        "Störend": ["kleiner_stocker", "stocker", "stocker_lang"],
        "Neutral": ["ignorieren", "natuerlich_kurz", "atem"],
        "Bewusst": ["natuerlich", "rhetorisch", "wirkung"],
    }
    for gruppe, typliste in gruppen.items():
        gesamt_gruppe = sum(typen.get(t, 0) for t in typliste)
        z.append(f"  {gruppe} (gesamt: {gesamt_gruppe})")
        for t in typliste:
            anzahl = typen.get(t, 0)
            prozent = anzahl / len(pausen) * 100 if pausen else 0
            z.append(f"    {t:20s}: {anzahl:4d} ({prozent:5.1f}%)")
        z.append("")

    # ── Kernbotschaft-Check ───────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. KERNBOTSCHAFT-CHECK")
    z.append(ru.SEP2)
    z.append("  Kernaussagen wirken erst, wenn das Publikum einen Moment Zeit hat,")
    z.append("  sie zu verarbeiten. Dafür braucht es eine Pause von mindestens")
    z.append("  800 ms vor oder nach dem Satz.")
    z.append("")
    if kb_checks:
        fehlende = [k for k in kb_checks if not k.hat_rhetorische_pause]
        z.append(f"  Geprüfte Kernaussagen:   {len(kb_checks)}")
        z.append(f"  Mit ausreichender Pause: {len(kb_checks) - len(fehlende)}")
        z.append(f"  OHNE ausreichende Pause: {len(fehlende)}")
        z.append("")
        if fehlende:
            z.append("  💡 Nachbessern — lege vor oder nach diesen Sätzen eine Pause ein:")
            for k in fehlende:
                z.append(ru.fundstelle_zeile(k.start_ms, k.text))
                z.append(f"    💡 Deine Kernaussage '{k.text}' hat weder davor noch danach")
                z.append(f"       eine ausreichende Pause (mind. 800ms). Füge direkt danach")
                z.append(f"       eine kurze Pause ein, damit sie beim Publikum wirken kann.")
            z.append("")
    else:
        z.append("  Keine Kernaussagen in der Inhaltsanalyse gefunden.")
        z.append("")

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  6. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Das Modul analysiert alle Lücken zwischen Wörtern im Transkript und")
    z.append("  ordnet jede anhand von Länge und Kontext einem von 8 Typen zu.")
    z.append("  Nur Lücken ab 150 ms gelten als hörbare Pausen.")
    z.append("")
    z.append("  Länge          Typ                    Wirkung")
    z.append("  " + "-" * 58)
    z.append("  < 150 ms       Nicht hörbar            Unter der Wahrnehmungsschwelle")
    z.append("  150 – 300 ms   Kleiner Stocker         Kaum hörbar, aber messbar")
    z.append("  300 – 800 ms   Stocker                 Hörbar, klingt unsicher")
    z.append("  800 – 1500 ms  Sinnpause / Atempause   Natürlich, strukturierend")
    z.append("  1500 – 2000 ms Sinnpause (lang)        Deutliche Betonung")
    z.append("  2000 – 4000 ms Wirkungspause           Maximale Aufmerksamkeit")
    z.append("  > 4000 ms      Zu lang / Schnitt       Nicht bewertet")
    z.append("")
    z.append("  Quellen: Campione & Véronis (2002) · Heldner & Edlund (2010) ·")
    z.append("  O'Connell & Kowal (1983) · Hieke (1983)")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)

    return "\n".join(z)



# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def analyse_pausen(
    transkript_pfad: Path,
    inhalt_pfad: Optional[Path] = None,
    output_json_pfad: Optional[Path] = None,
    output_txt_kurz_pfad: Optional[Path] = None,
    output_txt_detail_pfad: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Haupt-Einstiegspunkt für die Pausen-Analyse.

    Args:
        transkript_pfad: Pfad zum Transkript (Format: Wort HH:MM:SS.mmm HH:MM:SS.mmm)
        inhalt_pfad: Pfad zu inhalt_analyse_output.json (optional, aber empfohlen)
        output_json_pfad: Zielpfad für JSON-Output
        output_txt_kurz_pfad: Zielpfad für die Kurzfassung (.txt)
        output_txt_detail_pfad: Zielpfad für die Detailansicht (.txt)

    Returns:
        Dictionary mit allen Ergebnissen (für gesamtscore.py)
    """

    print(f"[pausen_analyse] Starte Analyse: {transkript_pfad.name}")

    # 1. Transkript parsen
    woerter = parse_transkript(transkript_pfad)
    if not woerter:
        raise ValueError("Keine Wörter im Transkript gefunden.")

    print(f"[pausen_analyse] {len(woerter)} Wörter geladen.")

    # 2. Inhaltsanalyse laden
    inhalt_data = None
    if inhalt_pfad and inhalt_pfad.exists():
        inhalt_data = lade_inhalt_analyse(inhalt_pfad)
        if inhalt_data:
            print("[pausen_analyse] Inhaltsanalyse geladen.")

    # 3. Sätze extrahieren und anreichern
    saetze = extrahiere_saetze(woerter, inhalt_data)
    print(f"[pausen_analyse] {len(saetze)} Sätze erkannt.")

    markiere_kernbotschaften(saetze, inhalt_data)
    markiere_struktur_uebergaenge(saetze, inhalt_data)

    kernbotschaft_count = sum(1 for s in saetze if s.ist_kernbotschaft)
    print(f"[pausen_analyse] {kernbotschaft_count} Kernbotschaften markiert.")

    # 4. Pausen finden und klassifizieren
    pausen = finde_pausen(woerter, saetze)
    klassifiziere_alle_pausen(pausen)

    zaehlbare = [p for p in pausen if p.ist_zaehlbar]
    stocker = [p for p in pausen if p.ist_stocker]
    rhetorische = [p for p in pausen if p.ist_rhetorisch]

    print(f"[pausen_analyse] {len(pausen)} Lücken gefunden.")
    print(f"[pausen_analyse]   -> {len(zaehlbare)} zählbare Pausen")
    print(f"[pausen_analyse]   -> {len(stocker)} Stocker")
    print(f"[pausen_analyse]   -> {len(rhetorische)} rhetorische Pausen")

    # 5. Präsentationsdauer
    gesamt_dauer_ms = woerter[-1].end_ms - woerter[0].start_ms
    dauer_min = gesamt_dauer_ms / 60000.0

    # 6. Kernbotschaft-Check
    kb_checks = pruefe_kernbotschaften(saetze, pausen)
    fehlende_kb = [k for k in kb_checks if not k.hat_rhetorische_pause]

    # 7. Scoring
    d1_score, d1_ratio, d1_text = berechne_d1_rhetorische_qualitaet(pausen)
    d2_score, d2_rate, d2_text = berechne_d2_stocker_rate(pausen, dauer_min)
    d3_score, d3_rate, d3_text = berechne_d3_pausen_haushalt(pausen, dauer_min)
    gesamt_score = berechne_gesamtscore(d1_score, d2_score, d3_score)

    print(f"[pausen_analyse] Scoring: D1={d1_score}, D2={d2_score}, D3={d3_score}, Gesamt={gesamt_score}")

    # 8. JSON-Output vorbereiten
    output_data = {
        "modul": "pausen_analyse",
        "version": "2.0",
        "timestamp": datetime.now().isoformat(),
        "input": str(transkript_pfad),
        "meta": {
            "woerter_gesamt": len(woerter),
            "saetze_gesamt": len(saetze),
            "praesentationsdauer_min": round(dauer_min, 3),
            "praesentationsdauer_ms": round(gesamt_dauer_ms, 3),
        },
        "pausen": [p.to_dict() for p in pausen],
        "statistiken": {
            "anzahl_pausen": len(pausen),
            "anzahl_zaehlbar": len(zaehlbare),
            "anzahl_stocker": len(stocker),
            "anzahl_rhetorisch": len(rhetorische),
            "stocker_rate_pro_min": round(d2_rate, 2),
            "pausen_rate_pro_min": round(d3_rate, 2),
            "rhetorisch_ratio": round(d1_ratio, 4),
        },
        "kernbotschaft_check": {
            "gesamt": len(kb_checks),
            "mit_rhetorischer_pause": len(kb_checks) - len(fehlende_kb),
            "ohne_rhetorische_pause": len(fehlende_kb),
            "details": [k.to_dict() for k in kb_checks]
        },
        "scoring": {
            "d1_rhetorische_qualitaet": {
                "gewichtung": GEWICHT_D1,
                "punkte": d1_score,
                "bewertung": d1_text,
                "ratio": round(d1_ratio, 4)
            },
            "d2_stocker_rate": {
                "gewichtung": GEWICHT_D2,
                "punkte": d2_score,
                "bewertung": d2_text,
                "rate_pro_min": round(d2_rate, 2)
            },
            "d3_pausen_haushalt": {
                "gewichtung": GEWICHT_D3,
                "punkte": d3_score,
                "bewertung": d3_text,
                "rate_pro_min": round(d3_rate, 2)
            },
            "gesamtscore": gesamt_score
        }
    }

    # 9. Speichern
    if output_json_pfad:
        output_json_pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_pfad, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"[pausen_analyse] JSON gespeichert: {output_json_pfad}")

    if output_txt_kurz_pfad:
        output_txt_kurz_pfad.parent.mkdir(parents=True, exist_ok=True)
        kurz_report = generiere_kurz_report(
            pausen, kb_checks,
            d1_score, d1_ratio, d1_text,
            d2_score, d2_rate, d2_text,
            d3_score, d3_rate, d3_text,
            gesamt_score, dauer_min,
            transkript_pfad.name,
        )
        with open(output_txt_kurz_pfad, "w", encoding="utf-8") as f:
            f.write(kurz_report)
        print(f"[pausen_analyse] Kurz-Report gespeichert: {output_txt_kurz_pfad}")

    if output_txt_detail_pfad:
        output_txt_detail_pfad.parent.mkdir(parents=True, exist_ok=True)
        detail_report = generiere_detail_report(
            pausen, saetze, kb_checks,
            d1_score, d1_ratio, d1_text,
            d2_score, d2_rate, d2_text,
            d3_score, d3_rate, d3_text,
            gesamt_score, dauer_min,
            transkript_pfad.name,
        )
        with open(output_txt_detail_pfad, "w", encoding="utf-8") as f:
            f.write(detail_report)
        print(f"[pausen_analyse] Detail-Report gespeichert: {output_txt_detail_pfad}")

    print(f"[pausen_analyse] Fertig. Gesamt-Score: {gesamt_score}/100")

    return output_data


# =============================================================================
# CLI / MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Pausen-Analyse für Präsentationsbewertungs-AI"
    )
    parser.add_argument(
        "transkript",
        type=str,
        help="Pfad zum Transkript (Format: Wort HH:MM:SS.mmm HH:MM:SS.mmm)"
    )
    parser.add_argument(
        "--inhalt",
        type=str,
        default=None,
        help="Pfad zu inhalt_analyse_output.json (optional, empfohlen)"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="zwischen_output/pausen_analyse_output.json",
        help="Zielpfad für JSON-Output"
    )
    parser.add_argument(
        "--output-txt-kurz",
        type=str,
        default=None,
        help="Zielpfad für Kurz-Report (Default: reports/pausen/pausen_kurz_TIMESTAMP.txt)"
    )
    parser.add_argument(
        "--output-txt-detail",
        type=str,
        default=None,
        help="Zielpfad für Detail-Report (Default: reports/pausen/pausen_detail_TIMESTAMP.txt)"
    )

    args = parser.parse_args()

    transkript_pfad = Path(args.transkript)
    inhalt_pfad = Path(args.inhalt) if args.inhalt else DEFAULT_INPUT_DIR / "inhalt_analyse_output.json"

    output_json = Path(args.output_json)

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_txt_kurz = Path(args.output_txt_kurz) if args.output_txt_kurz else DEFAULT_REPORT_DIR / f"pausen_kurz_{ts}.txt"
    output_txt_detail = Path(args.output_txt_detail) if args.output_txt_detail else DEFAULT_REPORT_DIR / f"pausen_detail_{ts}.txt"

    if not transkript_pfad.exists():
        print(f"[FEHLER] Transkript nicht gefunden: {transkript_pfad}")
        exit(1)

    try:
        analyse_pausen(transkript_pfad, inhalt_pfad, output_json, output_txt_kurz, output_txt_detail)
    except Exception as e:
        print(f"[FEHLER] {e}")
        raise
