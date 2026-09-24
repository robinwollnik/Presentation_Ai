# Report Overhaul Plan

> **Letzte Aktualisierung:** Sub-Task 11 (Transkript-Report) auf Wunsch ergänzt.

## Ziel

Alle 8 Modul-Reports und der Gesamtreport werden von Grund auf überarbeitet.
Jeder Report soll danach:

- **Einheitliches Format** haben (gleiche Trennlinien, gleiche Abschnittsstruktur)
- **Vollständig auf Deutsch** sein (keine englischen Fachbegriffe ohne Erklärung)
- **Jeden Messwert erklären** — was bedeutet die Zahl, was ist gut, was ist schlecht
- **Konkrete Handlungsempfehlungen** enthalten — nicht nur Score, sondern „was tun"
- **Alle erhobenen Daten sichtbar machen** — nichts weglassen, aber alles verständlich einleiten
- **Ampelsystem** (✅ gut ≥75 / 🟡 ausbaufähig ≥50 / ❌ Handlungsbedarf <50) konsistent verwenden

---

## Einheitliches Format (gilt für alle Reports)

Jeder Report folgt exakt dieser Struktur:

```
======================================================================
  [MODUL-NAME] — ANALYSE-REPORT
======================================================================
  Erstellt:   DD.MM.YYYY HH:MM
  Quelle:     [Dateiname]
  Dauer:      [X Min Y Sek]

======================================================================
  GESAMTERGEBNIS
======================================================================
  [Score] / 100   [████████████░░░░░░░░]   [✅/🟡/❌]
  [1–2 Sätze Gesamtbefund in Alltagssprache]

----------------------------------------------------------------------
  WIE SICH DER SCORE ZUSAMMENSETZT
----------------------------------------------------------------------
  [Ampel] [Dimensionsname]   [Score]/100   ([Gewicht]%)
          Was gemessen wird: [Erklärung in einem Satz]
          Befund:            [Messwert in verständlicher Einheit]
          Bewertung:         [Texturteil]

  [... alle Dimensionen ...]

----------------------------------------------------------------------
  DETAIL-AUSWERTUNG
----------------------------------------------------------------------
  [Modul-spezifische Rohdaten — Tabellen, Zeitstempel, Listen]
  Jede Spalte/Zeile hat eine kurze Legende

----------------------------------------------------------------------
  WAS DU KONKRET TUN KANNST
----------------------------------------------------------------------
  [Nummerierte, priorisierte Handlungsempfehlungen]
  Nur wenn Score < 75, sonst positives Feedback

----------------------------------------------------------------------
  HINTERGRUND & REFERENZWERTE
----------------------------------------------------------------------
  [Erklärung der Mess-Methode in 2–3 Sätzen]
  Optimum: [Wert/Bereich]
  Quellen: [Studien]
======================================================================
  ENDE REPORT
======================================================================
```

---

## Sub-Task 1 — Pausen-Report

**Status:** `[x] done`

**Intent:**
Der Pausen-Report ist derzeit der schwächste. „Stocker", „rhetorische Qualität" und die 8 Pausentypen sind für Laien unverständlich. Der Report soll erklären was eine Pause überhaupt ist, warum sie wichtig ist, und was der Unterschied zwischen einem hilfreichen und einem störenden Innehalten ist.

**Relevant:** `pausen_analyse.py` → Funktion `generiere_txt_report()` (Zeile ~722)

**Erwartetes Ergebnis:**
- Fachbegriff „Stocker" wird als „ungewolltes Stocken / Unterbrechung" eingeführt und erklärt
- Fachbegriff „rhetorische Pause" wird erklärt: bewusstes Innehalten zur Wirkung
- Die 8 Pausentypen werden in lesbare Gruppen zusammengefasst (störend / neutral / bewusst)
- Negative Prozentzahl bei Verlangsamung wird als klarer Befund formuliert
- Kernbotschaft-Check erklärt warum Pausen vor/nach Kernaussagen wichtig sind
- Konkrete Tipps: wann Pausen einbauen, wie lang, wie üben

**Todo-Liste:**
1. Pausentypen in 3 Gruppen zusammenfassen und mit Erklärung versehen:
   - Störend: `kleiner_stocker`, `stocker`, `stocker_lang`
   - Neutral: `ignorieren`, `natuerlich_kurz`, `atem`
   - Bewusst: `natuerlich`, `rhetorisch`, `wirkung`
2. D1 „Rhetorische Qualität" umbenennen in „Bewusste Pausen" — Formel erklären:
   `Anteil bewusster Pausen an allen hörbaren Pausen`
3. D2 „Stocker-Rate" umbenennen in „Ungewolltes Stocken" — Referenzwert erklären:
   `Gut: unter 3/Minute, Hörbar: 3–8/Minute, Störend: über 8/Minute`
4. D3 „Pausen-Haushalt" umbenennen in „Gesamtzahl Pausen" — Referenzwert erklären:
   `Gut: 5–15/Minute, Zu wenig: unter 5, Zu viele/stockend: über 20`
5. Stocker-Detail-Liste mit erklärendem Einleitungssatz versehen
6. Kernbotschaft-Check: erklären warum ≥800ms nötig (Publikum braucht Zeit zum Verarbeiten)
7. Empfehlungs-Abschnitt: 3 konkrete Tipps je nach Score-Bereich
8. Referenzwerte-Abschnitt: Pausenlängen-Tabelle (was wie lang dauert und was es bewirkt)

---

## Sub-Task 2 — Sprechtempo-Report

**Status:** `[x] done`

**Intent:**
Der Sprechtempo-Report ist bereits der benutzerfreundlichste aller Module (hat Feedback-Texte). Die Hauptprobleme sind: „Variationskoeffizient" als Fachbegriff, und die verwirrende negative Prozentzahl bei Kernbotschaften (bedeutet: du sprichst *schneller*, nicht langsamer). Der Report soll den Kernbefund noch deutlicher herausstellen.

**Relevant:** `sprechtempo_analyse.py` → Funktion `erstelle_txt_report()` (Zeile ~472)

**Erwartetes Ergebnis:**
- „Variationskoeffizient" verschwindet aus der Hauptanzeige (bleibt im Hintergrund-Abschnitt)
- Negative Verlangsamung wird als explizite Warnung formuliert: „⚠ Du sprichst bei Kernaussagen SCHNELLER als sonst"
- Silben/Sekunde wird immer mit WPM-Äquivalent angegeben (vertrauter)
- Struktur-Analyse bekommt erklärenden Einleitungssatz
- Referenzwerte werden als beschriftete Tabelle dargestellt

**Todo-Liste:**
1. Dimension 2 „Variation": Variationskoeffizient in Hintergrund-Abschnitt verschieben, stattdessen nur Beurteilung anzeigen
2. Dimension 3 „Kernbotschaften": Wenn Verlangsamung negativ → explizite Warnung-Zeile einfügen (⚠ Schneller statt langsamer)
3. Überall Silben/Sek mit WPM in Klammern ergänzen
4. Struktur-Analyse: Einleitungssatz ergänzen was die Segmente bedeuten
5. Referenzwerte als Tabelle: `Zu langsam | Optimal | Zu schnell` mit konkreten Werten
6. Empfehlungs-Abschnitt basierend auf Score-Bereich

---

## Sub-Task 3 — Füllwörter-Report

**Status:** `[x] done`

**Intent:**
Der Füllwörter-Report verwendet ein anderes Format (`========`) als alle anderen Reports (`======`). Außerdem ist „Heckenausdrücke" ein linguistischer Fachbegriff den normale Nutzer nicht kennen. Der Report soll ins einheitliche Format gebracht und alle Kategorienamen erklärt werden.

**Relevant:** `fuellwoerter_analyse_v2.py` → Report-Schreibung (nach Funktion `erstelle_report` suchen)

**Erwartetes Ergebnis:**
- Format vereinheitlicht mit allen anderen Reports (70 Zeichen `=` / `-`)
- Kategorie 1 „Verzögerungslaute" bleibt, bekommt Beispiele: ähm, äh, öh
- Kategorie 2 „Heckenausdrücke" → umbenannt in „Abschwächende Ausdrücke" mit Erklärung und Beispielen: sozusagen, irgendwie, quasi
- Kategorie 3 „Modalpartikeln + Diskursmarker" → umbenannt in „Füllwörter & Gewohnheitswörter" mit Beispielen: halt, wohl, ja, eigentlich
- Kategorie 4 „Intensivierer" bleibt, bekommt Beispiele: total, extrem, super
- ASCII-Zeitstrahl bekommt erklärenden Einleitungssatz
- Gesamtdichte-Metrik erklärt: was bedeuten 3.7% aller Wörter?
- Empfehlungs-Abschnitt: welche Kategorie hat den größten Einfluss

**Todo-Liste:**
1. Format-Header auf `=` × 70 umstellen
2. Kategorie 2 umbenennen und Erklärung ergänzen
3. Kategorie 3 umbenennen und Erklärung ergänzen
4. Jede Kategorie: Beispielliste „Erkannte Wörter dieser Art: ..." ergänzen
5. Gesamtdichte: Referenzwert erklären (NCT-Studie: Durchschnitt ~5.9%)
6. Zeitstrahl: Einleitungssatz „Zeitlicher Verlauf der Füllwörter im Redeverlauf:"
7. Empfehlungs-Abschnitt: Top-1-Problem benennen und Tipp geben

---

## Sub-Task 4 — Sprechfluss-Report

**Status:** `[x] done`

**Intent:**
Der Sprechfluss-Report enthält eine „Stocker-Info"-Sektion, die auf das Pausen-Modul verweist — das verwirrt Nutzer. Die Begriffe „Disfluenz-Cluster" und „Disfluenz" sind linguistische Fachbegriffe. Der Report soll diese ersetzen und die Stocker-Info verständlich einleiten.

**Relevant:** `sprechfluss_analyse.py` → Funktion `generiere_report()` (Zeile ~476)

**Erwartetes Ergebnis:**
- „Disfluenz" → „Sprechfehler" oder „Unterbrechung des Redeflusses"
- „Disfluenz-Cluster" → „Gehäufte Fehler in einem Satz"
- „Stocker-Info" bekommt erklärendem Einleitungssatz: warum hier (nur informativ), warum im Pausen-Report bewertet
- D1 bekommt Referenzwert: „Gut: 0 Ereignisse/Minute — jede Wiederholung/jeder Abbruch zählt"
- D2 erklärt: was ist eine „sauber" gesprochene Kernaussage
- Empfehlungs-Abschnitt: Tipps zur Vorbereitung (Wiederholungen entstehen aus Unsicherheit)

**Todo-Liste:**
1. „Disfluenz" überall durch „Sprechfehler" ersetzen
2. „Disfluenz-Cluster" → „Mehrere Fehler im gleichen Satz"
3. Stocker-Info-Abschnitt: erklärender Einleitungssatz ergänzen
4. D1: Referenzwert-Tabelle ergänzen (0 = optimal, >3/min = auffällig)
5. D2: erklären was „sauber" bedeutet
6. Disfluenz-Listen: Einleitungssatz „Folgende Stellen klingen stockend oder wurden wiederholt:"
7. Empfehlungs-Abschnitt basierend auf Score

---

## Sub-Task 5 — Lautstärke-Report

**Status:** `[x] done`

**Intent:**
Der Lautstärke-Report zeigt dB-Werte ohne zu erklären was ein dB ist oder was ein „guter" Wert bedeutet. „ΔdB", „Baseline", „Std" sind technische Abkürzungen. Der Report soll alle dB-Werte in verständlichen Kontext setzen.

**Relevant:** `lautstaerke_analyse.py` → Funktion `generiere_report()` (Zeile ~550)

**Erwartetes Ergebnis:**
- dB wird einmal kurz erklärt: „Dezibel — je größer der Wert, desto lauter"
- „Baseline" erklärt: „dein durchschnittlicher Lautstärkepegel während der Präsentation"
- „ΔdB" erklärt: „wie viel lauter oder leiser du an dieser Stelle bist im Vergleich zum Durchschnitt"
- D1 „Kernbotschafts-Betonung": Referenzbereich ergänzen: „Empfehlung: +3 bis +6 dB bei Kernaussagen"
- D2 „Gesamt-Variation": Referenzbereich ergänzen: „Gut: 4–9 dB Streuung, Monoton: unter 3 dB"
- D3 „Struktur-Konsistenz": Einleitungssatz erklären was „im erwarteten Bereich" bedeutet
- Segment-Detail-Tabelle: Spaltenüberschriften klarer beschriften

**Todo-Liste:**
1. Header: kurze dB-Erklärung ergänzen
2. D1: Baseline erklären + Referenzbereich +3 bis +6 dB ergänzen
3. D2: Referenzbereich ergänzen (3–9 dB = gut)
4. D3: Einleitungssatz ergänzen
5. Segment-Tabelle: Spalte „Ist dB" → „Deine Lautstärke (dB)", Spalte „Erwartet" → „Zielbereich (dB)"
6. Empfehlungs-Abschnitt: pro Score-Bereich einen konkreten Tipp

---

## Sub-Task 6 — Tonhöhen-Report

**Status:** `[x] done`

**Intent:**
Der Tonhöhen-Report ist technisch komplex: „Semitones" (Halbtöne), „Endkontur", „KB-Variation-Ratio" sind für Laien völlig unzugänglich. Der Report muss diese Konzepte in Alltagssprache übersetzen und trotzdem präzise bleiben.

**Relevant:** `pitch_variation_analyse.py` → Funktion `generiere_report()` (Zeile ~525)

**Erwartetes Ergebnis:**
- „Semitones / Halbtöne" wird einmal erklärt: „Maßeinheit für Stimmhöhen-Unterschiede. 12 Halbtöne = eine Oktave. Eine normale Stimme variiert 3–6 Halbtöne in einer Unterhaltung."
- „Endkontur" erklärt: „Wie die Stimme am Ende eines Satzes klingt — bei Aussagen sollte sie fallen, bei Fragen steigen"
- D1 „Gesamtvariation": Referenzwerte sichtbar: „Gut: 3–6 Halbtöne, Monoton: unter 1.5, Übertrieben: über 8"
- D2 „End-Kontur" → „Satzmelodie": zeigt pro Satz ob korrekt (✅) oder auffällig (❌)
- D3 „KB-Variation" → „Stimmbetonung bei Kernaussagen": Verhältnis in Alltagssprache
- Satz-Tabelle: Spalten auf Deutsch, technische Werte mit Einheitenangabe
- Monoton-Passagen: warum 8 Sekunden kritisch sind, kurz erklärt

**Todo-Liste:**
1. Header: Einleitungssatz mit Erklärung was Tonhöhenvariaton ist und warum sie wichtig ist
2. Halbtöne-Erklärung als Kasten vor den Dimensionen einfügen
3. D1: Referenzwert-Tabelle (monoton / akzeptabel / optimal / übertrieben)
4. D2 umbenennen auf „Satzmelodie (Stimme am Satzende)"
5. D3 umbenennen auf „Stimmbetonung bei Kernaussagen"
6. Satz-Tabelle: alle Spaltenköpfe auf Deutsch, Einheit angeben
7. Monoton-Passagen: Einleitungssatz ergänzen (warum 8 Sek kritisch)
8. Empfehlungs-Abschnitt: konkreter Übungstipp (z.B. laut vorlesen mit übertriebener Melodie)

---

## Sub-Task 7 — Emotionale-Variation-Report

**Status:** `[x] done`

**Intent:**
Der Emotions-Report ist der am schwersten lesbare. Arousal, Valence und Dominance sind englische Fachbegriffe aus der Emotionspsychologie. Die Zahlenwerte (0.49–0.51) vermitteln keinen intuitiven Eindruck. Der Report muss diese Konzepte vollständig ins Deutsche übersetzen und mit greifbaren Beschreibungen verbinden.

**Relevant:** `emotionale_variation_analyse.py` → Funktion `generiere_report()` (Zeile ~533)

**Erwartetes Ergebnis:**
- „Arousal" → „Aktivierungsniveau" (wie aufgeweckt / energetisch klingt die Stimme)
- „Valence" → „Stimmungsrichtung" (positiv-neutral-negativ)
- „Dominance" → „Stimmkraft / Präsenz" (wie selbstsicher klingt die Stimme)
- Einleitungsabschnitt erklärt: Warum emotionale Variation wichtig ist, was das KI-Modell misst
- Segment-Tabelle: deutsche Spaltenköpfe, Werte mit Interpretationshilfe
  - „0.0 = sehr niedrig, 0.5 = mittel, 1.0 = sehr hoch"
- D1 „Arousal-Wechsel-Rate" → „Abwechslung im Aktivierungsniveau": Referenz TED-Speaker 3–5 Wechsel/Min sichtbar
- D2 „Valence-Passung" → „Passung zum Präsentationston": erklärt was der Zielbereich bedeutet
- D3 „Dominance-Anhebung" → „Stimmkraft bei Kernaussagen": erklärt was Anhebung bedeutet
- Modellname aus dem Hauptteil entfernen (in Hintergrund-Abschnitt)

**Todo-Liste:**
1. Alle drei englischen Begriffe ersetzen (Arousal → Aktivierungsniveau, Valence → Stimmungsrichtung, Dominance → Stimmkraft)
2. Einleitungsabschnitt: 3 Sätze was das KI-Modell tut und misst
3. D1: Umbenennung + TED-Referenzwert sichtbar machen (3–5 Wechsel/Min)
4. D2: Umbenennung + erklären was „Passung" bedeutet (Ton sachlich → neutrale Stimmungsrichtung erwartet)
5. D3: Umbenennung + erklären was Anhebung bedeutet und warum sie bei Kernaussagen wichtig ist
6. Segment-Tabelle: Spalten auf Deutsch, Interpretationshilfe-Zeile ergänzen
7. Kernbotschaft-Passung: Einleitungssatz ergänzen
8. Modellname aus dem Header in Hintergrund-Abschnitt verschieben

---

## Sub-Task 8 — Video-Report

**Status:** `[x] done`

**Intent:**
Der Video-Report wird direkt in `main.py` hardcoded geschrieben — nicht in einer separaten `generiere_report()`-Funktion. Die 5 Dimensionsnamen sind englisch (gesture_variety, body_openness, etc.) und das gesamte Feedback ist auf Englisch. Der Report muss ins Deutsche übersetzt und in das einheitliche Format gebracht werden.

**Relevant:** `main.py` → Funktion `run_video()`, Report-Schreibung ab Zeile ~417
`video_model/infer.py` → Funktion `interpret_scores()` — erzeugt die englischen Feedback-Texte

**Erwartetes Ergebnis:**
- Alle 5 Dimensionen auf Deutsch:
  - `gesture_variety` → „Gestenvielfalt"
  - `body_openness` → „Offene Körperhaltung"
  - `movement_energy` → „Bewegungsenergie"
  - `head_movement` → „Kopfbewegung"
  - `expressiveness` → „Ausdrucksstärke"
- Feedback-Texte auf Deutsch (entweder in `infer.py` übersetzen oder in `run_video()` eine Deutsche Übersetzungs-Map anlegen)
- Report erhält einheitliches Format (70-Zeichen-Trennlinien)
- Einleitungsabschnitt erklärt was das Modell misst (MediaPipe-Skelett → ST-GCN-Netz)
- Score-Interpretation: was bedeuten >0.8, 0.5–0.8, <0.5 in diesem Modell
- Empfehlungs-Abschnitt: nur ausgeben wenn eine Dimension unter 0.6

**Todo-Liste:**
1. `video_model/infer.py`: `interpret_scores()` lesen — prüfen ob Feedback-Texte dort oder in `run_video()` am einfachsten zu übersetzen sind
2. Dimensions-Namen-Map in `run_video()` anlegen (EN → DE)
3. Feedback-Texte übersetzen (entweder in `infer.py` direkt oder via Map in `run_video()`)
4. Report-Header auf einheitliches Format umstellen
5. Einleitungssatz: was misst das Modell und wie
6. Score-Interpretation-Abschnitt ergänzen
7. Empfehlungs-Abschnitt: nur für Dimensionen unter Schwelle

---

## Sub-Task 9 — Inhaltsanalyse-Report

**Status:** `[x] done`

**Intent:**
Der Inhaltsanalyse-Report verwendet `══════` als Trennzeichen statt `======`, was ihn von allen anderen abhebt. Außerdem ist der „Kernbotschaft-Score" (z.B. 0.23) eine KI-interne Zahl ohne Erklärung, und „Höhepunkt" (rhetorischer Peak) ist nicht definiert.

**Relevant:** `inhalt_analyse.py` → Funktion `schreibe_bericht()` (Zeile ~1143)

**Erwartetes Ergebnis:**
- Format auf `=` × 70 und `-` × 70 vereinheitlicht
- Kernbotschaft-Score (0.23) erklärt: „Relevanz-Score der KI: 0 = wenig relevant, 1 = sehr relevant als Kernaussage"
- „Rhetorische Momente" erklärt: Fragen = direkte Publikumsfragen, Höhepunkte = Sätze mit besonderer emotionaler Intensität
- „Publikumsbezug" erklärt: Sätze mit direkter Ansprache (Du, Sie, Wir)
- Warnungen-Abschnitt: Warnungen verständlicher formulieren (kein interner Code-Jargon)
- Footer-Hinweis auf JSON entfernen oder in Hintergrund-Abschnitt verschieben

**Todo-Liste:**
1. Alle `══════` → `======` (70 Zeichen), alle `──────` → `------` (70 Zeichen)
2. Kernbotschaft-Score: Erklärungszeile ergänzen
3. Abschnitt 3 „Kernbotschaften": Einleitungssatz was Kernbotschaften sind und wie sie erkannt werden
4. Abschnitt 4 „Rhetorische Momente": Einleitungssatz + Erklärung von „Höhepunkt"
5. Abschnitt 6 „Publikumsbezug": Einleitungssatz mit Beispielwörtern (Du, Sie, Wir, euch)
6. Warnungen: interne Meldungen in nutzerverständliche Sprache übersetzen
7. Footer: JSON-Hinweis in Hintergrund-Abschnitt verschieben

---

## Sub-Task 10 — Gesamtreport nachschärfen

**Status:** `[x] done`

**Intent:**
Der Gesamtreport wurde bereits überarbeitet (Balken, Ampel, Tipps). Er soll jetzt noch einen kurzen einleitenden Abschnitt bekommen der erklärt wie der Score zustande kommt — damit Nutzer die Gewichtungen verstehen.

**Relevant:** `gesamtscore.py` → Funktion `generiere_report()` (Zeile ~435)

**Erwartetes Ergebnis:**
- Neuer Abschnitt „WIE DER SCORE BERECHNET WIRD" zwischen Gesamtscore und Bereichen
- Erklärung in 3–4 Sätzen: welche Module gehen wie stark ein
- Hinweis: wenn ein Modul übersprungen wurde, wird sein Gewicht umverteilt

**Todo-Liste:**
1. Neuen Abschnitt „WIE DER SCORE BERECHNET WIRD" nach Gesamtscore einfügen
2. Gewichtungs-Tabelle: Stimme & Prosodie 45%, Körpersprache 30%, Inhalt & Sprache 25%
3. Pro Bereich: welche Module darin enthalten sind
4. Hinweis auf Gewichts-Umverteilung bei übersprungenen Modulen ergänzen

---

## Sub-Task 11 — Transkript-Report

**Status:** `[x] done`

**Intent:**
Das Transkript-Format (`Transkripte/*_transkript.txt`) ist ein maschinelles Rohdaten-Format — ein Wort pro Zeile mit Zeitstempel, kein Header, kein lesbarer Fließtext. Das ist bewusst so, damit die Analyse-Module es parsen können und darf **nicht** verändert werden.

Zusätzlich zur Rohdatei soll `transcribe.py` nach der Transkription einen **lesbaren Transkript-Report** erstellen. Dieser zeigt den gesprochenen Text als Fließtext mit Satz-Zeitstempeln, Gesamtdauer, Wortanzahl und einem Hinweis auf KI-bedingte Fehler. Er liegt in `reports/transkript/` und ist im Web-Interface unter „Alle Dateien" sichtbar und herunterladbar.

**Relevant:**
- `transcribe.py` → `schreibe_transkript()` (Zeile ~81) — Rohdatei, nicht anfassen
- `transcribe.py` → neue Funktion `schreibe_transkript_report()` wird ergänzt
- `main.py` → `run_transcribe()` (Zeile ~137) — soll danach auch den Report-Schreiber aufrufen
- `app.py` → `MODUL_REPORT_ORDNER` um `"transkript": "transkript"` ergänzen

**Erwartetes Ergebnis — Aufbau des neuen Reports:**

```
======================================================================
  TRANSKRIPT — LESBARE FASSUNG
======================================================================
  Erstellt:     DD.MM.YYYY HH:MM
  Quelldatei:   [Videoname]
  Modell:       Whisper small
  Sprache:      Deutsch

----------------------------------------------------------------------
  ÜBERSICHT
----------------------------------------------------------------------
  Gesamtdauer:    8.8 Sek (0:08 Min)
  Wörter gesamt:  27
  Wörter/Minute:  ca. 184
  Sätze erkannt:  4

----------------------------------------------------------------------
  GESPROCHENER TEXT (mit Zeitstempeln)
----------------------------------------------------------------------
  [00:00]  Hallo zusammen, ich bin der Robin.
  [00:02]  Ich mache Lehr als Entwickler Digital des Businesses.
  [00:05]  Ich bin wohl dann in zweiter Lehrjahr.
  [00:07]  Minilegisch abteilen ist selbst im Sinn.

----------------------------------------------------------------------
  VOLLSTÄNDIGER TEXT (fortlaufend)
----------------------------------------------------------------------
  Hallo zusammen, ich bin der Robin. Ich mache Lehr als Entwickler
  Digital des Businesses. Ich bin wohl dann in zweiter Lehrjahr.
  Minilegisch abteilen ist selbst im Sinn.

----------------------------------------------------------------------
  HINWEIS
----------------------------------------------------------------------
  Dies ist eine automatische Transkription durch Whisper (KI-Modell).
  Fehler sind möglich — besonders bei Eigennamen, Fachbegriffen und
  undeutlicher Aussprache.
  Die Rohdatei mit Wort-Zeitstempeln liegt unter:
  Transkripte/[Dateiname]_transkript.txt

======================================================================
  ENDE REPORT
======================================================================
```

**Todo-Liste:**
1. `transcribe.py`: Neue Funktion `schreibe_transkript_report(words, output_path, video_name, model_name)` schreiben:
   - Wörter zu Sätzen zusammenfassen (Satzgrenze = Wort endet auf `.` `!` `?` oder Pause > 0.8 s zwischen Wörtern)
   - Gesamtdauer, Wortanzahl, WPM berechnen
   - Fließtext auf ca. 70 Zeichen pro Zeile umbrechen
   - Report im einheitlichen Format (`=` × 70 / `-` × 70) schreiben
2. `main.py`: In `run_transcribe()` nach `schreibe_transkript()` aufrufen:
   - Output-Pfad: `reports/transkript/transkript_report_<TIMESTAMP>.txt`
   - Ordner `reports/transkript/` anlegen falls nicht vorhanden
3. `app.py`: `MODUL_REPORT_ORDNER` um `"transkript": "transkript"` ergänzen — damit der Report über `/api/report/transkript/txt` und `/api/report/transkript/download` abrufbar ist

---

## Nicht im Scope

- Die Berechnungslogik der Scores wird **nicht** verändert — nur die Report-Ausgabe
- Die JSON-Zwischenoutputs werden **nicht** verändert
- Die Web-UI wird **nicht** verändert (nutzt die Reports automatisch)
- `sprechtempo_analyse.py` und `pausen_analyse.py` Logik bleibt unverändert
- Das Rohdaten-Transkript (`*_transkript.txt`) wird **nicht** verändert — es bleibt im maschinellen Format
