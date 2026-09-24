"""report_utils.py
==============================================================================
Gemeinsame Bausteine für alle Modul-Reports (Pausen, Sprechtempo, Füllwörter,
Sprechfluss, Lautstärke, Pitch, Emotion, Inhalt, Video).

Jedes Modul erzeugt ab jetzt ZWEI Report-Dateien:
    <modul>_kurz_<timestamp>.txt    — Übersicht: Score, 1-Satz-Bewertung je
                                       Dimension, Top-Empfehlungen
    <modul>_detail_<timestamp>.txt  — alles: Formeln, Referenzwerte, jede
                                       Fundstelle im Original-Satz, Begründung
                                       je Punktzahl, Verbesserungsvorschlag
                                       je Fundstelle, Inhaltsverzeichnis

Dieses Modul enthält nur Formatierung — keine Score-Logik. Die bleibt
unverändert in den einzelnen Analyse-Skripten.
==============================================================================
"""

from __future__ import annotations
import re
from typing import List, Tuple, Optional, Any
from collections import Counter


SEP = "=" * 70
SEP2 = "-" * 70

# Unterhalb dieser Aufnahmedauer (Sekunden) sind Pro-Minute-Hochrechnungen
# statistisch unsicher — ein einzelnes Ereignis kann den Wert stark verzerren.
MIN_ZUVERLAESSIGE_DAUER_S = 60


def ampel(score: float) -> str:
    if score is None:
        return "—"
    if score >= 75:
        return "✅"
    if score >= 50:
        return "🟡"
    return "❌"


def balken(score: float, breite: int = 20) -> str:
    if score is None:
        return "░" * breite
    gefuellt = max(0, min(breite, round(score / (100 / breite))))
    return "█" * gefuellt + "░" * (breite - gefuellt)


def kurz_header(titel: str, quelle: str, dauer_str: str, extra: Optional[str] = None) -> List[str]:
    """Header für den Kurz-Report."""
    z = [SEP, f"  {titel} — KURZFASSUNG", SEP,
         f"  Quelle:  {quelle}     Dauer: {dauer_str}"]
    if extra:
        z.append(f"  {extra}")
    z.append("")
    return z


def detail_header(titel: str, quelle: str, dauer_str: str, extra: Optional[str] = None) -> List[str]:
    """Header für den Detail-Report."""
    z = [SEP, f"  {titel} — DETAILANSICHT", SEP,
         f"  Quelle:  {quelle}     Dauer: {dauer_str}"]
    if extra:
        z.append(f"  {extra}")
    z.append("")
    return z


def gesamtergebnis_block(score: int, satz_gut: str, satz_mittel: str, satz_schlecht: str) -> List[str]:
    z = [SEP2, "  GESAMTERGEBNIS", SEP2,
         f"  {score} / 100   [{balken(score)}]   {ampel(score)}"]
    if score >= 75:
        z.append(f"  {satz_gut}")
    elif score >= 50:
        z.append(f"  {satz_mittel}")
    else:
        z.append(f"  {satz_schlecht}")
    z.append("")
    return z


def dimension_zeile_kurz(name: str, gewicht_pct: int, score: int, kurzsatz: str) -> List[str]:
    """Eine Zeile pro Dimension in der Kurzfassung — Name, Score, 1 Satz."""
    return [f"  {ampel(score)} {name:<32s} {score:>3d}/100   {kurzsatz}"]


def build_toc(abschnitte: List[str]) -> List[str]:
    """Erstellt ein Inhaltsverzeichnis für den Detail-Report."""
    z = [SEP2, "  INHALTSVERZEICHNIS", SEP2]
    for i, name in enumerate(abschnitte, 1):
        z.append(f"  {i}. {name}")
    z.append("")
    return z


def kleine_stichprobe_warnung(dauer_s: float) -> List[str]:
    """
    Warnhinweis, wenn die Aufnahme so kurz ist, dass Pro-Minute-Hochrechnungen
    unzuverlässig werden (ein einzelnes Ereignis verzerrt den Wert stark).
    """
    if dauer_s >= MIN_ZUVERLAESSIGE_DAUER_S:
        return []
    return [
        "  ⚠ Hinweis zur Datenbasis:",
        f"  Diese Aufnahme ist nur {dauer_s:.0f} Sekunden lang. Werte, die auf",
        "  eine ganze Minute hochgerechnet werden, können sich bei einer",
        "  längeren Aufnahme deutlich ändern — schon ein einzelnes Ereignis",
        "  wirkt sich hier stark aus. Für eine zuverlässige Bewertung sind",
        f"  mindestens {MIN_ZUVERLAESSIGE_DAUER_S} Sekunden empfohlen.",
        "",
    ]


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
    'präsentation': 4, 'präsentationen': 5,
    'praesentation': 4, 'praesentationen': 5,
    'produktion': 3, 'produktionen': 4,
    'funktion': 3, 'funktionen': 4,
    'position': 3, 'positionen': 4,
    'diskussion': 3, 'diskussionen': 4,
    'motivation': 4, 'motivationen': 5,
    'information': 4, 'informationen': 5,
    'organisation': 5, 'organisationen': 6,
    'baby': 2, 'babys': 2, 'party': 2, 'partys': 2,
    'story': 2, 'stories': 2, 'hobby': 2, 'hobbys': 2,
    'city': 2, 'jury': 2, 'company': 3,
}


def zaehle_silben(wort: str) -> int:
    """
    Zählt Silben in einem deutschen Wort — genaue Version mit Ausnahmenliste,
    Diphthong- und y-Sonderregeln. Zentral hier, damit alle 9 Module dieselbe,
    genaue Zählung nutzen statt jeweils eine eigene, gröbere Schätzung.

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
    wort_clean = re.sub(r'[^a-zäöüßy]', '', wort.lower())
    if not wort_clean:
        return 0

    if wort_clean in SILBEN_AUSNAHMEN:
        return SILBEN_AUSNAHMEN[wort_clean]

    diphthonge = {'ai', 'ei', 'au', 'äu', 'eu', 'ie', 'ui', 'ay', 'ey'}
    vokale_ohne_y = set('aeiouäöü')

    def ist_vokal(pos):
        c = wort_clean[pos]
        if c in vokale_ohne_y:
            return True
        if c == 'y':
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
            zwei_zeichen = wort_clean[i:i + 2]
            if i + 1 < len(wort_clean) and zwei_zeichen in diphthonge:
                silben += 1
                i += 2
            else:
                silben += 1
                while i + 1 < len(wort_clean) and wort_clean[i + 1] == wort_clean[i]:
                    i += 1
                i += 1
        else:
            i += 1

    return max(1, silben)


def wrap_text(text: str, breite: int = 66) -> List[str]:
    """Bricht einen langen String an Wortgrenzen um — für Textbausteine,
    die als ein durchgehender String vorliegen statt vorformatiert."""
    woerter = text.split()
    zeilen, aktuelle = [], ""
    for w in woerter:
        kandidat = f"{aktuelle} {w}".strip()
        if len(kandidat) > breite and aktuelle:
            zeilen.append(aktuelle)
            aktuelle = w
        else:
            aktuelle = kandidat
    if aktuelle:
        zeilen.append(aktuelle)
    return zeilen


def dimension_block_detail(
    name: str,
    gewicht_pct: Optional[int],
    score: int,
    was_gemessen: List[str],
    warum: List[str],
    fundstellen: List[str],
    tipp: List[str],
) -> List[str]:
    """
    Ein vollständiger Dimensions-Block für den Detail-Report:
    Was gemessen wird / Warum diese Punktzahl / Wo genau / Verbesserungsvorschlag
    """
    label = f"({gewicht_pct}% Gewichtung)" if gewicht_pct else ""
    kopf = f"  {ampel(score)} {name} {label}".rstrip()
    score_str = f"{score}/100"
    luecke = max(2, 68 - len(kopf) - len(score_str))
    z = [kopf + (" " * luecke) + score_str, "  " + "-" * 66]
    z.append("  Was gemessen wird:")
    # Erlaubt sowohl vorformatierte Zeilenlisten als auch einen langen String
    was_zeilen = []
    for zeile in was_gemessen:
        was_zeilen += wrap_text(zeile) if len(zeile) > 66 else [zeile]
    z += [f"    {zeile}" for zeile in was_zeilen]
    z.append("")
    z.append("  Warum diese Punktzahl:")
    warum_zeilen = []
    for zeile in warum:
        warum_zeilen += wrap_text(zeile) if len(zeile) > 66 else [zeile]
    z += [f"    {zeile}" for zeile in warum_zeilen]
    z.append("")
    if fundstellen:
        z.append("  Wo genau:")
        z += [f"    {zeile}" for zeile in fundstellen]
        z.append("")
    if tipp:
        z.append("  💡 Verbesserungsvorschlag:")
        tipp_zeilen = []
        for eintrag in tipp:
            # Ein Tipp kann echte Zeilenumbrüche enthalten, um Schritte
            # (Schritt 1/2/3...) klar zu trennen, statt als ein langer
            # Fließtext zu erscheinen. Jeder Teil wird einzeln umgebrochen.
            for teil in eintrag.split("\n"):
                if not teil.strip():
                    tipp_zeilen.append("")
                    continue
                tipp_zeilen += wrap_text(teil) if len(teil) > 66 else [teil]
        z += [f"    {zeile}" if zeile else "" for zeile in tipp_zeilen]
    z.append("")
    return z


def markiere_stelle_im_satz(satz_text: str, gesuchtes_wort: str, marker: str = "⚠") -> str:
    """
    Markiert die Stelle in einem Satz, an der ein Wort vorkommt, z. B.:
    "Hallo [zusammen,] ⚠ ich bin der Robin."
    Fällt auf den unmarkierten Satz zurück, wenn das Wort nicht gefunden wird.
    """
    if not satz_text or not gesuchtes_wort:
        return satz_text or ""
    woerter = satz_text.split()
    for i, w in enumerate(woerter):
        if w.strip(".,!?").lower() == gesuchtes_wort.strip(".,!?").lower():
            woerter[i] = f"[{w}] {marker}"
            break
    return " ".join(woerter)


def video_zeit(ms: float) -> str:
    """
    Formatiert Millisekunden als einfache Video-Position (M:SS), ohne
    Millisekunden-Nachkommastellen — leichter lesbar als der interne
    Zeitstempel, gedacht zum direkten Nachschauen im Video.
    """
    ms = max(0, ms)
    total_sec = int(round(ms / 1000))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fundstelle_zeile(start_ms: float, satz_markiert: str, zusatz: str = "") -> str:
    """
    Einheitliche Fundstellen-Zeile: sagt explizit, wo im VIDEO man das
    nachschauen kann, nicht nur wann im Transkript.
    """
    zusatz_str = f"  ({zusatz})" if zusatz else ""
    return f"  ▶ Im Video bei {video_zeit(start_ms)} min: \"{satz_markiert}\"{zusatz_str}"


def fundstelle_mit_einzeltipp(start_ms: float, satz_markiert: str, tipp_text: str,
                                zusatz: str = "") -> List[str]:
    """
    Wie fundstelle_zeile, aber mit einer zweiten Zeile direkt darunter:
    ein Tipp, der sich NUR auf diese eine Fundstelle bezieht (nutzt die
    echten Wörter/den echten Ort dieser Stelle, kein aggregiertes Muster).
    Wird für JEDE Fundstelle erzeugt, unabhängig davon ob genug Evidenz
    für ein Muster vorliegt — deckt damit auch den Einzelfall ab.
    """
    kopf = fundstelle_zeile(start_ms, satz_markiert, zusatz)
    tipp_umbrochen = wrap_text(tipp_text, breite=64) if len(tipp_text) > 64 else [tipp_text]
    zeilen = [kopf]
    zeilen.append(f"    💡 {tipp_umbrochen[0]}")
    zeilen += [f"       {z}" for z in tipp_umbrochen[1:]]
    return zeilen


# ============================================================================
# MUSTER-ERKENNUNG
# ============================================================================
# Wiederverwendbares Grundgerüst, damit "Verbesserungsvorschlag"-Texte aus
# den tatsächlichen Fundstellen berechnet werden, statt aus einer festen
# Schablone ausgewählt zu werden. Ein Muster wird nur ausgegeben, wenn genug
# Beweise dafür da sind (MIN_EVIDENZ) — sonst bleibt es beim generischen
# Fallback-Tipp, statt bei wenig Daten ein Muster zu erfinden.

MIN_EVIDENZ = 2  # ein einzelner Treffer reicht nicht als "Muster"


def muster_haeufung_an_position(befunde: List[dict], position_key: str,
                                  position_wert, label: str,
                                  schwelle: float = 0.5) -> Optional[str]:
    """
    Prüft, ob ein bestimmter Anteil der Befunde an einer bestimmten Position
    auftritt (z.B. 'satz_position' == 'anfang'). Gibt nur einen Text zurück,
    wenn genug Fälle vorliegen UND der Anteil über der Schwelle liegt.
    """
    if len(befunde) < MIN_EVIDENZ:
        return None
    treffer = [b for b in befunde if b.get(position_key) == position_wert]
    anteil = len(treffer) / len(befunde)
    if anteil >= schwelle:
        return (f"Bei dir sitzen {anteil:.0%} deiner Fundstellen {label} — "
                f"das ist kein Zufall, sondern ein wiederkehrendes Muster.")
    return None


def muster_wiederkehrendes_wort(befunde: List[dict], wort_key: str = "wort",
                                  min_anzahl: int = 2) -> Optional[str]:
    """Prüft, ob ein einzelnes Wort mehrfach als Fundstelle auftaucht."""
    if len(befunde) < MIN_EVIDENZ:
        return None
    from collections import Counter
    zaehler = Counter(b.get(wort_key, "").strip(".,!?").lower() for b in befunde if b.get(wort_key))
    if not zaehler:
        return None
    wort, anzahl = zaehler.most_common(1)[0]
    if anzahl >= min_anzahl:
        return (f"Das Wort '{wort}' taucht bei dir {anzahl}x als Stolperstelle auf — "
                f"übe gezielt genau dieses Wort, isoliert und mehrmals laut.")
    return None


def muster_trend_ueber_zeit(befunde: List[dict], zeit_key: str,
                              gesamt_dauer_ms: float) -> Optional[str]:
    """
    Prüft, ob sich Fundstellen in der ersten oder zweiten Hälfte der
    Aufnahme häufen (z.B. Nervosität am Anfang, die sich legt).
    """
    if len(befunde) < MIN_EVIDENZ or gesamt_dauer_ms <= 0:
        return None
    mitte = gesamt_dauer_ms / 2
    erste_haelfte = sum(1 for b in befunde if b.get(zeit_key, 0) < mitte)
    anteil = erste_haelfte / len(befunde)
    if anteil >= 0.7:
        return ("Auffällig viele Fundstellen liegen in der ERSTEN Hälfte deiner "
                "Aufnahme — das deutet auf Nervosität am Anfang hin, die sich mit "
                "der Zeit legt. Ein kurzes Warm-up vor der eigentlichen Aufnahme "
                "könnte helfen.")
    elif anteil <= 0.3:
        return ("Auffällig viele Fundstellen liegen in der ZWEITEN Hälfte deiner "
                "Aufnahme — das kann auf nachlassende Konzentration oder weniger "
                "gut vorbereiteten Inhalt gegen Ende hindeuten.")
    return None


def erkenne_muster(befunde: List[dict], pruefungen: List) -> List[str]:
    """
    Führt eine Liste von Muster-Prüfungen aus und gibt nur die Texte zurück,
    bei denen tatsächlich genug Evidenz gefunden wurde.
    """
    ergebnisse = []
    for pruefung in pruefungen:
        text = pruefung()
        if text:
            ergebnisse.append(text)
    return ergebnisse


# ============================================================================
# MUSTER-ENGINE V2 — konfigurationsbasiert, mit Priorisierung
# ============================================================================
#
# Ablauf (siehe Konzept-Dokumentation):
#   1. Merkmalsextraktion: jede Fundstelle -> dict mit Achsen-Werten
#      (modulspezifisch, außerhalb der Engine)
#   2. Evidenzprüfung pro Achse (hier, generisch, 5 Achsen-Typen)
#   3. Priorisierung: bei mehreren zutreffenden Mustern nur die
#      wichtigsten MAX_TIPPS ausgeben
#   4. Textbaustein-Zusammensetzung: Befund + Ursache + Übung
#
# Ein Muster wird NIE erfunden, wenn zu wenig Fundstellen vorliegen —
# das ist der zentrale Sicherheitsmechanismus gegen falsche Schlüsse bei
# kurzen Aufnahmen.

from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class Achse:
    """
    Definiert eine Merkmalsachse, gegen die Fundstellen geprüft werden.

    art:
      "anteil"      — Mehrheits-Muster: mind. `min_evidenz` Fundstellen
                       insgesamt, UND der gewichtete Anteil mit
                       merkmal(f)==merkmal_wert liegt über `schwelle`.
      "praesenz"    — Präsenz-Muster: mind. `min_evidenz` Fundstellen mit
                       merkmal(f)==merkmal_wert, unabhängig vom Anteil.
                       Für seltene, aber wichtige Fälle (z.B. "an der
                       Kernaussage"), wo schon 1 Treffer erwähnenswert ist.
      "wiederholung"— derselbe Wert (meist ein Wort) kommt >= min_anzahl
                       mal vor, unabhängig von der Gesamtzahl.
      "cluster"     — mind. `cluster_min_groesse` Fundstellen liegen
                       zeitlich enger als `cluster_fenster_ms` beieinander.
      "trend"       — Fundstellen häufen sich in der ersten oder zweiten
                       Hälfte der Aufnahme (Schwelle 0.7 / 0.3, fix).
    """
    name: str
    art: str
    prioritaet: int                      # 1 = wichtigstes Muster
    merkmal_key: str = ""
    merkmal_wert: Any = True
    min_evidenz: int = 2
    schwelle: float = 0.5
    min_anzahl: int = 2                  # für "wiederholung"
    cluster_fenster_ms: float = 2000
    cluster_min_groesse: int = 2
    zeit_key: str = "start_ms"
    gewicht_key: str = "gewicht"         # optionales Gewichtungsfeld je Fundstelle
    befund_template: str = ""            # z.B. "Bei {anteil:.0%} deiner Fundstellen ({n}x) …"
    ursache_template: str = ""
    uebung_template: str = ""


@_dataclass
class MusterTreffer:
    achse_name: str
    prioritaet: int
    anteil: float
    text: str


def _gewicht(f: dict, key: str) -> float:
    return float(f.get(key, 1.0))


def _pruefe_anteil(befunde: List[dict], achse: Achse) -> Optional[MusterTreffer]:
    if len(befunde) < achse.min_evidenz:
        return None
    gesamt_gewicht = sum(_gewicht(f, achse.gewicht_key) for f in befunde)
    if gesamt_gewicht <= 0:
        return None
    treffer_gewicht = sum(
        _gewicht(f, achse.gewicht_key) for f in befunde
        if f.get(achse.merkmal_key) == achse.merkmal_wert
    )
    anteil = treffer_gewicht / gesamt_gewicht
    if anteil < achse.schwelle:
        return None
    return MusterTreffer(achse.name, achse.prioritaet, anteil, "")


def _pruefe_praesenz(befunde: List[dict], achse: Achse) -> Optional[MusterTreffer]:
    treffer = [f for f in befunde if f.get(achse.merkmal_key) == achse.merkmal_wert]
    if len(treffer) < achse.min_evidenz:
        return None
    anteil = len(treffer) / len(befunde) if befunde else 0.0
    t = MusterTreffer(achse.name, achse.prioritaet, anteil, "")
    t.n_treffer = len(treffer)   # type: ignore[attr-defined]  — Trefferzahl, NICHT Gesamtzahl
    return t


def _pruefe_wiederholung(befunde: List[dict], achse: Achse) -> Optional[MusterTreffer]:
    schluessel = achse.merkmal_key or "wert"
    werte = [str(f.get(schluessel, "")).strip(".,!?").lower()
             for f in befunde if f.get(schluessel)]
    if not werte:
        return None
    haeufigster, anzahl = Counter(werte).most_common(1)[0]
    if anzahl < achse.min_anzahl:
        return None
    anteil = anzahl / len(befunde) if befunde else 0.0
    t = MusterTreffer(achse.name, achse.prioritaet, anteil, "")
    t.wert = haeufigster       # type: ignore[attr-defined]
    t.anzahl = anzahl          # type: ignore[attr-defined]
    return t


def _pruefe_cluster(befunde: List[dict], achse: Achse) -> Optional[MusterTreffer]:
    zeiten = sorted(f.get(achse.zeit_key, 0) for f in befunde)
    if len(zeiten) < achse.cluster_min_groesse:
        return None
    beste_groesse = 1
    fenster_start = 0
    for i in range(len(zeiten)):
        while zeiten[i] - zeiten[fenster_start] > achse.cluster_fenster_ms:
            fenster_start += 1
        beste_groesse = max(beste_groesse, i - fenster_start + 1)
    if beste_groesse < achse.cluster_min_groesse:
        return None
    anteil = beste_groesse / len(befunde)
    t = MusterTreffer(achse.name, achse.prioritaet, anteil, "")
    t.cluster_groesse = beste_groesse   # type: ignore[attr-defined]
    return t


def _pruefe_trend(befunde: List[dict], achse: Achse,
                   gesamt_dauer_ms: float) -> Optional[MusterTreffer]:
    if len(befunde) < achse.min_evidenz or gesamt_dauer_ms <= 0:
        return None
    mitte = gesamt_dauer_ms / 2
    erste_haelfte = sum(1 for f in befunde if f.get(achse.zeit_key, 0) < mitte)
    anteil = erste_haelfte / len(befunde)
    if anteil >= 0.7:
        t = MusterTreffer(achse.name, achse.prioritaet, anteil, "")
        t.richtung = "anfang"   # type: ignore[attr-defined]
        return t
    if anteil <= 0.3:
        t = MusterTreffer(achse.name, achse.prioritaet, 1 - anteil, "")
        t.richtung = "ende"     # type: ignore[attr-defined]
        return t
    return None


_PRUEF_FUNKTIONEN = {
    "anteil": _pruefe_anteil,
    "praesenz": _pruefe_praesenz,
    "wiederholung": _pruefe_wiederholung,
    "cluster": _pruefe_cluster,
}


def erkenne_muster_v2(
    befunde: List[dict],
    achsen: List[Achse],
    gesamt_dauer_ms: float = 0,
    max_tipps: int = 2,
    fall_a_text: Optional[List[str]] = None,
    einzelfund_template: Optional[str] = None,
    fall_c_einleitung: str = "Mehrere Fundstellen ohne gemeinsames Muster — die auffälligsten:",
    fallback_tipp: Optional[List[str]] = None,
) -> List[str]:
    """
    Vollständige Feedback-Ableitung, deckt JEDEN Fall ab:

      FALL A — 0 Fundstellen:       `fall_a_text` (nichts zu bemängeln)
      FALL B — genau 1 Fundstelle:  `einzelfund_template`, gefüllt mit den
                                     echten Werten DIESER einen Fundstelle
                                     (kein Muster wird behauptet)
      FALL C — 2+ Fundstellen,
               aber keine Achse
               trifft zu:           die 2 auffälligsten Fundstellen einzeln
                                     über `einzelfund_template`, mit
                                     Einleitungssatz
      FALL D — 2+ Fundstellen,
               1+ Achsen treffen:   aggregiertes Muster, nach Priorität
                                     sortiert, Top `max_tipps` Achsen

    `fallback_tipp` ist der alte, starre Text — wird nur noch verwendet,
    wenn `fall_a_text`/`einzelfund_template` nicht angegeben sind (Rückwärts-
    kompatibilität mit Modulen, die noch nicht auf die 4 Fälle umgestellt sind).
    """
    n = len(befunde)

    # FALL A — nichts gefunden
    if n == 0:
        if fall_a_text is not None:
            return fall_a_text
        return fallback_tipp or []

    # FALL B — genau 1 Fundstelle: kein Muster möglich, aber echte Daten da
    if n == 1:
        if einzelfund_template:
            try:
                return [einzelfund_template.format(**befunde[0])]
            except (KeyError, IndexError):
                pass
        return fallback_tipp or []

    # ab hier: n >= 2 — Achsen prüfen
    treffer: List[MusterTreffer] = []
    for achse in achsen:
        if achse.art == "trend":
            t = _pruefe_trend(befunde, achse, gesamt_dauer_ms)
        else:
            pruef_fn = _PRUEF_FUNKTIONEN.get(achse.art)
            t = pruef_fn(befunde, achse) if pruef_fn else None
        if t:
            treffer.append(t)

    # FALL C — mehrere Fundstellen, aber kein Muster trifft zu
    if not treffer:
        if einzelfund_template:
            zeilen = [fall_c_einleitung]
            for b in befunde[:2]:
                try:
                    zeilen.append("• " + einzelfund_template.format(**b))
                except (KeyError, IndexError):
                    continue
            if len(zeilen) > 1:
                return zeilen
        return fallback_tipp or []

    # FALL D — aggregiertes Muster
    treffer.sort(key=lambda t: (t.prioritaet, -t.anteil))
    treffer = treffer[:max_tipps]

    achsen_by_name = {a.name: a for a in achsen}
    texte = []
    for t in treffer:
        achse = achsen_by_name[t.achse_name]
        kontext = {
            "anteil": t.anteil,
            "n": getattr(t, "n_treffer", len(befunde)),
            "n_gesamt": len(befunde),
            "wort": getattr(t, "wert", ""),
            "anzahl": getattr(t, "anzahl", 0),
            "cluster_groesse": getattr(t, "cluster_groesse", 0),
            "richtung": getattr(t, "richtung", ""),
        }
        try:
            bausteine = [
                achse.befund_template.format(**kontext),
                achse.ursache_template.format(**kontext),
                achse.uebung_template.format(**kontext),
            ]
            texte.append(" ".join(b for b in bausteine if b))
        except (KeyError, IndexError):
            continue
    return texte or (fallback_tipp or [])
