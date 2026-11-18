# clip-watcher

Wartet, bis ein Twitch-Kanal live geht, nimmt auf, liest den Chat ohne Account mit,
erkennt Ausschläge und schneidet fertige 9:16-Shorts mit Untertiteln.
**Gepostet wird nichts automatisch.** Du siehst dir jeden Clip an.

## Einrichten (einmalig, 5 Minuten)
1. Windows: `installieren.bat` doppelklicken. Mac: `installieren.command`.
2. `config.json` öffnen, bei `kanaele` den Kanal eintragen:
   - `"rechte": "eigen"` = dein eigener Kanal / eigene Aufnahme
   - `"rechte": "kampagne"` oder `"erlaubnis"` = fremder Streamer. **Dann muss bei `nachweis` ein Link oder eine
     Notiz stehen**, wo er Clips erlaubt (Twitch-Infofeld, Discord-Regel, Kampagnenseite). Sonst startet der Kanal nicht.
3. `starten.bat` / `starten.command` doppelklicken. Es öffnet sich die Oberfläche im Browser (http://localhost:8765). Fenster offen lassen.
   Vom Handy im selben WLAN: `starten-handy.bat` (Mac: `python3 app.py --lan`) und dann `http://<IP-deines-PCs>:8765` öffnen. Nur im eigenen WLAN nutzen, die Oberfläche hat kein Passwort.

Mehrere Kanäle: einfach weitere Blöcke in `kanaele` kopieren.

## Oberfläche
Links die Seitenleiste (auf dem Handy unten), unten links die Watcher-Karte (aus, bereit, REC) und die Version.
- **Übersicht:** was gerade los ist. Kennzahlen, Kampagnen-Clips mit laufender Frist, Clips zum Sichten, beste Shorts, Kanäle, Aktivität. Solange etwas fehlt, steht dort die Einrichtung (Kanal, Watcher, API-Schlüssel, Google-Konto, erster Link).
- **Clips:** der Schnittplatz. Links Neu/Gut/Verworfen, in der Mitte das Video mit Zeitleiste (Hook-Fenster und Pointe), rechts Hook, Caption und Posten. Hook-Vorschau erscheint beim Tippen im Video.
- **Entdecken:** welche Twitch-Streams gerade live sind (Sprache, Kategorie, Mindest-Zuschauer), sortiert nach Clip-Potenzial. „Grundlage prüfen“ sucht im Profiltext, Titel und in den Tags nach Hinweisen auf eine Clip-Erlaubnis. Das ist nur ein Hinweis: Du prüfst selbst, trägst den Nachweis ein, bestätigst und übernimmst den Kanal. Ohne Nachweis und Bestätigung geht es nicht. Braucht einen kostenlosen Twitch-Zugang (Einstellungen → Twitch-Zugang). YouTube Live ist nur zur Ansicht.
- **Tracker:** Aufrufe pro Short als Balken, Tabelle, Auswertung nach Hook-Typ und Länge.
- **Trends:** Muster aus aktuellen GTA-6-Shorts.
- **Kanäle:** überwachte Kanäle mit Live-Status, Kanal hinzufügen (fremde nur mit Nachweis), Watcher starten/stoppen.
- **Einstellungen:** Clip-Auswahl, Clip-Länge, Speicher, Aussehen (vier Farbstile), Benachrichtigungen (Desktop-Meldung und Ton bei neuem Clip), Twitch-Zugang, YouTube-Daten (API-Schlüssel), YouTube-Konto und Upload, Programm-Version und Update.
- **Protokoll:** was der Watcher gerade macht.
- **Tastenkürzel:** `?` zeigt alle. `G` gut, `W` weg, `↑`/`↓` Clip wechseln, `Leertaste` Play/Pause, `←`/`→` eine Sekunde, `M` Ton.
- Nur den Watcher ohne Oberfläche: `python watcher.py`.

## Ergebnis
Ordner `clips/<Datum>/<kanal>_<Uhrzeit>/`:
- `short.mp4` fertiger Clip (1080x1920, Untertitel, Zoom auf die Pointe)
- `untertitel.srt` für den YouTube-Upload
- `info.txt` Auslöser, Chat-Wörter, was gesagt wurde, Checkliste
- `vorschau.jpg`, `roh.mp4` (ungeschnitten, falls du anders schneiden willst)
- `clips/liste.csv` = Tracker, Spalten wie im Projekt (in Excel öffnen)

## Hook-Text einbauen
`python hook.py "clips/2026-11-19/kanal_201533" "Das hat keiner kommen sehen"` -> `short_hook.mp4`
(max. 8 Wörter, erscheint oben in den ersten 2,5 Sekunden)

## Eigene Aufnahme (PS5/Xbox-Datei)
`python watcher.py --datei aufnahme.mp4 --rechte eigen`
findet die lautesten Stellen und schneidet daraus Clips.

## Einstellungen (config.json)
- `auswahl`: `locker`, `normal`, `streng` (Standard) oder `highlights`. Steuert, wie stark der Chat ausschlagen muss und wie viele Clips pro Stunde höchstens entstehen. Einstellbar im Tab „Kanäle“, wirkt sofort.
- `layout`: `unschaerfe` (ganzes Bild, unscharfer Hintergrund) oder `mitte` (Mittenschnitt)
- `whisper_modell`: `small` ist gut für Deutsch; `base` ist schneller, `medium` genauer
- `clip_laenge_s`: Länge der Clips in Sekunden (Standard 18, einstellbar 15 bis 20 im Tab „Kanäle“). Der Höhepunkt liegt bei etwa zwei Dritteln.

## Vor jedem Post
Clip ansehen. Kein Spoiler, keine Zwischensequenz allein, kein Leak, keine laute Musik. Eigener Hook/Kommentar drauf.
Credit in die Beschreibung. Bezahlter Clip: „Werbung“ sichtbar.

## Wenn etwas nicht klappt
- „FFmpeg/Streamlink fehlt“: Installation wiederholen, neues Fenster öffnen.
- Kanal bleibt bei „Warte“: er ist nicht live, oder der Name ist falsch (nur der Name, ohne twitch.tv/).
- Erste Untertitel dauern länger, Whisper lädt einmalig sein Modell (ca. 500 MB).
- Ohne Whisper entstehen Clips trotzdem, nur ohne Untertitel.
- Untertitelschrift: bei Mac in `config.json` `"schrift": "Helvetica"` setzen.

## Trends, Tracker, Caption, Dauerbetrieb (neu)
- **API-Schlüssel:** In `config.json` bei `youtube_api_key` eintragen oder im Tab „Trends“ einfügen (Anleitung steht dort). Ohne Schlüssel laufen Trends und Tracker nicht, der Rest schon.
- **Trends** (`trend.py`): alle 3 Stunden eine Suche „GTA 6“ (DE, kurz, letzte 72 h). Höchstens 20 Suchen pro Tag (je 100 Quota-Punkte). Ergebnis in `trend.json`. Muster nur, wenn mindestens 3 Kanäle sie zeigen.
- **Tracker** (`tracker.py`): stündlich Aufrufe, Likes, Kommentare der Clips, die in der Karte einen YouTube-Link haben. Aufrufe landen in `clips/liste.csv`, Rest in `tracker.json`.
- **Caption:** Knopf pro Clip, Titel (max. 100 Zeichen), Beschreibung mit Credit, Hashtags. Bei `kampagne` mit „Werbung“ und den Pflichtangaben aus dem Nachweis.
- **Dauerbetrieb:** siehe `dauerbetrieb.txt`. Aufräum-Werte in `config.json` unter `aufraeumen`, Festplattenwarnung über `min_frei_gb`.

## YouTube-Konto, Durchsehrate, optionaler Upload
- `client_secret.json` (OAuth-Client „Desktop-App“) selbst aus der Google Cloud Console laden und in den Programmordner legen. YouTube Analytics API aktivieren, dich als Testnutzer eintragen.
- Tab „Tracker“ > „Mit Google verbinden“. Nur lesende Rechte. Das Token liegt in `token.json` (nur lokal, nie in die ZIP packen). „Trennen“ löscht es.
- Spalte „Durchgesehen %“ und Auswertung nach Hook-Typ (Feld pro Clip) und Länge: erst ab 10 Clips pro Gruppe, Median.
- Upload ist standardmäßig aus (`upload_aktiv` in `config.json` oder Schalter im Tracker-Tab, danach neu verbinden). Nur Clips mit Status „Gut“, nur per Knopf „Hochladen …“ und „Ja, jetzt hochladen“, Sichtbarkeit immer ausdrücklich, Standard privat.
