"""transcribe.py
==============================================================================
Transkribiert ein Video oder eine Audio-Datei mit lokal laufendem Whisper.
Ausgabe im Format, das die downstream-Module (inhalt_analyse, pausen_analyse,
sprechfluss_analyse, sprechtempo_analyse) erwarten:

    Wort HH:MM:SS.mmm HH:MM:SS.mmm

Ein Wort pro Zeile, KEIN Header, KEIN Trennstrich.

Standardmaessig 'small'. Über --model lässt sich das überschreiben.

Aufruf:
    python transcribe.py <video_path> [--model small|medium|...]
    python transcribe.py             # öffnet File-Dialog

Ausgabe:
    Transkripte/<basename>_transkript.txt
=============================================================================="""

import sys
import os
import argparse
import textwrap
from datetime import datetime
import whisper


def format_time(seconds: float) -> str:
    """Whisper liefert Sekunden — wir brauchen HH:MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def pick_video_file():
    """File-Dialog, wenn tkinter verfügbar. Sonst None."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Video/Audio auswählen",
        filetypes=[
            ("Video/Audio", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.wav *.mp3 *.m4a"),
            ("Alle Dateien", "*.*"),
        ],
    )
    root.destroy()
    return path or None


def transcribe(video_path: str, model_name: str = "small") -> list:
    """Whisper mit word_timestamps aufrufen."""
    print(f"[transcribe] Lade Whisper-Modell '{model_name}' ...")
    model = whisper.load_model(model_name)
    print(f"[transcribe] Transkribiere: {video_path}")
    result = model.transcribe(
        video_path,
        word_timestamps=True,
        language="de",  # explizit Deutsch — verhindert falsche Sprach-Detection
    )
    words = []
    for segment in result["segments"]:
        for word_info in segment.get("words", []):
            wort = word_info["word"].strip()
            if not wort:
                continue
            words.append({
                "word": wort,
                "start": float(word_info["start"]),
                "end": float(word_info["end"]),
            })
    return words


def schreibe_transkript(words: list, output_path: str) -> None:
    """
    Schreibt im Downstream-Format:
        Wort HH:MM:SS.mmm HH:MM:SS.mmm

    KEIN Header, KEIN Trennstrich — sonst stolpern die Parser der
    Analyse-Module über ungültige Zeitstempel.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        for w in words:
            f.write(f"{w['word']} {format_time(w['start'])} {format_time(w['end'])}\n")


def schreibe_transkript_report(
    words: list,
    output_path: str,
    video_name: str,
    model_name: str = "small",
) -> None:
    """
    Schreibt einen lesbaren Transkript-Report mit Satz-Zeitstempeln,
    Statistiken und Fließtext.

    Die Rohdatei (maschinelles Format) bleibt unverändert — diese Funktion
    schreibt einen *zusätzlichen* lesbaren Bericht.
    """
    if not words:
        return

    SEP  = "=" * 70
    SEP2 = "-" * 70

    # ── Sätze zusammenfassen ─────────────────────────────────────────────
    saetze = []
    aktuell = []
    for i, w in enumerate(words):
        aktuell.append(w)
        # Satzgrenze: Wort endet auf .  !  ?  ODER lange Pause zum nächsten Wort
        endet_satz = w["word"].rstrip().endswith((".", "!", "?"))
        lange_pause = (
            i + 1 < len(words) and (words[i + 1]["start"] - w["end"]) > 0.8
        )
        if endet_satz or lange_pause:
            saetze.append(aktuell)
            aktuell = []
    if aktuell:
        saetze.append(aktuell)

    # ── Statistiken ──────────────────────────────────────────────────────
    gesamtdauer = words[-1]["end"] - words[0]["start"]
    wortanzahl  = len(words)
    wpm         = int(wortanzahl / (gesamtdauer / 60)) if gesamtdauer > 0 else 0
    min_ges     = int(gesamtdauer // 60)
    sek_ges     = int(gesamtdauer % 60)

    # ── Fließtext (70 Zeichen, 2 Leerzeichen Einzug) ─────────────────────
    volltext = " ".join(w["word"] for w in words)
    fliesszeilen = textwrap.fill(volltext, width=68, initial_indent="  ",
                                  subsequent_indent="  ")

    # ── Report schreiben ─────────────────────────────────────────────────
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(SEP + "\n")
        f.write("  TRANSKRIPT — LESBARE FASSUNG\n")
        f.write(SEP + "\n")
        f.write(f"  Erstellt:      {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"  Quelldatei:    {video_name}\n")
        f.write(f"  Modell:        Whisper {model_name}\n")
        f.write(f"  Sprache:       Deutsch\n")
        f.write("\n")

        f.write(SEP2 + "\n")
        f.write("  ÜBERSICHT\n")
        f.write(SEP2 + "\n")
        f.write(f"  Gesamtdauer:    {gesamtdauer:.1f} Sek  "
                f"({min_ges} Min {sek_ges} Sek)\n")
        f.write(f"  Wörter gesamt:  {wortanzahl}\n")
        f.write(f"  Wörter/Minute:  ca. {wpm}\n")
        f.write(f"  Sätze erkannt:  {len(saetze)}\n")
        f.write("\n")

        f.write(SEP2 + "\n")
        f.write("  GESPROCHENER TEXT (mit Zeitstempeln)\n")
        f.write(SEP2 + "\n")
        for satz_worte in saetze:
            start_sek = satz_worte[0]["start"]
            mm = int(start_sek // 60)
            ss = int(start_sek % 60)
            satz_text = " ".join(w["word"] for w in satz_worte)
            f.write(f"  [{mm:02d}:{ss:02d}]  {satz_text}\n")
        f.write("\n")

        f.write(SEP2 + "\n")
        f.write("  VOLLSTÄNDIGER TEXT (fortlaufend)\n")
        f.write(SEP2 + "\n")
        f.write(fliesszeilen + "\n")
        f.write("\n")

        rahdatei_name = video_name + "_transkript.txt"
        f.write(SEP2 + "\n")
        f.write("  HINWEIS\n")
        f.write(SEP2 + "\n")
        f.write("  Dies ist eine automatische Transkription durch Whisper (KI-Modell).\n")
        f.write("  Fehler sind möglich — besonders bei Eigennamen, Fachbegriffen und\n")
        f.write("  undeutlicher Aussprache.\n")
        f.write("  Die Rohdatei mit Wort-Zeitstempeln liegt unter:\n")
        f.write(f"  Transkripte/{rahdatei_name}\n")
        f.write("\n")

        f.write(SEP + "\n")
        f.write("  ENDE REPORT\n")
        f.write(SEP + "\n")


def main():
    parser = argparse.ArgumentParser(description="Whisper-Transkription für präsentation_ai.")
    parser.add_argument("video", nargs="?", help="Pfad zur Video-/Audio-Datei")
    parser.add_argument(
        "--model", default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper-Modell (Standard: small)"
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Zielverzeichnis (Standard: ./Transkripte relativ zum Script)"
    )
    args = parser.parse_args()

    video_path = args.video or pick_video_file()
    if not video_path:
        print("[transcribe] Kein Video ausgewählt. Abbruch.")
        sys.exit(1)

    if not os.path.isfile(video_path):
        print(f"[transcribe] Datei nicht gefunden: {video_path}")
        sys.exit(1)

    words = transcribe(video_path, model_name=args.model)
    if not words:
        print("[transcribe] Whisper hat keine Wörter geliefert.")
        sys.exit(2)

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Transkripte"
    )
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_path = os.path.join(output_dir, base_name + "_transkript.txt")

    schreibe_transkript(words, output_path)
    print(f"[transcribe] Transkript gespeichert: {output_path}")
    print(f"[transcribe] {len(words)} Wörter, "
          f"Dauer ca. {words[-1]['end'] - words[0]['start']:.1f}s")


if __name__ == "__main__":
    main()
