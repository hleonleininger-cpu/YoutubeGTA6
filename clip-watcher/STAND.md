# STAND: clip-watcher (Stand 18.11.2026, 15:50)

Für das Code-Tool: Lies diese Datei zuerst, dann `watcher.py`, `app.py`, `hook.py`, `config.json`. Danach den Prompt „clip-watcher erweitern“ aus dem Projekt abarbeiten.

## Zweck
Deutscher YouTube-Shorts-Kanal (GTA 6, Gaming) von Nils. Der Watcher nimmt Twitch-Streams auf, erkennt Momente, schneidet 9:16-Clips mit Untertiteln. **Nichts wird automatisch gepostet.** Nils lädt selbst hoch.

## Dateien
- `watcher.py` Kern. Streamlink -> FFmpeg (10-s-Segmente, Ringpuffer), anonymer Twitch-Chat (IRC, justinfan, ohne Token), Ausschlag-Erkennung (Chat-Score gegen Median der letzten 30 Fenster à 10 s), Schnitt, Whisper-Untertitel. Zusätzlich Datei-Modus `--datei x.mp4 --rechte eigen` (Lautstärke-Spitzen).
- `app.py` startet den Watcher als Unterprozess + Oberfläche (stdlib `http.server`, Port 8765, `--lan` fürs Handy). Tabs: Neu, Gut, Verworfen, Kanäle, Protokoll. Status pro Clip in `status.txt`.
- `hook.py` Hook-Text per ffmpeg `drawtext` -> `short_hook.mp4`.
- `config.json` Kanäle (name, rechte, nachweis) und alle Einstellungen. Clix ist als `kampagne` mit Nachweis `https://reachclipping.com/Clix` eingetragen.
- `installieren.*`, `starten.*`, `starten-handy.bat`, `ANLEITUNG.md`.

## Ablauf pro Clip (Eilmodus, zwei Phasen)
1. Ausschlag erkannt -> Segmente zusammenfügen -> `roh.mp4`.
2. Phase 1: 9:16 (Layout `unschaerfe` oder `mitte`) + Zoom auf die Pointe -> `short.mp4`, `info.txt`, `vorschau.jpg`, Zeile in `clips/liste.csv`. Sofort sichtbar.
3. Phase 2 (Hintergrund-Queue `UT_QUEUE`): Whisper -> `untertitel.srt`, `clip.ass`, `woerter.json`, `short.mp4` wird ersetzt, Marker `untertitel.fertig`/`untertitel.keine`. War schon ein Hook gesetzt, wird er neu angewendet.

## Rechte-Sperre (nicht entfernen)
Kanal startet nur, wenn `rechte` in {eigen, kampagne, erlaubnis} und bei kampagne/erlaubnis ein `nachweis` steht. Gilt in `watcher.py` und im Kanal-Formular von `app.py`.

## Getestet
- Gesamtkette mit simuliertem Stream + simuliertem Chat (Whisper durch Attrappe ersetzt): Ausschlag erkannt, Clip 1080x1920, Zoom, Untertitel, Hook.
- Datei-Modus, Rechte-Sperre, Oberfläche (API, Video mit Range-Requests, Pfadschutz, Handy-Ansicht).

## NICHT getestet
- **Echter Twitch** (Live-Erkennung per `streamlink --json`, echter Stream, echter Chat): Entwicklungsumgebung hatte keinen Zugriff auf Twitch/YouTube/Hugging Face.
- **Echtes Whisper** (Modell-Download, Laufzeit auf Nils' PC, Wortzeiten).
- **Eilmodus** (zwei Phasen): nur Syntaxprüfung, noch nie gelaufen. Zuerst mit Datei-Modus und langsamer Whisper-Attrappe testen.
- Windows-Besonderheiten: Pfade im ffmpeg-`ass`-Filter (läuft relativ im Clip-Ordner), `concat:`-Segmente, Schrift „Arial“ (Mac: Helvetica).

## Bekannte Schwächen / Ideen
- Signalwörter (`SIGNAL`) und Schwellen (`min_score`, `schwelle_faktor`, `chat_verzug_s`) sind geschätzt, nach den ersten echten Clips einstellen.
- Kein Schutz gegen Musik/Spoiler/Zwischensequenzen: Nils sieht jeden Clip vor dem Posten an.
- Oberfläche ohne Passwort: nur localhost oder eigenes WLAN.

## Regeln des Projekts (immer einhalten)
1. Rechte vor Qualität: nur eigenes Material, Kampagnenmaterial oder schriftliche Erlaubnis.
2. GTA 6: keine Leaks, keine Story-Spoiler, keine Zwischensequenzen allein.
3. Eigener Beitrag bei fremdem Material: mindestens zwei von Hook-Text, Untertitel, Zoom, Kommentar.
4. Nichts automatisch veröffentlichen, nur nach gezeigtem Entwurf und klarem Ja von Nils.
5. Keine Umgehungen, keine gekauften Aufrufe, keine Bots.
6. Bezahlte Clips: „Werbung“ sichtbar. Kampagnen-Pflichtangaben wörtlich übernehmen (Clix: Name + #Clix, Einreichung innerhalb 30 Minuten).
7. Spalten von `clips/liste.csv` nicht ändern: Datum, Plattform, Clip, Link, Quelle, Hook-Typ, Länge, Aufrufe, Durchgesehen %, Kampagne, Rate pro 1000, Deckel, Gebühr %, Mindestauszahlung, Status, Notiz.
