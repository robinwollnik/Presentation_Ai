"""sprechtempo_analyse.py
==============================================================================
Analyse des Sprechtempos einer Präsentation.

Misst die Sprechgeschwindigkeit in Silben pro Sekunde und bewertet:
  1. Gesamttempo         (Gewichtung 40%)  Optimum: 4.5-5.5 Silben/Sek
  2. Variation           (Gewichtung 30%)  Optimum: CV 0.20-0.35
  3. Kernbotschaften     (Gewichtung 30%)  Optimum: >= 10% Verlangsamung

Inputs:
  - Transkript (.txt) im Format: Wort HH:MM:SS.mmm HH:MM:SS.mmm
  - inhalt_analyse_output.json aus zwischen_output/

Outputs:
  - TXT-Report in reports/sprechtempo/
  - JSON-Intermediate in zwischen_output/sprechtempo_analyse_output.json"""

import re
import json
import sys
import statistics
from pathlib import Path
from datetime import datetime

import report_utils as ru

import tkinter as tk
from tkinter import filedialog


# ============================================================================
# KONFIGURATION
# ============================================================================

GEWICHTUNG = {
    'tempo': 0.40,
    'variation': 0.30,
    'kernbotschaften': 0.30,
}

# Tempo-Schwellenwerte in Silben pro Sekunde
TEMPO_OPTIMUM_MIN = 4.5
TEMPO_OPTIMUM_MAX = 5.5
TEMPO_LEICHT_LANGSAM = 3.5
TEMPO_LEICHT_SCHNELL = 6.5
TEMPO_EXTREM_LANGSAM = 2.5
TEMPO_EXTREM_SCHNELL = 7.5

# Variationskoeffizient (Standardabweichung / Mittelwert)
CV_OPTIMUM_MIN = 0.20
CV_OPTIMUM_MAX = 0.35
CV_LEICHT_MIN = 0.10
CV_LEICHT_MAX = 0.50

# Kernbotschaften-Verlangsamung (als Anteil, 0.10 = 10%)
VERLANGSAMUNG_GUT = 0.10

# Mindestwerte für valide Tempo-Messung pro Satz
MIN_SATZ_DAUER = 1.0    # Sekunden
MIN_SATZ_SILBEN = 3

# Pfade (relativ zum Projekt-Root)
PROJEKT_ROOT = Path(__file__).resolve().parent
JSON_INPUT_PFAD = PROJEKT_ROOT / "zwischen_output" / "inhalt_analyse_output.json"
JSON_OUTPUT_PFAD = PROJEKT_ROOT / "zwischen_output" / "sprechtempo_analyse_output.json"
REPORT_ORDNER = PROJEKT_ROOT / "reports" / "sprechtempo"


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def zeit_zu_sekunden(zeitstring):
    """Konvertiert HH:MM:SS.mmm zu Sekunden als float."""
    teile = zeitstring.strip().split(':')
    if len(teile) != 3:
        raise ValueError(f"Ungültiges Zeitformat: {zeitstring}")
    return int(teile[0]) * 3600 + int(teile[1]) * 60 + float(teile[2])


def hole_ende(eintrag):
    """Feldname 'end' bevorzugt, 'ende' als Fallback."""
    if 'end' in eintrag:
        return eintrag['end']
    if 'ende' in eintrag:
        return eintrag['ende']
    raise KeyError("Weder 'end' noch 'ende' im Eintrag")


# Ausnahmenliste für unregelmäßige Wörter (v2-Fix).
# Diese Wörter widersprechen der Diphthong- und Doppelvokal-Regel und
# werden deshalb direkt gemappt.
SILBEN_AUSNAHMEN = {
    'idee': 3, 'ideen': 3,
    'museum': 4, 'museums': 4, 'museen': 3,
    'familie': 4, 'familien': 4,
    'aktuell': 3, 'aktuelle': 4, 'aktuellen': 4,
    'individuell': 5, 'individuelle': 5,
    'situation': 4, 'situationen': 5,
    'nation': 3, 'nationen': 4, 'national': 4,
    'region': 3, 'regionen': 4, 'regional': 4,
    'union': 3, 'unionen': 4,
    'million': 3, 'millionen': 4,
    'milliarde': 4, 'milliarden': 4,
    'kreation': 4, 'kreationen': 5,
    'aktion': 3, 'aktionen': 4,
    'real': 2, 'reale': 3, 'realen': 3, 'realitaet': 4,
    'kreativitaet': 5, 'kreativität': 5,
    # -tion Wörter (Nation-Regel: -tion = 1 Silbe)
    'präsentation': 4, 'präsentationen': 5,
    'praesentation': 4, 'praesentationen': 5,
    'produktion': 3, 'produktionen': 4,
    'funktion': 3, 'funktionen': 4,
    'position': 3, 'positionen': 4,
    'diskussion': 3, 'diskussionen': 4,
    'motivation': 4, 'motivationen': 5,
    'information': 4, 'informationen': 5,
    'organisation': 5, 'organisationen': 6,
    # Fremdwoerter mit y am Ende (vokalisches y)
    'baby': 2, 'babys': 2, 'party': 2, 'partys': 2,
    'story': 2, 'stories': 2, 'hobby': 2, 'hobbys': 2,
    'city': 2, 'jury': 2, 'company': 3,
}


def zaehle_silben(wort):
    """
    Zählt Silben in einem deutschen Wort (v2-konform).

    Regeln:
      - Ausnahmenliste zuerst prüfen (Idee, Museum, Familie, ...)
      - Vokale/Umlaute zählen: a, e, i, o, u, ä, ö, ü
      - y-Sonderregel: y nur als Vokal wenn zwischen Konsonanten
        (z.B. 'System', 'Rhythmus'). Am Wortanfang/-ende oder vor Vokal
        ist y Konsonant (Yoga, Yacht).
      - Diphthonge als eine Silbe: ai, ei, au, äu, eu, ie, ui, ay, ey
      - Aufeinanderfolgende gleiche Vokale (See, Boot) = eine Silbe
      - Minimum: 1 Silbe pro Wort
    """
    wort_clean = re.sub(r'[^a-z\u00e4\u00f6\u00fc\u00dfy]', '', wort.lower())
    if not wort_clean:
        return 0

    # 1. Ausnahmenliste
    if wort_clean in SILBEN_AUSNAHMEN:
        return SILBEN_AUSNAHMEN[wort_clean]

    diphthonge = {'ai', 'ei', 'au', '\u00e4u', 'eu', 'ie', 'ui', 'ay', 'ey'}
    vokale_ohne_y = set('aeiou\u00e4\u00f6\u00fc')

    def ist_vokal(pos):
        """y ist nur Vokal wenn zwischen zwei Konsonanten steht."""
        c = wort_clean[pos]
        if c in vokale_ohne_y:
            return True
        if c == 'y':
            # y ist Konsonant am Anfang, am Ende oder neben einem Vokal
            hat_vokal_davor = pos > 0 and wort_clean[pos - 1] in vokale_ohne_y
            hat_vokal_danach = (pos + 1 < len(wort_clean)
                                and wort_clean[pos + 1] in vokale_ohne_y)
            am_rand = pos == 0 or pos == len(wort_clean) - 1
            if am_rand or hat_vokal_davor or hat_vokal_danach:
                return False
            return True
        return False

    silben = 0
    i = 0
    while i < len(wort_clean):
        if ist_vokal(i):
            # Diphthong-Prüfung (nutzt echte Zeichen — y ist hier nie Teil
            # eines Standard-Diphthongs ausser in ay/ey)
            zwei_zeichen = wort_clean[i:i + 2]
            if i + 1 < len(wort_clean) and zwei_zeichen in diphthonge:
                silben += 1
                i += 2
            else:
                silben += 1
                # Gleiche Doppelvokale überspringen (See, Boot)
                while i + 1 < len(wort_clean) and wort_clean[i + 1] == wort_clean[i]:
                    i += 1
                i += 1
        else:
            i += 1

    return max(1, silben)


def silben_zu_wpm(silben_pro_sek):
    """Rechnet Silben/Sek zu Wörtern/Min um (Deutsch: ~2.5 Silben pro Wort)."""
    return round((silben_pro_sek / 2.5) * 60, 1)


# ============================================================================
# EINLESEN
# ============================================================================

def lade_transkript(pfad):
    """
    Liest Transkript im Format: Wort HH:MM:SS.mmm HH:MM:SS.mmm

    Gibt Liste zurück: [{'wort', 'start_s', 'ende_s', 'silben'}, ...]
    """
    muster = re.compile(
        r'^(\S+)\s+(\d{2}:\d{2}:\d{2}\.\d+)\s+(\d{2}:\d{2}:\d{2}\.\d+)\s*$'
    )

    def parse(datei):
        eintraege = []
        for zeile in datei:
            zeile = zeile.strip()
            if not zeile:
                continue
            treffer = muster.match(zeile)
            if not treffer:
                continue
            eintraege.append({
                'wort': treffer.group(1),
                'start_s': zeit_zu_sekunden(treffer.group(2)),
                'ende_s': zeit_zu_sekunden(treffer.group(3)),
                'silben': zaehle_silben(treffer.group(1)),
            })
        return eintraege

    try:
        with open(pfad, 'r', encoding='utf-8') as f:
            woerter = parse(f)
    except UnicodeDecodeError:
        # Fallback für Windows-Encodings
        with open(pfad, 'r', encoding='latin-1') as f:
            woerter = parse(f)

    if not woerter:
        raise ValueError("Keine gültigen Zeilen im Transkript gefunden.")

    return woerter


def lade_inhalt_json(pfad):
    """Liest die JSON-Ausgabe von inhalt_analyse.py ein."""
    with open(pfad, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================================
# TEMPO-BERECHNUNG
# ============================================================================

def berechne_tempo_pro_satz(woerter, satzgrenzen):
    """
    Ordnet Wörter den Sätzen zu und berechnet Tempo pro Satz.

    Tempo = Netto-Artikulationsrate = Silben / Summe der Wort-Dauern
    (Pausen zählen nicht zur Sprechzeit)
    """
    ergebnis = []
    for satz in satzgrenzen:
        satz_start = zeit_zu_sekunden(satz['start'])
        satz_ende = zeit_zu_sekunden(hole_ende(satz))

        # Wort gehört zum Satz, wenn sein Start im Satz-Zeitraum liegt
        satz_woerter = [
            w for w in woerter
            if w['start_s'] >= satz_start - 0.05
            and w['start_s'] <= satz_ende + 0.05
        ]

        if not satz_woerter:
            continue

        silben_summe = sum(w['silben'] for w in satz_woerter)
        sprechzeit = sum(w['ende_s'] - w['start_s'] for w in satz_woerter)
        gesamt_dauer = satz_woerter[-1]['ende_s'] - satz_woerter[0]['start_s']

        if sprechzeit <= 0:
            continue

        tempo = silben_summe / sprechzeit

        ergebnis.append({
            'satz_id': satz['satz_id'],
            'text': satz.get('text', ''),
            'start_s': satz_start,
            'ende_s': satz_ende,
            'silben': silben_summe,
            'sprechzeit_s': round(sprechzeit, 3),
            'gesamt_dauer_s': round(gesamt_dauer, 3),
            'tempo': tempo,
            'valid': gesamt_dauer >= MIN_SATZ_DAUER and silben_summe >= MIN_SATZ_SILBEN,
        })

    return ergebnis


def berechne_gesamttempo(woerter):
    """Netto-Sprechrate über alle Wörter (Artikulationsrate)."""
    silben = sum(w['silben'] for w in woerter)
    sprechzeit = sum(w['ende_s'] - w['start_s'] for w in woerter)
    if sprechzeit <= 0:
        return 0.0
    return silben / sprechzeit


# ============================================================================
# BEWERTUNG (jeweils label + punkte)
# ============================================================================

def bewerte_gesamttempo(tempo):
    if TEMPO_OPTIMUM_MIN <= tempo <= TEMPO_OPTIMUM_MAX:
        return 'optimal', 100
    if TEMPO_LEICHT_LANGSAM <= tempo < TEMPO_OPTIMUM_MIN:
        return 'etwas_langsam', 80
    if TEMPO_OPTIMUM_MAX < tempo <= TEMPO_LEICHT_SCHNELL:
        return 'etwas_schnell', 80
    if TEMPO_EXTREM_LANGSAM <= tempo < TEMPO_LEICHT_LANGSAM:
        return 'zu_langsam', 50
    if TEMPO_LEICHT_SCHNELL < tempo <= TEMPO_EXTREM_SCHNELL:
        return 'zu_schnell', 50
    return 'extrem', 20


def bewerte_variation(cv):
    if CV_OPTIMUM_MIN <= cv <= CV_OPTIMUM_MAX:
        return 'optimal', 100
    if CV_LEICHT_MIN <= cv < CV_OPTIMUM_MIN:
        return 'leicht', 75
    if CV_OPTIMUM_MAX < cv <= CV_LEICHT_MAX:
        return 'stark', 75
    if cv < CV_LEICHT_MIN:
        return 'monoton', 40
    return 'chaotisch', 40


def bewerte_kernbotschaften(verlangsamung):
    """verlangsamung = (tempo_neben - tempo_kern) / tempo_neben"""
    if verlangsamung >= VERLANGSAMUNG_GUT:
        return 'gut', 100
    if verlangsamung >= 0:
        return 'neutral', 70
    return 'negativ', 30


def berechne_gesamtscore(punkte_tempo, punkte_variation, punkte_kern):
    return round(
        GEWICHTUNG['tempo'] * punkte_tempo
        + GEWICHTUNG['variation'] * punkte_variation
        + GEWICHTUNG['kernbotschaften'] * punkte_kern,
        1,
    )


# ============================================================================
# FEEDBACK-TEXTE (Hochdeutsch, 5 Levels pro Dimension)
# ============================================================================

TEMPO_FEEDBACK = {
    'optimal': (
        "Dein Sprechtempo liegt im idealen Bereich für Präsentationen "
        "(4.5–5.5 Silben/Sek). Das Publikum kann dir mühelos folgen."
    ),
    'etwas_langsam': (
        "Dein Tempo ist ruhig und gut verständlich, könnte aber an einigen "
        "Stellen etwas mehr Energie vertragen. Bei komplexen Inhalten ist dieses "
        "Tempo dennoch angemessen."
    ),
    'etwas_schnell': (
        "Dein Tempo ist zügig und noch verständlich. Achte darauf, "
        "wichtige Stellen bewusst zu verlangsamen, damit die Kernaussagen ankommen."
    ),
    'zu_langsam': (
        "Dein Tempo ist deutlich zu langsam. Das Publikum verliert bei diesem "
        "Tempo schnell die Aufmerksamkeit. Steigere die Sprechgeschwindigkeit, "
        "um lebendiger zu wirken."
    ),
    'zu_schnell': (
        "Dein Tempo ist zu schnell. Studien zeigen, dass die Verständlichkeit "
        "ab 180 Wörtern pro Minute um bis zu 22 % sinkt. Verlangsame "
        "bewusst und setze mehr Pausen."
    ),
    'extrem': (
        "Dein Tempo liegt weit außerhalb des Referenzbereichs. Eine deutliche "
        "Anpassung ist notwendig, damit das Publikum dir folgen kann."
    ),
}

VARIATION_FEEDBACK = {
    'optimal': (
        "Dein Tempo variiert in einem angenehmen Bereich. Diese Dynamik hält "
        "die Aufmerksamkeit des Publikums aufrecht."
    ),
    'leicht': (
        "Deine Tempo-Variation ist vorhanden, könnte aber ausgeprägter sein. "
        "Setze bewusst schnellere und langsamere Passagen ein."
    ),
    'stark': (
        "Deine Tempo-Variation ist sehr ausgeprägt. Achte darauf, dass "
        "die Wechsel dem Inhalt dienen und nicht zufällig wirken."
    ),
    'monoton': (
        "Dein Tempo ist zu konstant. Monotonie wirkt einschläfernd. Baue "
        "bewusst Tempo-Wechsel ein, insbesondere bei Kernbotschaften und Übergängen."
    ),
    'chaotisch': (
        "Dein Tempo wechselt sehr sprunghaft. Das kann unruhig oder nervös "
        "wirken. Strebe einen ruhigeren Grundrhythmus mit gezielten "
        "Variationen an."
    ),
}

KERNBOTSCHAFT_FEEDBACK = {
    'gut': (
        "Du verlangsamst dein Tempo bei Kernbotschaften spürbar. Das betont "
        "diese Aussagen effektiv und lässt sie beim Publikum ankommen."
    ),
    'neutral': (
        "Deine Kernbotschaften werden im gleichen Tempo wie der Rest gesprochen. "
        "Eine bewusste Verlangsamung um mindestens 10 % würde sie deutlich "
        "stärker hervorheben."
    ),
    'negativ': (
        "Du sprichst bei Kernbotschaften schneller als im Durchschnitt. Das "
        "schwächt ihre Wirkung erheblich. Verlangsame bewusst bei "
        "wichtigen Aussagen."
    ),
}


# ============================================================================
# STRUKTUR-CHECK (nur informativ, geht nicht in den Score)
# ============================================================================

def analysiere_struktur(saetze_mit_tempo, struktur_segmente, gesamttempo):
    """Prüft Tempo pro Struktur-Segment und ob die Erwartung erfüllt ist."""
    ergebnis = []
    for segment in struktur_segmente:
        seg_start = zeit_zu_sekunden(segment['start'])
        seg_ende = zeit_zu_sekunden(hole_ende(segment))

        segment_saetze = [
            s for s in saetze_mit_tempo
            if s['valid']
            and s['start_s'] >= seg_start - 0.5
            and s['ende_s'] <= seg_ende + 0.5
        ]
        if not segment_saetze:
            continue

        seg_tempo = statistics.mean(s['tempo'] for s in segment_saetze)
        typ = segment['typ']
        erwartet_langsamer = typ in ('Einleitung', 'Schluss', 'Uebergang', 'Zusammenfassung')
        abweichung = (seg_tempo - gesamttempo) / gesamttempo if gesamttempo > 0 else 0

        if erwartet_langsamer:
            erfuellt = seg_tempo <= gesamttempo * 1.05  # 5% Toleranz
        else:
            erfuellt = True

        ergebnis.append({
            'typ': typ,
            'start': segment['start'],
            'end': hole_ende(segment),
            'tempo': round(seg_tempo, 2),
            'abweichung_prozent': round(abweichung * 100, 1),
            'erwartung_erfuellt': erfuellt,
            'erwartet_langsamer': erwartet_langsamer,
        })
    return ergebnis


# ============================================================================
# OUTPUT
# ============================================================================


def mit_wpm(silben_sek):
    """Formatiert Silben/Sek mit WPM-Äquivalent in Klammern."""
    wpm = round((silben_sek / 2.5) * 60)
    return f"{silben_sek:.2f} Silben/Sek (~{wpm} Wörter/Min)"


# ============================================================================
# REPORT — KURZFASSUNG
# ============================================================================

TEMPO_FEEDBACK_KURZ = {
    'optimal': "Ideales Tempo, gut verständlich",
    'etwas_langsam': "Etwas ruhig, aber verständlich",
    'etwas_schnell': "Etwas zügig, noch verständlich",
    'zu_langsam': "Deutlich zu langsam",
    'zu_schnell': "Deutlich zu schnell",
    'extrem': "Weit außerhalb des Zielbereichs",
}

VARIATION_FEEDBACK_KURZ = {
    'optimal': "Angenehme, natürliche Abwechslung",
    'leicht': "Etwas mehr Abwechslung möglich",
    'stark': "Sehr ausgeprägte Variation",
    'monoton': "Zu gleichförmig, wirkt einschläfernd",
    'chaotisch': "Sehr sprunghaft, wirkt unruhig",
}

KERNBOTSCHAFT_FEEDBACK_KURZ = {
    'gut': "Bewusst verlangsamt — gut betont",
    'neutral': "Gleiches Tempo wie der Rest",
    'negativ': "Schneller als der Rest — schwächt die Wirkung",
}

def erstelle_kurz_report(analyse, pfad):
    z = ru.kurz_header("SPRECHTEMPO", analyse['transkript_datei'], f"{int(analyse['dauer_s']//60)} Min {int(analyse['dauer_s']%60)} Sek")
    score = analyse['gesamtscore']
    z += ru.gesamtergebnis_block(
        score,
        "Dein Sprechtempo ist überzeugend. Kleinigkeiten kannst du feinjustieren.",
        "Dein Sprechtempo ist ausbaufähig — einzelne Bereiche brauchen Arbeit.",
        "Dein Sprechtempo weicht deutlich vom Optimum ab.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE DREI TEILWERTE")
    z.append(ru.SEP2)
    z += ru.dimension_zeile_kurz("Gesamttempo", 40, analyse['punkte_tempo'],
                                  TEMPO_FEEDBACK_KURZ[analyse['bewertung_tempo']])
    variation_text = VARIATION_FEEDBACK_KURZ.get(analyse['bewertung_variation'], "Zu wenige valide Sätze")
    z += ru.dimension_zeile_kurz("Tempo-Variation", 30, analyse['punkte_variation'], variation_text)
    if analyse['kern_daten_vorhanden']:
        kern_text = KERNBOTSCHAFT_FEEDBACK_KURZ[analyse['bewertung_kernbotschaften']]
    else:
        kern_text = "Keine Kernbotschaften gefunden"
    z += ru.dimension_zeile_kurz("Kernbotschaften", 30, analyse['punkte_kernbotschaften'], kern_text)
    z.append("")

    if analyse['kern_daten_vorhanden'] and analyse['verlangsamung_prozent'] is not None \
            and analyse['verlangsamung_prozent'] < 0:
        z.append(f"  ⚠ Du sprichst bei Kernaussagen {abs(analyse['verlangsamung_prozent']):.0f}%"
                 f" SCHNELLER statt langsamer — das schwächt ihre Wirkung.")
        z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    z += _empfehlungen(analyse)
    z.append("")
    z.append("  Wo genau im Video du zu schnell/langsam sprichst, und warum diese")
    z.append("  Punktzahl herauskommt, steht im ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w', encoding='utf-8') as f:
        f.write('\n'.join(z))


def _empfehlungen(analyse):
    score = analyse['gesamtscore']
    z = []
    if score >= 75:
        z.append("  Bereits auf gutem Niveau — halte diesen Standard:")
        z.append("  1. Variiere dein Tempo bewusst an Übergängen.")
        z.append("  2. Verlangsame kurz vor Kernaussagen als rhetorisches Signal.")
        return z

    punkte = []
    if analyse['bewertung_tempo'] in ('zu_langsam', 'zu_schnell', 'extrem'):
        if analyse['bewertung_tempo'] in ('zu_schnell', 'extrem'):
            punkte.append("Verlangsame bewusst: Ziel 4.5–5.5 Silben/Sek (~108–132 Wörter/Min).")
        else:
            punkte.append("Steigere dein Grundtempo: Ziel mindestens 108 Wörter/Min.")
    if analyse['kern_daten_vorhanden'] and analyse['bewertung_kernbotschaften'] in ('negativ', 'neutral'):
        punkte.append("Verlangsame bewusst bei deinen Kernaussagen um mindestens 10%.")
    if not punkte:
        punkte.append("Übe dein Grundtempo mit einer Stoppuhr — Ziel 4.5–5.5 Silben/Sek.")
        punkte.append("Markiere deine Kernaussagen im Text und übe, dort bewusst langsamer zu sprechen.")

    for i, p in enumerate(punkte, 1):
        z.append(f"  {i}. {p}")
    return z


# ============================================================================
# REPORT — DETAILANSICHT
# ============================================================================

def erstelle_detail_report(analyse, pfad):
    z = ru.detail_header("SPRECHTEMPO", analyse['transkript_datei'], f"{int(analyse['dauer_s']//60)} Min {int(analyse['dauer_s']%60)} Sek")
    z += ru.build_toc([
        "Gesamtergebnis",
        "Gesamttempo — Begründung & Fundstellen",
        "Tempo-Variation — Begründung & Fundstellen",
        "Kernbotschaften — Begründung & Fundstellen",
        "Struktur-Analyse (Einleitung/Hauptteil/Schluss)",
        "Hintergrund & Referenzwerte",
    ])

    score = analyse['gesamtscore']
    z += ru.gesamtergebnis_block(
        score,
        "Dein Sprechtempo ist überzeugend. Kleinigkeiten kannst du feinjustieren.",
        "Dein Sprechtempo ist ausbaufähig — einzelne Bereiche brauchen Arbeit.",
        "Dein Sprechtempo weicht deutlich vom Optimum ab.",
    )

    saetze = analyse.get('saetze_mit_tempo', [])

    # ── D1 Gesamttempo ───────────────────────────────────────────────────────
    # Befund-Liste für die Muster-Engine: jeder valide Satz mit Tempo-Einordnung
    valide_saetze = [s for s in saetze if s['valid']]
    d1_befunde = []
    for s in valide_saetze:
        if s['tempo'] < 3.5 or s['tempo'] > 6.5:
            einordnung = "extrem"
        else:
            einordnung = "normal"
        d1_befunde.append({
            "satz": s['text'],
            "tempo": s['tempo'],
            "abweichung": einordnung,
            "einordnung_text": "zu schnell" if s['tempo'] > 5.5 else "zu langsam" if s['tempo'] < 4.5 else "im Zielbereich",
            "gegenrichtung": "langsamer" if s['tempo'] > 5.5 else "schneller",
            "zeit_str": ru.video_zeit(s['start_s'] * 1000),
            "start_ms": s['start_s'] * 1000,
        })

    # "Wo genau" nutzt DIESELBE Grundlage wie die Muster-Engine (abweichung),
    # nicht die grobe Gesamtkategorie — sonst können sich beide widersprechen
    extreme_saetze = [b for b in d1_befunde if b["abweichung"] == "extrem"]
    fundstellen_d1 = []
    if extreme_saetze:
        richtung_ueberschrift = ("Sätze mit auffälligem Tempo:")
        fundstellen_d1.append(f"  {richtung_ueberschrift}")
        for b in sorted(extreme_saetze, key=lambda b: -abs(b["tempo"] - 5.0))[:5]:
            fundstellen_d1.append(ru.fundstelle_zeile(
                b["start_ms"], b["satz"], f"{b['tempo']:.1f} Silben/Sek — {b['einordnung_text']}"))
    else:
        fundstellen_d1 = ["  Dein Tempo liegt im optimalen Bereich — keine auffälligen",
                           "  Stellen zum Nachschauen."]

    D1_ACHSEN = [
        ru.Achse(
            "durchgehend_extrem", "anteil", prioritaet=1,
            merkmal_key="abweichung", merkmal_wert="extrem", min_evidenz=2, schwelle=0.5,
            befund_template="Bei {anteil:.0%} deiner Sätze liegt dein Tempo im Extrembereich — unter 3,5 oder über 6,5 Silben pro Sekunde.",
            ursache_template="Das ist kein einzelner Ausrutscher, sondern zieht sich durch die ganze Aufnahme — dein Grundtempo insgesamt liegt nicht im optimalen Bereich.",
            uebung_template=(
                "\nSchritt 1: Sprich 30 Sekunden aus deinem Text mit einer "
                "Stoppuhr, zähle die Silben — vergleiche mit dem Zielbereich "
                "4,5-5,5 Silben/Sek.\n"
                "Schritt 2: Übe denselben Abschnitt nochmal, bewusst im "
                "Zielbereich, auch wenn es sich zunächst falsch anfühlt.\n"
                "Schritt 3: Nimm dich auf und vergleiche beide Versionen — "
                "welche ist besser verständlich?"
            ),
        ),
        ru.Achse(
            "zeittrend_tempo", "praesenz", prioritaet=2,
            merkmal_key="zeittrend_richtung", merkmal_wert="steigt", min_evidenz=1,
            befund_template="Von der ersten zur zweiten Hälfte deiner Aufnahme steigt dein Tempo spürbar an.",
            ursache_template="Das passiert typischerweise, wenn die Zeit knapp wird oder Nervosität in Hetzen übergeht.",
            uebung_template=(
                "\nSchritt 1: Sprich nur den zweiten Teil deiner Präsentation, "
                "bewusst im selben Tempo wie den Anfang.\n"
                "Schritt 2: Plane an einer Stelle im zweiten Teil eine bewusste "
                "Pause ein, um das Tempo zu bremsen.\n"
                "Schritt 3: Sprich die komplette Präsentation am Stück und "
                "achte gezielt auf gleichbleibendes Tempo."
            ),
        ),
    ]

    # Echter Vorher/Nachher-Tempovergleich statt reiner Zeit-Häufung — die
    # generische "trend"-Achse der Engine prüft nur WANN Sätze vorkommen,
    # nicht OB das Tempo selbst steigt/fällt. Das wird hier separat bestimmt
    # und als eigenes Merkmal in die Befunde geschrieben.
    if len(valide_saetze) >= 4:
        mitte_idx = len(valide_saetze) // 2
        tempo_erste_haelfte = sum(s["tempo"] for s in valide_saetze[:mitte_idx]) / mitte_idx
        tempo_zweite_haelfte = sum(s["tempo"] for s in valide_saetze[mitte_idx:]) / (len(valide_saetze) - mitte_idx)
        diff = tempo_zweite_haelfte - tempo_erste_haelfte
        richtung = "steigt" if diff > 0.3 else ("faellt" if diff < -0.3 else "gleich")
        for b in d1_befunde:
            b["zeittrend_richtung"] = richtung
        for achse in D1_ACHSEN:
            if achse.name == "zeittrend_tempo":
                if richtung == "faellt":
                    achse.merkmal_wert = "faellt"
                    achse.befund_template = "Von der ersten zur zweiten Hälfte deiner Aufnahme wird dein Tempo spürbar langsamer."
                    achse.ursache_template = "Das kann bedeuten, dass die anfängliche Energie zum Ende hin nachlässt."
                    achse.uebung_template = "Plane bewusst einen energischeren Moment für den zweiten Teil ein."
    else:
        for b in d1_befunde:
            b["zeittrend_richtung"] = "gleich"

    tipp_d1 = ru.erkenne_muster_v2(
        d1_befunde, D1_ACHSEN, gesamt_dauer_ms=analyse.get('dauer_s', 0) * 1000, max_tipps=1,
        fall_a_text=[
            "In dieser Aufnahme gab es keinen einzigen Satz, der lang genug war "
            "(mindestens 1 Sekunde, mindestens 3 Silben), um dein Tempo verlässlich "
            "zu messen. Bei einer längeren Aufnahme mit vollständigeren Sätzen wird "
            "diese Dimension aussagekräftig.",
        ],
        einzelfund_template=(
            "Bei '{satz}' sprichst du mit {tempo:.1f} Silben pro Sekunde — "
            "das ist {einordnung_text}.\n"
            "Schritt 1: Sprich nur diesen Satz, bewusst {gegenrichtung}, "
            "5x hintereinander.\n"
            "Schritt 2: Sprich den Satz davor und diesen Satz zusammen, "
            "3x im neuen Tempo.\n"
            "Schritt 3: Nimm dich auf und vergleiche mit dem Original — "
            "ist der Unterschied hörbar?"
        ),
        fall_c_einleitung="Deine Sätze schwanken im Tempo, aber ohne erkennbares Muster — das ist grundsätzlich unauffällig:",
    )

    z += ru.dimension_block_detail(
        "Gesamttempo", 40, analyse['punkte_tempo'],
        was_gemessen=["Deine durchschnittliche Sprechgeschwindigkeit über die",
                      "gesamte Präsentation, gemessen in Silben pro Sekunde."],
        warum=[f"Gemessen: {mit_wpm(analyse['gesamttempo'])}.",
               "Faustregel: 4.5–5.5 Silben/Sek (~108–132 Wörter/Min) gilt als",
               "gut verständlich. Langsamer wirkt zäh, schneller überfordert.",
               f"Bei dir: {TEMPO_FEEDBACK[analyse['bewertung_tempo']]}"],
        fundstellen=fundstellen_d1,
        tipp=tipp_d1,
    )

    # ── D2 Variation ──────────────────────────────────────────────────────────
    z += ru.dimension_block_detail(
        "Tempo-Variation", 30, analyse['punkte_variation'],
        was_gemessen=["Wie stark dein Sprechtempo zwischen einzelnen Sätzen",
                      "schwankt. Zu gleichförmig wirkt monoton, zu unruhig wirkt",
                      "hektisch."],
        warum=[f"Schwankung (Streuung/Mittelwert): {analyse['variation_cv']:.2f}.",
               "Faustregel: 0.20–0.35 gilt als natürliche Abwechslung.",
               f"Bei dir: {VARIATION_FEEDBACK.get(analyse['bewertung_variation'], 'Zu wenige valide Sätze für eine Bewertung.')}"],
        fundstellen=[],
        tipp=["Wechsle bewusst zwischen etwas schnelleren Passagen (Fakten,",
              "Aufzählungen) und langsameren Passagen (wichtige Aussagen)."],
    )

    # ── D3 Kernbotschaften ────────────────────────────────────────────────────
    fundstellen_d3 = []
    if analyse['kern_daten_vorhanden']:
        for s in analyse.get('kern_saetze', [])[:5]:
            fundstellen_d3.append(ru.fundstelle_zeile(s['start_s'] * 1000, s['text'], mit_wpm(s['tempo'])))
    if not fundstellen_d3:
        fundstellen_d3 = ["  Keine Kernbotschaften erkannt — keine Fundstellen."]

    warum_d3 = []
    if analyse['kern_daten_vorhanden']:
        v = analyse['verlangsamung_prozent']
        warum_d3.append(f"Bei Kernaussagen sprichst du {mit_wpm(analyse['tempo_kern'])},")
        warum_d3.append(f"im Rest {mit_wpm(analyse['tempo_neben'])}.")
        if v is not None and v < 0:
            warum_d3.append(f"Das heißt: du bist bei Kernaussagen {abs(v):.0f}% SCHNELLER,")
            warum_d3.append("statt langsamer — das schwächt ihre Wirkung.")
        else:
            warum_d3.append(f"Das ist eine Verlangsamung von {v:.0f}%.")
        warum_d3.append("Faustregel: ab 10% Verlangsamung wirkt eine Kernaussage bewusst betont.")
        warum_d3.append(f"Bei dir: {KERNBOTSCHAFT_FEEDBACK[analyse['bewertung_kernbotschaften']]}")
    else:
        warum_d3.append("Es wurden keine Kernbotschaften oder keine Nebensätze gefunden,")
        warum_d3.append("darum konnte diese Dimension nicht bewertet werden.")

    d3_befunde = []
    if analyse['kern_daten_vorhanden']:
        tempo_neben_ref = analyse['tempo_neben']
        for s in analyse.get('kern_saetze', []):
            diff_prozent = (s['tempo'] - tempo_neben_ref) / tempo_neben_ref * 100 if tempo_neben_ref else 0
            d3_befunde.append({
                "satz": s['text'],
                "tempo": s['tempo'],
                "tempo_rest": tempo_neben_ref,
                "richtung": "schneller" if diff_prozent > 0 else "langsamer",
                "differenz": diff_prozent,
                "zeit_str": ru.video_zeit(s['start_s'] * 1000),
                "bewertungssatz": (
                    "Das schwächt die Wirkung dieser Aussage." if diff_prozent > 0
                    else "Das betont die Aussage gut."
                ),
                "start_ms": s['start_s'] * 1000,
            })

    D3_ACHSEN = [
        ru.Achse(
            "kernaussage_falsch_schnell", "anteil", prioritaet=1,
            merkmal_key="richtung", merkmal_wert="schneller", min_evidenz=2, schwelle=0.3,
            befund_template="Bei {anteil:.0%} deiner Kernaussagen sprichst du SCHNELLER statt langsamer als im übrigen Text.",
            ursache_template="Das schwächt genau die Stellen, die hervorstechen sollen, weil Verlangsamung eines der stärksten hörbaren Signale für 'das ist wichtig' ist.",
            uebung_template=(
                "\nSchritt 1: Markiere alle deine Kernaussagen im Text.\n"
                "Schritt 2: Sprich jede einzeln, bewusst 20% langsamer als "
                "den Rest, 3x pro Kernaussage.\n"
                "Schritt 3: Sprich die ganze Präsentation durch — achte "
                "gezielt nur auf diese verlangsamten Stellen."
            ),
        ),
    ]

    tipp_d3 = ru.erkenne_muster_v2(
        d3_befunde, D3_ACHSEN, max_tipps=1,
        fall_a_text=[
            "In der Inhaltsanalyse wurden keine Kernbotschaften gefunden, an denen "
            "sich diese Dimension prüfen ließe — sie konnte darum nicht bewertet werden.",
        ],
        einzelfund_template=(
            "Bei deiner Kernaussage '{satz}' sprichst du mit {tempo:.1f} "
            "Silben pro Sekunde — {richtung} als im Rest ({tempo_rest:.1f}). "
            "{bewertungssatz}\n"
            "Schritt 1: Sprich nur diese Kernaussage, bewusst langsamer als "
            "den Rest, 5x hintereinander.\n"
            "Schritt 2: Sprich den Satz davor normal, dann die Kernaussage "
            "bewusst verlangsamt — 3x den Übergang üben.\n"
            "Schritt 3: Nimm die ganze Passage auf und höre, ob die "
            "Kernaussage jetzt hörbar heraussticht."
        ),
        fall_c_einleitung="Deine Kernaussagen sind uneinheitlich betont — bei manchen verlangsamst du, bei anderen nicht:",
    )

    z += ru.dimension_block_detail(
        "Kernbotschaften", 30, analyse['punkte_kernbotschaften'],
        was_gemessen=["Ob du bei deinen wichtigsten Aussagen bewusst langsamer",
                      "sprichst als im Rest — das hebt sie hervor."],
        warum=warum_d3,
        fundstellen=fundstellen_d3,
        tipp=tipp_d3,
    )

    # ── Struktur-Analyse ──────────────────────────────────────────────────────
    if analyse.get('struktur_analyse'):
        z.append(ru.SEP2)
        z.append("  4. STRUKTUR-ANALYSE (zur Orientierung, nicht im Score)")
        z.append(ru.SEP2)
        z.append("  So verändert sich dein Tempo über die Abschnitte deiner")
        z.append("  Präsentation hinweg:")
        z.append("")
        for seg in analyse['struktur_analyse']:
            status = "✅ wie erwartet" if seg['erwartung_erfuellt'] else "⚠ weicht ab"
            z.append(f"  {seg['typ']:<15} {mit_wpm(seg['tempo']):<28} "
                     f"{seg['abweichung_prozent']:>+6.1f} %  {status}")
        z.append("")

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  5. HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  Tempo = Silben geteilt durch reine Sprechzeit (Pausen zählen nicht")
    z.append("  mit). WPM-Werte sind eine Umrechnung zur besseren Einordnung.")
    z.append("")
    z.append("  Zu langsam    | Optimal        | Zu schnell")
    z.append("  " + "-" * 50)
    z.append("  < 3.5 Sil/Sek | 4.5–5.5 Sil/Sek | > 6.5 Sil/Sek")
    z.append("  (<84 WPM)     | (108–132 WPM)   | (>156 WPM)")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w', encoding='utf-8') as f:
        f.write('\n'.join(z))


def erstelle_json_output(analyse, saetze_mit_tempo, pfad):
    """Erstellt die JSON-Ausgabe für nachgelagerte Scripts / gesamtscore.py."""
    output = {
        'gesamttempo_silben_sek': round(analyse['gesamttempo'], 2),
        'gesamttempo_wpm': analyse['gesamttempo_wpm'],
        'gesamtscore': analyse['gesamtscore'],
        'teilscores': {
            'tempo': analyse['punkte_tempo'],
            'variation': analyse['punkte_variation'],
            'kernbotschaften': analyse['punkte_kernbotschaften'],
        },
        'bewertungen': {
            'tempo': analyse['bewertung_tempo'],
            'variation': analyse['bewertung_variation'],
            'kernbotschaften': analyse['bewertung_kernbotschaften'],
        },
        'variation': {
            'standardabweichung': analyse['variation_std'],
            'variationskoeffizient': analyse['variation_cv'],
        },
        'kernbotschaften': {
            'tempo_kern': analyse.get('tempo_kern'),
            'tempo_neben': analyse.get('tempo_neben'),
            'verlangsamung_prozent': analyse.get('verlangsamung_prozent'),
        },
        'struktur': analyse.get('struktur_analyse', []),
        'saetze': [
            {
                'satz_id': s['satz_id'],
                'tempo': round(s['tempo'], 2),
                'silben': s['silben'],
                'sprechzeit_s': s['sprechzeit_s'],
                'valid': s['valid'],
            }
            for s in saetze_mit_tempo
        ],
    }
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ============================================================================
# FILE-PICKER
# ============================================================================

def waehle_datei(titel, dateitypen):
    root = tk.Tk()
    root.withdraw()
    pfad = filedialog.askopenfilename(title=titel, filetypes=dateitypen)
    root.destroy()
    return pfad


# ============================================================================
# MAIN
# ============================================================================

def main():
    print()
    print("=" * 70)
    print("SPRECHTEMPO-ANALYSE")
    print("=" * 70)
    print()

    # -------------------------------------------------------------
    # Schritt 1: Transkript wählen
    # -------------------------------------------------------------
    print("Schritt 1/9: Transkript waehlen...")
    if len(sys.argv) > 1:
        transkript_pfad = Path(sys.argv[1])
    else:
        pfad_str = waehle_datei(
            "Transkript waehlen (.txt)",
            [("Textdateien", "*.txt"), ("Alle Dateien", "*.*")],
        )
        if not pfad_str:
            print("  [x] Kein Transkript gewaehlt. Abbruch.")
            return
        transkript_pfad = Path(pfad_str)
    if not transkript_pfad.exists():
        print(f"  [x] Datei nicht gefunden: {transkript_pfad}")
        return
    print(f"  [OK] Transkript: {transkript_pfad.name}")

    # -------------------------------------------------------------
    # Schritt 2: Transkript einlesen + Silben zählen
    # -------------------------------------------------------------
    print("Schritt 2/9: Transkript einlesen und Silben zaehlen...")
    try:
        woerter = lade_transkript(transkript_pfad)
    except Exception as e:
        print(f"  [x] Fehler beim Einlesen: {e}")
        return
    silben_gesamt = sum(w['silben'] for w in woerter)
    print(f"  [OK] {len(woerter)} Wörter, {silben_gesamt} Silben insgesamt")

    # -------------------------------------------------------------
    # Schritt 3: Inhalt-Analyse JSON laden
    # -------------------------------------------------------------
    print("Schritt 3/9: Inhalt-Analyse JSON laden...")
    json_pfad = JSON_INPUT_PFAD
    if not json_pfad.exists():
        print(f"  [!] Nicht gefunden am Standardort: {json_pfad}")
        print("      Bitte inhalt_analyse_output.json manuell waehlen...")
        pfad_str = waehle_datei(
            "inhalt_analyse_output.json waehlen",
            [("JSON-Dateien", "*.json"), ("Alle Dateien", "*.*")],
        )
        if not pfad_str:
            print("  [x] Keine JSON gewaehlt. Abbruch.")
            return
        json_pfad = Path(pfad_str)
    try:
        inhalt = lade_inhalt_json(json_pfad)
    except Exception as e:
        print(f"  [x] Fehler beim Laden: {e}")
        return
    n_saetze = len(inhalt.get('satzgrenzen', []))
    n_kern = len(inhalt.get('kernbotschaften', []))
    n_struktur = len(inhalt.get('struktur', []))
    print(f"  [OK] {n_saetze} Sätze, {n_kern} Kernbotschaften, {n_struktur} Struktur-Segmente")

    # -------------------------------------------------------------
    # Schritt 4: Tempo pro Satz berechnen
    # -------------------------------------------------------------
    print("Schritt 4/9: Tempo pro Satz berechnen...")
    saetze_mit_tempo = berechne_tempo_pro_satz(woerter, inhalt.get('satzgrenzen', []))
    valide_saetze = [s for s in saetze_mit_tempo if s['valid']]
    print(f"  [OK] {len(saetze_mit_tempo)} Sätze analysiert ({len(valide_saetze)} valide)")

    # -------------------------------------------------------------
    # Schritt 5: Gesamttempo berechnen
    # -------------------------------------------------------------
    print("Schritt 5/9: Gesamttempo berechnen...")
    gesamttempo = berechne_gesamttempo(woerter)
    gesamttempo_wpm = silben_zu_wpm(gesamttempo)
    print(f"  [OK] {gesamttempo:.2f} Silben/Sek (~{gesamttempo_wpm} WPM)")

    # -------------------------------------------------------------
    # Schritt 6: Variation berechnen
    # -------------------------------------------------------------
    print("Schritt 6/9: Variation berechnen...")
    if len(valide_saetze) >= 2:
        tempi = [s['tempo'] for s in valide_saetze]
        std = statistics.stdev(tempi)
        mittelwert = statistics.mean(tempi)
        cv = std / mittelwert if mittelwert > 0 else 0
        variation_bewertbar = True
    else:
        std = 0.0
        cv = 0.0
        variation_bewertbar = False
    print(f"  [OK] Std: {std:.3f}, CV: {cv:.3f}")

    # -------------------------------------------------------------
    # Schritt 7: Kernbotschaften analysieren
    # -------------------------------------------------------------
    print("Schritt 7/9: Kernbotschaften analysieren...")
    kern_ids = {k['satz_id'] for k in inhalt.get('kernbotschaften', [])}
    kern_saetze = [s for s in valide_saetze if s['satz_id'] in kern_ids]
    neben_saetze = [s for s in valide_saetze if s['satz_id'] not in kern_ids]
    kern_daten_vorhanden = len(kern_saetze) > 0 and len(neben_saetze) > 0
    if kern_daten_vorhanden:
        tempo_kern = statistics.mean(s['tempo'] for s in kern_saetze)
        tempo_neben = statistics.mean(s['tempo'] for s in neben_saetze)
        verlangsamung = (tempo_neben - tempo_kern) / tempo_neben if tempo_neben > 0 else 0
    else:
        tempo_kern = None
        tempo_neben = None
        verlangsamung = 0
    print(f"  [OK] {len(kern_saetze)} Kernbotschaften, {len(neben_saetze)} Nebensätze")

    # -------------------------------------------------------------
    # Schritt 8: Bewertung und Score
    # -------------------------------------------------------------
    print("Schritt 8/9: Bewertung und Score berechnen...")
    bewertung_tempo, punkte_tempo = bewerte_gesamttempo(gesamttempo)

    if variation_bewertbar:
        bewertung_variation, punkte_variation = bewerte_variation(cv)
    else:
        bewertung_variation, punkte_variation = 'nicht_bewertbar', 50

    if kern_daten_vorhanden:
        bewertung_kern, punkte_kern = bewerte_kernbotschaften(verlangsamung)
    else:
        bewertung_kern, punkte_kern = 'nicht_bewertbar', 50

    gesamtscore = berechne_gesamtscore(punkte_tempo, punkte_variation, punkte_kern)
    struktur_analyse = analysiere_struktur(
        saetze_mit_tempo, inhalt.get('struktur', []), gesamttempo
    )
    print(f"  [OK] Gesamtscore: {gesamtscore}/100")

    # Analyse-Objekt zusammenbauen
    analyse = {
        'transkript_datei': transkript_pfad.name,
        'dauer_s': (woerter[-1]['ende_s'] - woerter[0]['start_s']) if woerter else 0,
        'gesamttempo': gesamttempo,
        'gesamttempo_wpm': gesamttempo_wpm,
        'gesamtscore': gesamtscore,
        'punkte_tempo': punkte_tempo,
        'punkte_variation': punkte_variation,
        'punkte_kernbotschaften': punkte_kern,
        'bewertung_tempo': bewertung_tempo,
        'bewertung_variation': bewertung_variation,
        'bewertung_kernbotschaften': bewertung_kern,
        'variation_std': round(std, 3),
        'variation_cv': round(cv, 3),
        'kern_daten_vorhanden': kern_daten_vorhanden,
        'tempo_kern': round(tempo_kern, 2) if tempo_kern is not None else None,
        'tempo_neben': round(tempo_neben, 2) if tempo_neben is not None else None,
        'verlangsamung_prozent': round(verlangsamung * 100, 1) if kern_daten_vorhanden else None,
        'struktur_analyse': struktur_analyse,
        'saetze_mit_tempo': saetze_mit_tempo,
        'kern_saetze': kern_saetze,
        'neben_saetze': neben_saetze,
    }

    # -------------------------------------------------------------
    # Schritt 9: Output schreiben
    # -------------------------------------------------------------
    print("Schritt 9/9: Output schreiben...")
    zeitstempel = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_kurz_pfad = REPORT_ORDNER / f"sprechtempo_kurz_{zeitstempel}.txt"
    report_detail_pfad = REPORT_ORDNER / f"sprechtempo_detail_{zeitstempel}.txt"
    erstelle_kurz_report(analyse, report_kurz_pfad)
    erstelle_detail_report(analyse, report_detail_pfad)
    print(f"  [OK] Kurz-Report:      {report_kurz_pfad}")
    print(f"  [OK] Detail-Report:    {report_detail_pfad}")
    erstelle_json_output(analyse, saetze_mit_tempo, JSON_OUTPUT_PFAD)
    print(f"  [OK] JSON-Intermediate: {JSON_OUTPUT_PFAD}")

    print()
    print("=" * 70)
    print(f"ANALYSE ABGESCHLOSSEN - Gesamtscore: {gesamtscore}/100")
    print("=" * 70)


if __name__ == "__main__":
    main()
