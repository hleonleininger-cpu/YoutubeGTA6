# Designplan clip-watcher

Stand: 18.11.2026. Ziel: Die App soll sich wie ein fertiges Produkt anfühlen, das man verkaufen kann. Sie soll sofort zeigen, was gerade wichtig ist, und die tägliche Arbeit (sichten, Hook, Caption, posten) so schnell wie möglich machen.

## 1. Befund (Audit mit Demodaten)

| Bereich | Problem | Folge |
|---|---|---|
| Start | App öffnet direkt im Clip-Sichten, kein Überblick | Man sieht nicht, wer live ist, was wartet, welche Frist abläuft |
| Tracker | Zwei große Einrichtungs-Texte stehen über den Zahlen | Die eigentlichen Daten sind unter dem Falz |
| Tracker | Nur Kacheln und Tabelle, kein Diagramm | Man erkennt Gewinner und Verlierer nicht auf einen Blick |
| Einstellungen | Verstreut über Kanäle, Tracker und Trends | Man sucht, wo was eingestellt wird |
| Konto-Box | Checkbox verrutscht | Wirkt unfertig |
| Neue Clips | Keine Benachrichtigung | Bei Kampagnen mit 30-Minuten-Frist geht Zeit verloren |
| Einrichtung | Kein geführter Einstieg | Neue Nutzer wissen nicht, was fehlt |
| Tastatur | Kürzel nur in einer Zeile unter dem Video | Keine Übersicht |

## 2. Prinzipien

1. **Das Wichtigste zuerst.** Jede Seite beginnt mit der Antwort auf „Was muss ich jetzt tun?“.
2. **Ein Ort pro Aufgabe.** Sichten im Schnittplatz, Zahlen im Tracker, alles Einstellbare unter Einstellungen.
3. **Zustand in Form, nicht nur in Text.** Live = rote REC-Marke, Frist = Balken, Fortschritt = Striche, Erfolg = Balkenlänge.
4. **Ruhig und dunkel, ein Akzent pro Rolle.** Koralle für Hook, Pointe und Hauptaktion. Aqua für Abspielkopf, Auswahl, „läuft“.
5. **Nichts geht automatisch raus.** Upload bleibt aus, bis man ihn einschaltet, mit Bestätigung.

## 3. Informationsarchitektur

Seitenleiste statt Reiter (wie bei professionellen Werkzeugen), auf dem Handy eine Leiste unten.

```
Übersicht      neu: Startseite
Clips (4)      Schnittplatz: Ablage, Player, Arbeitsbereich
Tracker        Leistung der hochgeladenen Shorts
Trends         Muster aus aktuellen GTA-6-Shorts
Kanäle         Quellen mit Live-Status
Einstellungen  neu: alles Einstellbare an einem Ort
Protokoll      technisches Protokoll (nachrangig)
---
Watcher-Karte  läuft / REC / aus, Start und Stopp
Version        Update prüfen
```

## 4. Seiten

**Übersicht**
- Begrüßung mit einem Satz zur Lage („4 Clips warten, 2 Kanäle live“).
- Einrichtung (nur solange unvollständig): Kanal, Watcher, API-Schlüssel, Google-Konto, erster Link. Fortschrittsbalken, jeder Punkt mit Sprungknopf.
- Kennzahlen: Zu sichten, Heute geschnitten, Hochgeladen (7 Tage), Aufrufe (7 Tage).
- Fristen: Kampagnen-Clips mit laufendem Countdown, direkt öffnen.
- Live-Kanäle mit Clips heute.
- Aktivität: die letzten Ereignisse aus dem Protokoll, mit Symbolen.
- Beste Clips nach Aufrufen als Balken.

**Tracker**: Einrichtung raus (nur ein kurzer Hinweis mit Link zu Einstellungen). Kennzahlen, Balkendiagramm „Aufrufe pro Clip“, Tabelle, Auswertung nach Hook-Typ und Länge.

**Trends**: Einrichtung raus. Muster als Karten, Tabelle darunter.

**Kanäle**: Nur Quellen. Je Kanal: Live-Status, Rechte, Nachweis, Clips heute.

**Einstellungen** in Abschnitten: Clip-Auswahl, Clip-Länge, Speicher, Benachrichtigungen, YouTube-Daten (API-Schlüssel), YouTube-Konto und Upload, Version.

## 5. Designsystem

| Token | Wert | Rolle |
|---|---|---|
| nacht | #100f17 | Grund |
| deck 1–4 | #16141f … #302b41 | Flächen nach Höhe |
| ink 1–3 | #f1eef9 / #a7a0bb / #6f6985 | Text nach Wichtigkeit |
| koralle | #ff6b8b | Hook, Pointe, Hauptaktion, REC |
| aqua | #4fd6c8 | Auswahl, Abspielkopf, „aktiv“ |
| gut / weg / bernstein | #63dba5 / #ff7d7d / #ffbf5e | nur Zustände |

Schrift: Space Grotesk für Titel und Zahlen, Inter für Text, JetBrains Mono für Zeiten und Kennungen. Abstände in 4er-Schritten. Ecken: 16 für Flächen, 10 für Bedienelemente, 6 für Marken.

## 6. Neue Funktionen, die zum Design gehören

- **Benachrichtigung bei neuem Clip** (Browser-Meldung plus kurzer Ton, abschaltbar). Bei Kampagnen-Clips mit Frist im Text.
- **Tastenkürzel-Übersicht** mit `?`.
- **Einrichtungs-Assistent** auf der Übersicht.

## 7. Umsetzung (Reihenfolge)

1. Seitenleiste und Navigation, Handy-Leiste unten.
2. Übersicht mit Einrichtung, Kennzahlen, Fristen, Live, Aktivität, Bestenliste.
3. Einstellungen bündeln, Tracker und Trends entrümpeln, Konto-Box neu.
4. Diagramm im Tracker.
5. Benachrichtigungen und Kürzel-Übersicht.
6. Test aller Seiten mit Demodaten, Desktop und Handy, dann Update.

## 8. Umgesetzt am 18.11.2026

Punkte 1 bis 6 sind gebaut und mit Demodaten getestet (Desktop 1440 und 1280, Handy 390): Seitenleiste und Handy-Leiste, Übersicht mit Einrichtung, Kennzahlen, Fristen, Zu-sichten-Streifen, Bestenliste, Kanälen und Aktivität; Einstellungen gebündelt mit Sprungleiste und Schaltern; Tracker mit Balkendiagramm; Benachrichtigungen mit Ton; Kürzel-Übersicht.

## 9. Später (nicht in diesem Schritt)

- Helles Design als Option.
- Mehrere Profile oder Kanäle für Teams.
- Einnahmen pro Kampagne (Rate pro 1.000 Aufrufe) direkt im Tracker ausrechnen.
- Installer mit eigenem Symbol statt Batch-Dateien.
