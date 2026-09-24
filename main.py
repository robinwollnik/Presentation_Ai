"""main.py
==============================================================================
Orchestriert die komplette präsentation_ai-Pipeline (Planungs-Abschnitt 13).

Ablauf:
    1. transcribe.py                (Whisper)
    2. inhalt_analyse.py            (spaCy + Zero-Shot)
    3. Parallel-Gruppe A (Text-basiert, in fester Reihenfolge):
         a. pausen_analyse.py       — MUSS zuerst laufen (v2, 13.2)
         b. sprechfluss_analyse.py  — konsumiert Stocker informativ aus (a)
         c. sprechtempo_analyse.py
         d. füllwörter_analyse_v2.py
    4. Parallel-Gruppe B (Audio-basiert, teilen Audio im Speicher):
         a. lautstaerke_analyse.py
         b. pitch_variation_analyse.py
         c. emotionale_variation_analyse.py
    5. video_analyse.py             (MediaPipe + DeepFace) — falls vorhanden
    6. gesamtscore.py               (Aggregation + 5 Konsistenz-Checks)

Fehlerbehandlung:
    Wenn ein Modul crasht, stoppt die Pipeline NICHT. Der Fehler wird
    geloggt, das Modul in gesamtscore.py als "nicht bewertet" markiert.

Aufruf:
    python main.py <video_pfad>
    python main.py <video_pfad> --skip-transcribe   # wenn Transkript existiert
    python main.py <video_pfad> --skip-video         # kein Video-Modul
    python main.py --dry-run                         # nur die Pipeline zeigen
=============================================================================="""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, List

import report_utils as ru

# Ensure Unicode output works on Windows (cp1252 console can't print ═, → etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.environ["PYTHONIOENCODING"] = "utf-8"

# ============================================================================
# PFADE
# ============================================================================

PROJEKT_ROOT = Path(__file__).resolve().parent
ZWISCHEN_OUTPUT = PROJEKT_ROOT / "zwischen_output"
REPORTS_ROOT = PROJEKT_ROOT / "reports"
TRANSKRIPTE_DIR = PROJEKT_ROOT / "Transkripte"

# Standard-Dateinamen
TRANSKRIPT_SUFFIX = "_transkript.txt"

MODUL_OUTPUTS = {
    "inhalt":           ZWISCHEN_OUTPUT / "inhalt_analyse_output.json",
    "pausen":           ZWISCHEN_OUTPUT / "pausen_analyse_output.json",
    "sprechfluss":      ZWISCHEN_OUTPUT / "sprechfluss_analyse_output.json",
    "sprechtempo":      ZWISCHEN_OUTPUT / "sprechtempo_analyse_output.json",
    "fuellwoerter":     ZWISCHEN_OUTPUT / "fuellwoerter_analyse_output.json",
    "lautstaerke":      ZWISCHEN_OUTPUT / "lautstaerke_analyse_output.json",
    "pitch_variation":  ZWISCHEN_OUTPUT / "pitch_variation_analyse_output.json",
    "emotion":          ZWISCHEN_OUTPUT / "emotionale_variation_analyse_output.json",
    "video":            ZWISCHEN_OUTPUT / "video_analyse_output.json",
    "gesamt":           ZWISCHEN_OUTPUT / "gesamtscore_output.json",
}


# ============================================================================
# PIPELINE-INFRASTRUKTUR
# ============================================================================

class PipelineErgebnis:
    """Fasst zusammen, was gelaufen ist und was nicht."""
    def __init__(self):
        self.erfolgreich = []
        self.gescheitert = {}
        self.uebersprungen = []
        self.startzeit = time.time()

    def ok(self, name: str, dauer: float):
        self.erfolgreich.append((name, dauer))

    def fail(self, name: str, err: Exception):
        self.gescheitert[name] = err

    def skip(self, name: str, grund: str):
        self.uebersprungen.append((name, grund))

    def zusammenfassung(self) -> str:
        total = time.time() - self.startzeit
        lines = [
            "",
            "=" * 70,
            f"PIPELINE-ZUSAMMENFASSUNG  ({total:.1f}s gesamt)",
            "=" * 70,
        ]
        for name, dauer in self.erfolgreich:
            lines.append(f"  [OK]   {name:30s} {dauer:6.1f}s")
        for name, grund in self.uebersprungen:
            lines.append(f"  [SKIP] {name:30s} ({grund})")
        for name, err in self.gescheitert.items():
            lines.append(f"  [FAIL] {name:30s} {type(err).__name__}: {err}")
        lines.append("=" * 70)
        return "\n".join(lines)


def schritt(name: str, ergebnis: PipelineErgebnis, fn: Callable[[], None]) -> bool:
    """
    Führt einen Pipeline-Schritt aus mit Timing, Logging, Fehler-Isolation.
    Returns True wenn erfolgreich.
    """
    print(f"\n{'=' * 70}\n[main] SCHRITT: {name}\n{'=' * 70}")
    t0 = time.time()
    try:
        fn()
        dauer = time.time() - t0
        ergebnis.ok(name, dauer)
        print(f"[main] {name} fertig in {dauer:.1f}s")
        return True
    except Exception as e:
        ergebnis.fail(name, e)
        print(f"[main][FAIL] {name} crashed:\n{traceback.format_exc()}")
        return False


# ============================================================================
# WRAPPER FÜR JEDES MODUL
# ============================================================================

def run_transcribe(video_pfad: Path) -> Path:
    """Ruft transcribe.py auf und liefert den Transkript-Pfad."""
    import transcribe  # lokales Modul
    words = transcribe.transcribe(str(video_pfad), model_name="small")
    TRANSKRIPTE_DIR.mkdir(parents=True, exist_ok=True)
    transkript_pfad = TRANSKRIPTE_DIR / (video_pfad.stem + TRANSKRIPT_SUFFIX)
    transcribe.schreibe_transkript(words, str(transkript_pfad))

    # Lesbaren Transkript-Report schreiben
    report_pfad = REPORTS_ROOT / "transkript" / f"transkript_report_{ts()}.txt"
    report_pfad.parent.mkdir(parents=True, exist_ok=True)
    transcribe.schreibe_transkript_report(
        words, str(report_pfad), video_pfad.stem
    )

    return transkript_pfad


def run_inhalt(transkript_pfad: Path) -> None:
    """
    inhalt_analyse.py hat i.d.R. eine main()-Funktion mit File-Dialog.
    Wir setzen die Umgebungsvariable und rufen die Hauptfunktion direkt auf.
    Fallback: subprocess-Aufruf.
    """
    _run_module_subprocess(
        "inhalt_analyse.py",
        # nimmt den Pfad als arg — inhalt_analyse akzeptiert sys.argv[1]
        args=[str(transkript_pfad)],
    )


def run_pausen(transkript_pfad: Path) -> None:
    # pausen_analyse.py hat eine haupt-Funktion mit einer Path-Signatur
    import importlib.util
    spec = importlib.util.spec_from_file_location("pausen", PROJEKT_ROOT / "pausen_analyse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    zeitstempel = ts()
    mod.analyse_pausen(
        transkript_pfad=transkript_pfad,
        inhalt_pfad=MODUL_OUTPUTS["inhalt"],
        output_json_pfad=MODUL_OUTPUTS["pausen"],
        output_txt_kurz_pfad=REPORTS_ROOT / "pausen" / f"pausen_kurz_{zeitstempel}.txt",
        output_txt_detail_pfad=REPORTS_ROOT / "pausen" / f"pausen_detail_{zeitstempel}.txt",
    )


def run_sprechfluss(transkript_pfad: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sprechfluss", PROJEKT_ROOT / "sprechfluss_analyse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.analyse_sprechfluss(
        transkript_pfad=transkript_pfad,
        inhalt_pfad=MODUL_OUTPUTS["inhalt"],
        pausen_pfad=MODUL_OUTPUTS["pausen"],  # v2: Stocker informativ
        output_json_pfad=MODUL_OUTPUTS["sprechfluss"],
        output_txt_kurz_pfad=REPORTS_ROOT / "sprechfluss" / f"sprechfluss_kurz_{ts()}.txt",
        output_txt_detail_pfad=REPORTS_ROOT / "sprechfluss" / f"sprechfluss_detail_{ts()}.txt",
    )


def run_sprechtempo(transkript_pfad: Path) -> None:
    _run_module_subprocess("sprechtempo_analyse.py", args=[str(transkript_pfad)])


def run_fuellwoerter(transkript_pfad: Path) -> None:
    # v2: lokalen Konsistenz-Check unterdruecken, läuft zentral in gesamtscore
    os.environ["PAI_SKIP_LOCAL_CONSISTENCY"] = "1"
    _run_module_subprocess("fuellwoerter_analyse_v2.py", args=[str(transkript_pfad)])


def run_lautstaerke(audio_pfad: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lautstaerke", PROJEKT_ROOT / "lautstaerke_analyse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.analyse_lautstaerke(
        audio_pfad=audio_pfad,
        inhalt_pfad=MODUL_OUTPUTS["inhalt"],
        output_json_pfad=MODUL_OUTPUTS["lautstaerke"],
        output_txt_kurz_pfad=REPORTS_ROOT / "lautstaerke" / f"lautstaerke_kurz_{ts()}.txt",
        output_txt_detail_pfad=REPORTS_ROOT / "lautstaerke" / f"lautstaerke_detail_{ts()}.txt",
    )


def run_pitch(audio_pfad: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pitch", PROJEKT_ROOT / "pitch_variation_analyse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.analyse_pitch_variation(
        audio_pfad=audio_pfad,
        inhalt_pfad=MODUL_OUTPUTS["inhalt"],
        output_json_pfad=MODUL_OUTPUTS["pitch_variation"],
        output_txt_kurz_pfad=REPORTS_ROOT / "pitch" / f"pitch_kurz_{ts()}.txt",
        output_txt_detail_pfad=REPORTS_ROOT / "pitch" / f"pitch_detail_{ts()}.txt",
    )


def run_emotion(audio_pfad: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "emotion", PROJEKT_ROOT / "emotionale_variation_analyse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.analyse_emotionale_variation(
        audio_pfad=audio_pfad,
        inhalt_pfad=MODUL_OUTPUTS["inhalt"],
        output_json_pfad=MODUL_OUTPUTS["emotion"],
        output_txt_kurz_pfad=REPORTS_ROOT / "emotion" / f"emotion_kurz_{ts()}.txt",
        output_txt_detail_pfad=REPORTS_ROOT / "emotion" / f"emotion_detail_{ts()}.txt",
    )


_DIM_NAMEN = {
    "gesture_variety":  "Gestenvielfalt",
    "body_openness":    "Offene Körperhaltung",
    "movement_energy":  "Bewegungsenergie",
    "head_movement":    "Kopfbewegung",
    "expressiveness":   "Ausdrucksstärke",
}


def _video_fallback_tipps(gesamtscore: float) -> List[str]:
    if gesamtscore >= 80:
        return ["Deine Körpersprache ist bereits sehr stark — halte dieses "
                "Niveau: bewusste Gestik, offene Haltung, Blickkontakt."]
    if gesamtscore >= 50:
        return ["Achte bewusst auf die Dimension mit dem niedrigsten Wert — "
                "dort bringt Übung den größten Effekt."]
    return ["Nimm dich beim Üben auf Video auf und schau dir gezielt an, wie "
            "du in den schwächsten Momenten wirkst — meist reicht schon mehr "
            "bewusste Bewegung und offene Haltung."]


def generiere_video_kurz_report(
    clip_results: list, mean_scores: dict, gesamtscore: float,
    video_name: str, n_clips: int,
) -> str:
    dauer_s = n_clips * 5.0
    dauer_min = dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int(dauer_s % 60)} Sek"
    z = ru.kurz_header("KÖRPERSPRACHE (VIDEO)", video_name, dauer_str)

    if dauer_s < ru.MIN_ZUVERLAESSIGE_DAUER_S:
        z.append(f"  ⚠ Kurze Aufnahme ({dauer_s:.0f} Sek., {n_clips} Clip"
                  f"{'s' if n_clips != 1 else ''}) — Details dazu in der ausführlichen Fassung.")
        z.append("")

    z += ru.gesamtergebnis_block(
        round(gesamtscore),
        "Deine Körpersprache wirkt sehr überzeugend und lebendig.",
        "Deine Körpersprache ist ausbaufähig — einzelne Bereiche wirken noch zurückhaltend.",
        "Deine Körpersprache wirkt deutlich zurückhaltend — das schwächt deine Präsenz.",
    )

    z.append(ru.SEP2)
    z.append("  DEINE FÜNF TEILWERTE")
    z.append(ru.SEP2)
    for dim, val in mean_scores.items():
        name_de = _DIM_NAMEN.get(dim, dim)
        val100 = val * 100
        einordnung = "Stark ausgeprägt" if val >= 0.7 else ("Ausbaufähig" if val >= 0.4 else "Deutlich zurückhaltend")
        z += ru.dimension_zeile_kurz(name_de, 0, round(val100), einordnung)
    z.append("")

    z.append(ru.SEP2)
    z.append("  WAS DU KONKRET TUN KANNST")
    z.append(ru.SEP2)
    for i, zeile in enumerate(_video_fallback_tipps(gesamtscore), 1):
        umbrochen = ru.wrap_text(zeile) if len(zeile) > 64 else [zeile]
        z.append(f"  {i}. {umbrochen[0]}")
        z += [f"     {folgezeile}" for folgezeile in umbrochen[1:]]
    z.append("")
    z.append("  Wo genau im Video welche Bewegung erkannt wurde, steht im")
    z.append("  ausführlichen Report.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE KURZFASSUNG")
    z.append(ru.SEP)
    return "\n".join(z)


def generiere_video_detail_report(
    clip_results: list, mean_scores: dict, gesamtscore: float,
    video_name: str, n_clips: int,
) -> str:
    dauer_s = n_clips * 5.0
    dauer_min = dauer_s / 60.0
    dauer_str = f"{int(dauer_min)} Min {int(dauer_s % 60)} Sek"

    z = ru.detail_header("KÖRPERSPRACHE (VIDEO)", video_name, dauer_str)
    toc = ["Gesamtergebnis"]
    for dim in mean_scores:
        toc.append(f"{_DIM_NAMEN.get(dim, dim)} — Begründung & Fundstellen")
    toc.append("Hintergrund & Referenzwerte")
    z += ru.build_toc(toc)
    z.append("  Ein KI-Modell (MediaPipe) verfolgt dein Skelett — Körperhaltung,")
    z.append("  Arme, Hände, Kopf — und ein zweites Modell (ST-GCN) bewertet daraus")
    z.append("  5 Dimensionen deiner Körpersprache, in 5-Sekunden-Abschnitten.")
    z.append("")

    z += ru.gesamtergebnis_block(
        round(gesamtscore),
        "Deine Körpersprache wirkt sehr überzeugend und lebendig.",
        "Deine Körpersprache ist ausbaufähig — einzelne Bereiche wirken noch zurückhaltend.",
        "Deine Körpersprache wirkt deutlich zurückhaltend — das schwächt deine Präsenz.",
    )
    z += ru.kleine_stichprobe_warnung(dauer_s)

    # ── Übergreifend: mehrere Dimensionen gleichzeitig schwach im selben Clip ──
    kombi_befunde = []
    for cr in clip_results:
        schwache = [d for d, v in cr["scores"].items() if v < 0.6]
        if len(schwache) >= 2:
            kombi_befunde.append({
                "clip": cr, "schwache": schwache,
                "start_ms": cr["time_start"] * 1000,
            })

    # ── Pro Dimension: 2 Achsen (isolierte Schwäche, Zeittrend) ────────────────
    for dim, mean_val in mean_scores.items():
        name_de = _DIM_NAMEN.get(dim, dim)
        alle_clip_befunde = [{"wert": cr["scores"][dim], "schwach": cr["scores"][dim] < 0.6,
                               "start_ms": cr["time_start"] * 1000,
                               "zeit_von": s_to_zeitstr_video(cr["time_start"]),
                               "zeit_bis": s_to_zeitstr_video(cr["time_end"])}
                              for cr in clip_results]
        # Die Muster-Engine bekommt NUR die schwachen Clips als Befunde —
        # sonst würde sie auch starke Werte fälschlich als "Fundstelle" zeigen
        # (analog zu Pausen: nur Stocker sind Befunde, nicht alle Pausen).
        dim_befunde = [b for b in alle_clip_befunde if b["schwach"]]

        fundstellen = []
        for b in sorted(dim_befunde, key=lambda b: b["wert"])[:3]:
            fundstellen.append(ru.fundstelle_zeile(
                b["start_ms"], f"Clip bis {b['zeit_bis']}", f"{name_de}: {b['wert']:.2f}"))
        if not fundstellen:
            fundstellen = [f"  Keine schwachen Stellen bei {name_de} gefunden."]

        n_clips_gesamt = len(alle_clip_befunde)
        schwelle_durchgehend = max(2, (n_clips_gesamt + 1) // 2)  # mind. halbe Clips

        DIM_ACHSEN = [
            ru.Achse(
                f"{dim}_isolierte_schwaeche", "praesenz", prioritaet=1,
                merkmal_key="schwach", merkmal_wert=True, min_evidenz=schwelle_durchgehend,
                befund_template=(f"{name_de} ist bei {{n}} von {n_clips_gesamt} Clips "
                                  "schwächer ausgeprägt."),
                ursache_template="Das betrifft nicht nur einen kurzen Moment, sondern zieht sich durch einen Großteil des Videos.",
                uebung_template=_DIM_UEBUNG_SCHRITTE.get(dim, f"Übe {name_de} gezielt vor einem Spiegel."),
            ),
            ru.Achse(
                f"{dim}_zeittrend", "trend", prioritaet=2, zeit_key="start_ms",
                befund_template="{richtung_text}",
                ursache_template="{richtung_ursache}",
                uebung_template="{richtung_uebung}",
            ),
        ]
        for achse in DIM_ACHSEN:
            if achse.name == f"{dim}_zeittrend":
                probe = ru._pruefe_trend(dim_befunde, achse, dauer_s * 1000)
                if probe and getattr(probe, "richtung", "") == "anfang":
                    achse.befund_template = f"{name_de} ist zu Beginn des Videos schwächer als später."
                    achse.ursache_template = "Das ist typisch für Nervosität am Anfang, die sich meist von selbst legt."
                    achse.uebung_template = "Ein kurzes Warm-up vor der eigentlichen Aufnahme kann das reduzieren."
                elif probe:
                    achse.befund_template = f"{name_de} lässt Richtung Ende des Videos nach."
                    achse.ursache_template = "Das kann mit nachlassender Energie oder Konzentration zusammenhängen."
                    achse.uebung_template = "Plane bei längeren Aufnahmen bewusst einen energischen Moment für den Schluss ein."

        tipp = ru.erkenne_muster_v2(
            dim_befunde, DIM_ACHSEN, gesamt_dauer_ms=dauer_s * 1000, max_tipps=1,
            fall_a_text=[f"Keine Auffälligkeiten bei {name_de} gefunden."],
            einzelfund_template=(
                f"Bei {{zeit_von}} bis {{zeit_bis}} ist {name_de} schwächer "
                "ausgeprägt (Wert {wert:.2f}). Das kann ein kurzer Unsicherheitsmoment "
                "gewesen sein." + _DIM_UEBUNG_SCHRITTE.get(dim, "")
            ),
            fall_c_einleitung=f"Mehrere kurze Stellen mit schwächerem {name_de}, ohne gemeinsames Muster:",
        )
        if not tipp:
            tipp = [f"{name_de} ist über das ganze Video hinweg stark ausgeprägt — weiter so."]

        z += ru.dimension_block_detail(
            name_de, None, round(mean_val * 100),
            was_gemessen=[_DIM_BESCHREIBUNG.get(dim, "Körpersprache-Dimension.")],
            warum=[f"Durchschnitt über {n_clips} Clip{'s' if n_clips != 1 else ''}: {mean_val:.3f}.",
                   "Referenz: über 0.7 = stark, 0.4-0.7 = mittel, unter 0.4 = ausbaufähig."],
            fundstellen=fundstellen,
            tipp=tipp,
        )

    # ── Übergreifender Kombi-Befund ─────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  ÜBERGREIFEND")
    z.append(ru.SEP2)
    if kombi_befunde:
        b = min(kombi_befunde, key=lambda x: len(x["schwache"]) * -1)
        namen = ", ".join(_DIM_NAMEN.get(d, d) for d in b["schwache"])
        text = (f"Bei {s_to_zeitstr_video(b['clip']['time_start'])}–"
                f"{s_to_zeitstr_video(b['clip']['time_end'])} sind gleich mehrere Werte "
                f"gleichzeitig schwächer ({namen}). Das ist typisch für einen kurzen "
                "Unsicherheitsmoment, kein Dauerproblem — nicht die Körpersprache "
                "an sich, sondern der Inhalt an dieser Stelle ist wahrscheinlich "
                "die eigentliche Ursache.\n"
                "Schritt 1: Höre dir nur den Ton dieser Sekunden an — was sagst "
                "du da genau?\n"
                "Schritt 2: Sprich genau diesen Satz 3x frei, ohne aufs Video zu "
                "schauen, bis er inhaltlich sicher sitzt.\n"
                "Schritt 3: Nimm die Stelle mit Video neu auf und vergleiche.")
        for zeile in text.split("\n"):
            if not zeile.strip():
                z.append("")
                continue
            for teil in ru.wrap_text(zeile, breite=66):
                z.append(f"  {teil}")
    else:
        z.append("  Keine Stelle gefunden, an der mehrere Dimensionen gleichzeitig")
        z.append("  schwächer waren — deine Körpersprache wirkt konsistent.")
    z.append("")

    # ── Hintergrund ───────────────────────────────────────────────────────────
    z.append(ru.SEP2)
    z.append("  HINTERGRUND & REFERENZWERTE")
    z.append(ru.SEP2)
    z.append("  MediaPipe extrahiert Pose, Hände und Kopfposition als Skelett-Daten.")
    z.append("  Ein trainiertes Netz (ST-GCN) bewertet daraus 5 Dimensionen auf einer")
    z.append("  Skala von 0 bis 1: über 0.7 = stark, 0.4-0.7 = mittel, unter 0.4 =")
    z.append("  ausbaufähig.")
    z.append("")
    z.append(ru.SEP)
    z.append("  ENDE DETAILANSICHT")
    z.append(ru.SEP)
    return "\n".join(z)


_DIM_BESCHREIBUNG = {
    "gesture_variety": "Wie abwechslungsreich du deine Hände einsetzt — zeigende, offene, zählende Gesten statt immer der gleichen Bewegung.",
    "body_openness": "Ob deine Arme offen und weg vom Körper sind, statt verschränkt oder verschlossen.",
    "movement_energy": "Wie viel körperliches Engagement und Lebendigkeit insgesamt erkennbar ist.",
    "head_movement": "Natürliches Nicken und gerichtete Kopfbewegung — signalisiert Engagement mit dem Publikum.",
    "expressiveness": "Ob Gesicht und Körper zusammen die Begeisterung für den Inhalt vermitteln.",
}


def s_to_zeitstr_video(sekunden: float) -> str:
    m = int(sekunden // 60)
    s = int(sekunden % 60)
    return f"{m}:{s:02d}"


_DIM_UEBUNG_SCHRITTE = {
    "gesture_variety": (
        "\nSchritt 1: Stell dich vor einen Spiegel und übe 3 verschiedene "
        "Handgesten (zeigen, öffnen, zählen) zu genau dieser Textstelle.\n"
        "Schritt 2: Sprich den Abschnitt erneut, während du bewusst "
        "zwischen den 3 Gesten wechselst.\n"
        "Schritt 3: Nimm dich mit Video auf und prüfe, ob die Gesten "
        "natürlich wirken, nicht einstudiert."
    ),
    "body_openness": (
        "\nSchritt 1: Stell dich hin, Arme locker seitlich, und sprich "
        "den Abschnitt einmal bewusst mit offener Haltung.\n"
        "Schritt 2: Achte darauf, die Arme nicht zu verschränken oder "
        "eng am Körper zu halten.\n"
        "Schritt 3: Nimm dich mit Video auf und vergleiche mit der "
        "vorherigen Aufnahme."
    ),
    "movement_energy": (
        "\nSchritt 1: Sprich den Abschnitt im Stehen, mit bewusst mehr "
        "Körperbewegung (kleine Schritte, Handbewegungen) als sonst.\n"
        "Schritt 2: Wiederhole und übertreibe die Bewegung bewusst leicht.\n"
        "Schritt 3: Nimm dich mit Video auf und finde ein Mittelmaß, das "
        "sich noch natürlich anfühlt."
    ),
    "head_movement": (
        "\nSchritt 1: Sprich den Abschnitt und nicke bewusst bei jeder "
        "wichtigen Aussage leicht.\n"
        "Schritt 2: Wiederhole, diesmal mit Blickkontakt zur Kamera "
        "kombiniert.\n"
        "Schritt 3: Nimm dich mit Video auf und prüfe, ob das Nicken "
        "natürlich wirkt."
    ),
    "expressiveness": (
        "\nSchritt 1: Sprich den Abschnitt einmal bewusst mit mehr "
        "Mimik — Augenbrauen, Lächeln, wo es passt.\n"
        "Schritt 2: Kombiniere die Mimik mit passender Körperhaltung.\n"
        "Schritt 3: Nimm dich mit Video auf und vergleiche den Ausdruck "
        "mit einer starken Stelle deines Videos."
    ),
}


def run_video(video_pfad: Path) -> None:
    """
    Video-Analyse-Pipeline:
      1. extract_skeleton.py       — MediaPipe pose/hand/face -> .pickle
      2. split_skeleton_clips.py   — 5-Sekunden-Clips        -> clip_*.pickle
      3. infer.py                  — ST-GCN Scoring          -> scores pro Clip
      4. Aggregation               -> zwischen_output/video_analyse_output.json
    """
    import sys as _sys
    import json as _json
    import pickle as _pickle
    import importlib.util as _ilu

    # ── Pfade ────────────────────────────────────────────────────────────────
    VIDEO_MODEL_DIR = PROJEKT_ROOT / "video_model"
    CHECKPOINT      = VIDEO_MODEL_DIR / "model.pth"
    MP_MODELS_DIR   = VIDEO_MODEL_DIR / "mp_models"
    WORK_DIR        = ZWISCHEN_OUTPUT / "video_work"
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    skeleton_pkl = WORK_DIR / (video_pfad.stem + "_skeleton.pickle")
    clips_dir    = WORK_DIR / "clips"
    clips_dir.mkdir(exist_ok=True)

    if not CHECKPOINT.exists():
        raise FileNotFoundError(
            f"Kein Modell-Checkpoint gefunden: {CHECKPOINT}\n"
            "Lege model.pth nach abgabe_struktur/video_model/model.pth."
        )

    def _load(name):
        path = VIDEO_MODEL_DIR / f"{name}.py"
        spec = _ilu.spec_from_file_location(name, path)
        mod  = _ilu.module_from_spec(spec)
        if str(VIDEO_MODEL_DIR) not in _sys.path:
            _sys.path.insert(0, str(VIDEO_MODEL_DIR))
        spec.loader.exec_module(mod)
        return mod

    # ── Schritt 1: Skeleton extrahieren ──────────────────────────────────────
    print(f"[video] Schritt 1/3: Skeleton extrahieren → {skeleton_pkl.name}")
    if skeleton_pkl.exists():
        print(f"[video] Skeleton-Pickle existiert, überspringe Extraktion.")
    else:
        extract_mod = _load("extract_skeleton")
        extract_mod.extract(
            video_path=str(video_pfad),
            output_path=str(skeleton_pkl),
            models_dir=str(MP_MODELS_DIR),
        )

    # ── Schritt 2: Clips splitten ─────────────────────────────────────────────
    print(f"[video] Schritt 2/3: Skeleton in 5-Sekunden-Clips aufteilen")
    split_mod = _load("split_skeleton_clips")

    # split_skeleton_clips.py kann fps direkt aus dem Video lesen
    fps = split_mod.get_fps_from_video(str(video_pfad))

    with open(skeleton_pkl, "rb") as _f:
        frames_data = _pickle.load(_f)

    clips = split_mod.split_clips(
        frames_data=frames_data,
        fps=fps,
        clip_seconds=5.0,
        stride_seconds=None,   # non-overlapping
        keep_last=False,
    )
    if not clips:
        raise RuntimeError(
            "[video] Keine Clips erzeugt — zu wenig Pose-Erkennung im Video."
        )

    # Clips auf Disk schreiben (split_clips() gibt frame-Listen zurück, kein I/O)
    clips_meta = []
    for i, clip in enumerate(clips):
        filename = f"clip_{i:04d}.pickle"
        out_path = clips_dir / filename
        with open(out_path, "wb") as _f:
            _pickle.dump(clip["frames"], _f)
        clips_meta.append({
            "file":           filename,
            "start_time_sec": clip["start_time_sec"],
            "end_time_sec":   clip["end_time_sec"],
        })
    print(f"[video] {len(clips_meta)} Clips erzeugt.")

    # ── Schritt 3: Inference ─────────────────────────────────────────────────
    print(f"[video] Schritt 3/3: ST-GCN Inference ({len(clips_meta)} Clips)")
    import torch as _torch
    infer_mod = _load("infer")

    device = _torch.device("cuda" if _torch.cuda.is_available() else "cpu")
    model, cfg = infer_mod.load_model(str(CHECKPOINT), device)

    # extract_skeleton.py schreibt '*_keypoints_2d'-Schlüssel;
    # dataset.py::extract_skeleton() liest '*_keypoints' (ohne '_2d').
    # Wir normalisieren jeden Clip vor der Inference.
    import pickle as _pickle

    KEY_MAP = {
        "pose_keypoints_2d":       "pose_keypoints",
        "hand_left_keypoints_2d":  "hand_left_keypoints",
        "hand_right_keypoints_2d": "hand_right_keypoints",
        "face_keypoints_2d":       "face_keypoints",
    }

    def _normalise_keys(pkl_path):
        """Liest ein Clip-Pickle, normalisiert die Schlüsselnamen in-place."""
        with open(pkl_path, "rb") as _f:
            frames = _pickle.load(_f)
        changed = False
        for frame in frames:
            for person in frame:
                for old, new in KEY_MAP.items():
                    if old in person and new not in person:
                        person[new] = person.pop(old)
                        changed = True
        if changed:
            with open(pkl_path, "wb") as _f:
                _pickle.dump(frames, _f)

    clip_results = []
    for meta in clips_meta:
        pkl_path = clips_dir / meta["file"]
        _normalise_keys(str(pkl_path))
        try:
            scores   = infer_mod.score_clip(str(pkl_path), model, cfg, device)
            feedback = infer_mod.interpret_scores(scores)
            clip_results.append({
                "filename":   meta["file"],
                "time_start": meta["start_time_sec"],
                "time_end":   meta["end_time_sec"],
                "scores":     scores,
                "feedback":   feedback,
            })
        except Exception as e:
            print(f"[video] WARN: Clip {meta['filename']} fehlgeschlagen: {e}")

    if not clip_results:
        raise RuntimeError("[video] Kein Clip konnte gescort werden.")

    # ── Aggregation: Mittelwert über alle Clips ───────────────────────────────
    import statistics as _stats
    dims = list(clip_results[0]["scores"].keys())
    mean_scores = {
        dim: round(_stats.mean(c["scores"][dim] for c in clip_results), 4)
        for dim in dims
    }
    # Gesamtscore: Mittelwert der 5 Dimensionen, skaliert auf 0–100
    gesamtscore = round(sum(mean_scores.values()) / len(mean_scores) * 100, 2)

    output = {
        "modul":        "video_analyse",
        "version":      "1.0",
        "video":        str(video_pfad),
        "n_clips":      len(clip_results),
        "fps":          fps,
        "mean_scores":  mean_scores,
        "clip_results": clip_results,
        "scoring": {
            "gesamtscore": gesamtscore,
            "dimension_scores": {dim: round(v * 100, 2) for dim, v in mean_scores.items()},
        },
    }

    output_path = MODUL_OUTPUTS["video"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        _json.dump(output, f, ensure_ascii=False, indent=2)

    # Report
    zeitstempel = ts()
    report_kurz_path = REPORTS_ROOT / "video" / f"video_kurz_{zeitstempel}.txt"
    report_detail_path = REPORTS_ROOT / "video" / f"video_detail_{zeitstempel}.txt"
    report_kurz_path.parent.mkdir(parents=True, exist_ok=True)

    kurz_report = generiere_video_kurz_report(
        clip_results, mean_scores, gesamtscore, video_pfad.name, len(clip_results)
    )
    with open(report_kurz_path, "w", encoding="utf-8") as f:
        f.write(kurz_report)

    detail_report = generiere_video_detail_report(
        clip_results, mean_scores, gesamtscore, video_pfad.name, len(clip_results)
    )
    with open(report_detail_path, "w", encoding="utf-8") as f:
        f.write(detail_report)

    print(f"[video] Gesamtscore: {gesamtscore:.1f}/100")
    print(f"[video] JSON:   {output_path}")
    print(f"[video] Kurz-Report:   {report_kurz_path}")
    print(f"[video] Detail-Report: {report_detail_path}")


def run_gesamtscore() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gesamtscore", PROJEKT_ROOT / "gesamtscore.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.aggregiere(
        input_dir=ZWISCHEN_OUTPUT,
        output_json=MODUL_OUTPUTS["gesamt"],
        output_txt=REPORTS_ROOT / "gesamt" / f"gesamt_report_{ts()}.txt",
    )


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_module_subprocess(script_name: str, args: list = None) -> None:
    """
    Ruft ein Modul als Subprocess auf. Für Module mit main()+File-Dialog
    einfacher als Import, weil sie sys.argv, tkinter etc. verwenden.
    """
    import subprocess
    cmd = [sys.executable, str(PROJEKT_ROOT / script_name)]
    if args:
        cmd += args
    print(f"[main] Subprocess: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJEKT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} exit code {result.returncode}")


def extrahiere_audio(video_pfad: Path) -> Path:
    """Zieht die Audio-Spur aus dem Video mit ffmpeg. Skippt wenn schon da."""
    audio_pfad = TRANSKRIPTE_DIR / (video_pfad.stem + ".wav")
    audio_pfad.parent.mkdir(parents=True, exist_ok=True)
    if audio_pfad.exists():
        print(f"[main] Audio existiert: {audio_pfad}")
        return audio_pfad
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-i", str(video_pfad),
        "-ac", "1", "-ar", "16000",
        "-vn", str(audio_pfad),
    ]
    print(f"[main] Extrahiere Audio: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fehlgeschlagen:\n{result.stderr[:500]}")
    return audio_pfad


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="präsentation_ai Pipeline")
    parser.add_argument("video", nargs="?",
                        help="Pfad zum Video (oder Audio) der Präsentation")
    parser.add_argument("--transkript", help="Vorhandenes Transkript verwenden")
    parser.add_argument("--audio", help="Vorhandene Audio-Datei verwenden")
    parser.add_argument("--skip-transcribe", action="store_true",
                        help="Transkription auslassen (--transkript setzen)")
    parser.add_argument("--skip-video", action="store_true",
                        help="Video-Analyse auslassen")
    parser.add_argument("--skip-emotion", action="store_true",
                        help="Emotion-Analyse auslassen (ML-Modell laedt ~3 GB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur die Pipeline-Schritte anzeigen, nichts ausführen")
    args = parser.parse_args()

    if args.dry_run:
        pipeline_dry_run()
        return

    if not args.video and not args.transkript:
        print("[main] Bitte Video oder --transkript angeben.")
        parser.print_help()
        sys.exit(1)

    ZWISCHEN_OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)

    video_pfad = Path(args.video) if args.video else None
    ergebnis = PipelineErgebnis()

    # ------- Schritt 1: Transkription -------
    if args.transkript:
        transkript_pfad = Path(args.transkript)
        ergebnis.skip("transkription", "--transkript gesetzt")
    elif args.skip_transcribe:
        transkript_pfad = TRANSKRIPTE_DIR / (video_pfad.stem + TRANSKRIPT_SUFFIX)
        if not transkript_pfad.exists():
            print(f"[main][FAIL] --skip-transcribe aber Transkript fehlt: {transkript_pfad}")
            sys.exit(2)
        ergebnis.skip("transkription", "--skip-transcribe")
    else:
        schritt("transkription", ergebnis, lambda: run_transcribe(video_pfad))
        transkript_pfad = TRANSKRIPTE_DIR / (video_pfad.stem + TRANSKRIPT_SUFFIX)

    # ------- Schritt 2: Inhaltsanalyse -------
    schritt("inhalt_analyse", ergebnis, lambda: run_inhalt(transkript_pfad))

    # ------- Schritt 3: Gruppe A (Transkript-basiert) -------
    # Reihenfolge: pausen -> sprechfluss -> sprechtempo -> füllwörter
    # Für Iris Xe: sequentiell laufen lassen. Parallelisierung mit
    # concurrent.futures.ProcessPoolExecutor optional (siehe Kommentar unten).
    schritt("pausen_analyse", ergebnis, lambda: run_pausen(transkript_pfad))
    schritt("sprechfluss_analyse", ergebnis, lambda: run_sprechfluss(transkript_pfad))
    schritt("sprechtempo_analyse", ergebnis, lambda: run_sprechtempo(transkript_pfad))
    schritt("fuellwoerter_analyse", ergebnis, lambda: run_fuellwoerter(transkript_pfad))

    # ------- Schritt 4: Gruppe B (Audio) -------
    audio_pfad: Optional[Path] = None
    if args.audio:
        audio_pfad = Path(args.audio)
    elif video_pfad:
        schritt("audio_extraktion", ergebnis, lambda: setattr(main, "_audio", extrahiere_audio(video_pfad)))
        audio_pfad = getattr(main, "_audio", None)

    if audio_pfad and audio_pfad.exists():
        schritt("lautstaerke_analyse", ergebnis, lambda: run_lautstaerke(audio_pfad))
        schritt("pitch_variation_analyse", ergebnis, lambda: run_pitch(audio_pfad))
        if args.skip_emotion:
            ergebnis.skip("emotionale_variation", "--skip-emotion")
        else:
            schritt("emotionale_variation", ergebnis, lambda: run_emotion(audio_pfad))
    else:
        ergebnis.skip("audio_gruppe", "keine Audio-Datei verfügbar")

    # ------- Schritt 5: Video -------
    if args.skip_video:
        ergebnis.skip("video_analyse", "--skip-video")
    elif not video_pfad:
        ergebnis.skip("video_analyse", "kein Video angegeben")
    else:
        schritt("video_analyse", ergebnis, lambda: run_video(video_pfad))

    # ------- Schritt 6: Gesamt-Aggregation -------
    schritt("gesamtscore", ergebnis, run_gesamtscore)

    # ------- Abschluss -------
    print(ergebnis.zusammenfassung())


def pipeline_dry_run():
    print("PIPELINE (kein Lauf, nur Ablauf):\n")
    print("  1. transcribe.py                    -> Transkripte/<name>_transkript.txt")
    print("  2. inhalt_analyse.py                -> zwischen_output/inhalt_analyse_output.json")
    print("  3. Gruppe A (Text):")
    print("       a. pausen_analyse.py           -> zwischen_output/pausen_analyse_output.json")
    print("       b. sprechfluss_analyse.py      -> zwischen_output/sprechfluss_analyse_output.json")
    print("       c. sprechtempo_analyse.py      -> zwischen_output/sprechtempo_analyse_output.json")
    print("       d. fuellwoerter_analyse_v2.py  -> zwischen_output/fuellwoerter_analyse_output.json")
    print("  4. Gruppe B (Audio):")
    print("       a. lautstaerke_analyse.py")
    print("       b. pitch_variation_analyse.py")
    print("       c. emotionale_variation_analyse.py")
    print("  5. video_analyse (run_video):")
    print("       a. extract_skeleton.py         -> zwischen_output/video_work/<name>_skeleton.pickle")
    print("       b. split_pickle.py             -> zwischen_output/video_work/clips/")
    print("       c. infer.py (best_model.pth)   -> zwischen_output/video_analyse_output.json")
    print("  6. gesamtscore.py                   -> reports/gesamt/gesamt_report_*.txt")


# ----------------------------------------------------------------------------
# OPTIONAL: Parallelisierung von Gruppe A
# ----------------------------------------------------------------------------
# Auf Iris Xe bringt es ca. 60 % Zeitersparnis. WICHTIG:
# pausen MUSS zuerst fertig sein bevor sprechfluss startet
# (sprechfluss liest pausen_analyse_output.json). Daher:
#
#   from concurrent.futures import ProcessPoolExecutor
#   schritt("pausen_analyse", ergebnis, lambda: run_pausen(transkript_pfad))
#   with ProcessPoolExecutor(max_workers=3) as ex:
#       f1 = ex.submit(run_sprechfluss,  transkript_pfad)
#       f2 = ex.submit(run_sprechtempo,  transkript_pfad)
#       f3 = ex.submit(run_fuellwoerter, transkript_pfad)
#       for name, fut in [("sprechfluss", f1), ("sprechtempo", f2),
#                         ("füllwörter", f3)]:
#           try:  fut.result();  ergebnis.ok(name, 0)
#           except Exception as e: ergebnis.fail(name, e)
#
# Gruppe B sollte NICHT parallelisiert werden (Audio-Daten teilen im
# Speicher, dreifache Ladezeit sonst).


if __name__ == "__main__":
    main()
