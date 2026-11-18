#!/usr/bin/env python3
"""
clip-watcher: wartet, bis ein Twitch-Kanal live geht, nimmt mit Streamlink auf,
liest den Chat ohne Account mit, erkennt Ausschläge und schneidet daraus fertige
9:16-Shorts mit Untertiteln. Gepostet wird nichts automatisch.

Start:        python watcher.py
Eigene Datei: python watcher.py --datei aufnahme.mp4 --rechte eigen
Hilfe:        python watcher.py -h
"""
import argparse, csv, json, os, platform, queue, re, shutil, socket, statistics
import subprocess, sys, threading, time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

BASIS = Path(__file__).resolve().parent
ERLAUBT = {"eigen", "kampagne", "erlaubnis"}
SEG_S = 10  # Länge der Aufnahme-Stücke in Sekunden

# Wörter, die im Chat fast immer eine Reaktion auf etwas Sehenswertes sind
SIGNAL = {
    "lol", "lmao", "lul", "kek", "kekw", "omegalul", "haha", "hahaha", "xd", "xdd",
    "wtf", "was", "alter", "digga", "krass", "insane", "nochmal", "pog", "pogchamp",
    "poggers", "omg", "bruh", "nein", "jaaa", "geil", "brutal", "wow", "w", "lets",
    "weg", "lachen", "icant", "pepelaugh", "monkas", "sheesh",
}

DRUCK = threading.Lock()


def log(quelle, text):
    with DRUCK:
        print(f"[{datetime.now():%H:%M:%S}] [{quelle}] {text}", flush=True)


def lade_config():
    pfad = BASIS / "config.json"
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def clip_fenster(cfg):
    """Sekunden vor und nach dem Höhepunkt. clip_laenge_s (Standard 18) ersetzt vorlauf_s/nachlauf_s.
    Der Höhepunkt liegt bei etwa zwei Dritteln, damit der Clip auf die Pointe zuläuft und kurz danach endet."""
    try:
        laenge = float(cfg.get("clip_laenge_s", 18))
    except (TypeError, ValueError):
        laenge = 18.0
    laenge = min(max(laenge, 8.0), 60.0)
    vor = round(laenge * 0.65)
    return vor, laenge - vor


def pruefe_rechte(rechte, nachweis):
    """Rechte vor Qualität: ohne Grundlage wird nichts aufgenommen."""
    r = str(rechte or "").strip().lower()
    if r not in ERLAUBT:
        return False, f"Rechte '{rechte}' unbekannt. Erlaubt: eigen, kampagne, erlaubnis."
    if r != "eigen" and not str(nachweis or "").strip():
        return False, "Bei 'kampagne' oder 'erlaubnis' muss unter 'nachweis' ein Link oder eine Notiz stehen."
    return True, r


def ffmpeg_da():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("FFmpeg fehlt. Erst installieren.bat (Windows) oder installieren.command (Mac) ausführen.")


def streamlink_cmd():
    try:
        import streamlink  # noqa: F401
        return [sys.executable, "-m", "streamlink"]
    except ImportError:
        if shutil.which("streamlink"):
            return ["streamlink"]
    sys.exit("Streamlink fehlt. Erst installieren.bat bzw. installieren.command ausführen.")


def proben(datei):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height:format=duration", "-of", "json", str(datei)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    info = json.loads(out or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    hat_ton = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    dauer = float(info.get("format", {}).get("duration", 0) or 0)
    return v.get("width", 1920), v.get("height", 1080), hat_ton, dauer


# ---------------------------------------------------------------- Chat

class Chat(threading.Thread):
    """Liest den Twitch-Chat anonym (justinfan-Login, kein Account, kein Token)."""

    def __init__(self, kanal, stop, test_datei=None):
        super().__init__(daemon=True)
        self.kanal, self.stop, self.test_datei = kanal.lower(), stop, test_datei
        self.nachrichten = deque()
        self.lock = threading.Lock()
        self.verbunden = False

    def _rein(self, text):
        with self.lock:
            jetzt = time.time()
            self.nachrichten.append((jetzt, text))
            while self.nachrichten and self.nachrichten[0][0] < jetzt - 900:
                self.nachrichten.popleft()

    def hole(self, von, bis):
        with self.lock:
            return [t for (w, t) in self.nachrichten if von <= w < bis]

    def run(self):
        if self.test_datei:
            return self._test()
        while not self.stop.is_set():
            try:
                self._verbinde()
            except Exception as e:  # Verbindung weg: kurz warten, neu verbinden
                self.verbunden = False
                if not self.stop.is_set():
                    log(self.kanal, f"Chat getrennt ({e}), verbinde neu …")
                    time.sleep(5)

    def _verbinde(self):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
        s.settimeout(1.0)
        nick = f"justinfan{int(time.time()) % 100000}"
        s.sendall(f"NICK {nick}\r\nJOIN #{self.kanal}\r\n".encode())
        self.verbunden = True
        log(self.kanal, "Chat verbunden (ohne Account).")
        puffer, letzte = "", time.time()
        while not self.stop.is_set():
            try:
                daten = s.recv(4096)
                if not daten:
                    raise ConnectionError("Server hat getrennt")
                letzte = time.time()
            except socket.timeout:
                if time.time() - letzte > 360:
                    raise ConnectionError("keine Daten seit 6 Minuten")
                continue
            puffer += daten.decode("utf-8", "ignore")
            *zeilen, puffer = puffer.split("\r\n")
            for z in zeilen:
                if z.startswith("PING"):
                    s.sendall(("PONG" + z[4:] + "\r\n").encode())
                elif " PRIVMSG #" in z:
                    self._rein(z.split(" :", 1)[-1] if " :" in z else "")
        s.close()

    def _test(self):
        """Test: Zeilen 'sekunde<TAB>text' werden zeitgenau abgespielt."""
        start = time.time()
        self.verbunden = True
        for zeile in open(self.test_datei, encoding="utf-8"):
            if "\t" not in zeile:
                continue
            sek, text = zeile.rstrip("\n").split("\t", 1)
            while time.time() - start < float(sek):
                if self.stop.is_set():
                    return
                time.sleep(0.05)
            self._rein(text)


def chat_score(nachrichten):
    score = 0.0
    for t in nachrichten:
        klein = t.lower()
        woerter = set(re.findall(r"\w+", klein))
        score += 1
        if woerter & SIGNAL:
            score += 1
        if "clip" in klein:
            score += 2
    return score


def top_woerter(nachrichten, n=8):
    z = Counter()
    for t in nachrichten:
        for w in set(re.findall(r"[\wäöüß]+", t.lower())):
            if len(w) > 1:
                z[w] += 1
    return [f"{w} ({c})" for w, c in z.most_common(n)]


# ---------------------------------------------------------------- Aufnahme

class Aufnahme:
    """Streamlink -> FFmpeg, gespeichert in 10-Sekunden-Stücken (Ringpuffer)."""

    def __init__(self, ordner, quelle_cmd):
        self.ordner = Path(ordner)
        self.ordner.mkdir(parents=True, exist_ok=True)
        self.liste = self.ordner / "segmente.csv"
        self.quelle_cmd = quelle_cmd
        self.segmente = []          # (datei, start, ende) – Zeiten ab Aufnahmebeginn
        self.start_wall = None      # Uhrzeit, die Aufnahme-Sekunde 0 entspricht
        self._null = None
        self.lock = threading.Lock()

    def starte(self):
        self.p1 = subprocess.Popen(self.quelle_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.p2 = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
             "-f", "segment", "-segment_time", str(SEG_S),
             "-segment_list", "segmente.csv", "-segment_list_type", "csv",
             "-reset_timestamps", "1", "seg_%06d.ts"],
            stdin=self.p1.stdout, stderr=subprocess.DEVNULL, cwd=self.ordner)
        self.p1.stdout.close()

    def aktualisiere(self):
        if not self.liste.exists():
            return
        with open(self.liste, encoding="utf-8", newline="") as f:
            zeilen = [z for z in csv.reader(f) if len(z) >= 3]
        with self.lock:
            if not zeilen:
                return
            if self._null is None:
                self._null = float(zeilen[0][1])
                self.start_wall = time.time() - (float(zeilen[0][2]) - self._null)
            self.segmente = [(z[0], float(z[1]) - self._null, float(z[2]) - self._null) for z in zeilen]

    def ende(self):
        with self.lock:
            return self.segmente[-1][2] if self.segmente else 0.0

    def laeuft(self):
        return self.p2.poll() is None

    def rec_zeit(self, wall):
        return None if self.start_wall is None else wall - self.start_wall

    def schneide(self, von, bis, ziel):
        with self.lock:
            teile = [s for s in self.segmente if s[2] > von and s[1] < bis and (self.ordner / s[0]).exists()]
        if not teile:
            return False
        versatz = max(0.0, von - teile[0][1])
        quelle = "concat:" + "|".join(t[0] for t in teile)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", quelle, "-ss", f"{versatz:.2f}",
                            "-t", f"{bis - von:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(Path(ziel).resolve())],
                           cwd=self.ordner, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode == 0 and Path(ziel).exists()

    def aufraeumen(self, behalten_s, geschuetzt_ab):
        grenze = min(self.ende() - behalten_s, geschuetzt_ab)
        with self.lock:
            for datei, _, e in self.segmente:
                if e < grenze:
                    p = self.ordner / datei
                    if p.exists():
                        try:
                            p.unlink()
                        except OSError:
                            pass

    def stop(self):
        for p in (self.p1, self.p2):
            if p.poll() is None:
                p.terminate()
        for p in (self.p1, self.p2):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------------------------------------------------------------- Untertitel

_WHISPER = {"modell": None, "fehler": None}
_WHISPER_LOCK = threading.Lock()


def transkribiere(datei, cfg):
    """Wort-Zeitstempel mit faster-whisper. None, wenn Whisper fehlt."""
    with _WHISPER_LOCK:
        if _WHISPER["fehler"]:
            return None
        if _WHISPER["modell"] is None:
            try:
                from faster_whisper import WhisperModel
                log("whisper", f"Lade Modell '{cfg.get('whisper_modell', 'small')}' (beim ersten Mal ein paar Minuten) …")
                _WHISPER["modell"] = WhisperModel(cfg.get("whisper_modell", "small"), device="cpu", compute_type="int8")
            except Exception as e:
                _WHISPER["fehler"] = str(e)
                log("whisper", f"Whisper nicht verfügbar, Clips kommen ohne Untertitel: {e}")
                return None
        segs, _ = _WHISPER["modell"].transcribe(str(datei), language=cfg.get("sprache", "de"),
                                                word_timestamps=True, vad_filter=True)
        return [{"start": w.start, "end": w.end, "word": w.word.strip()}
                for s in segs for w in (s.words or []) if w.word.strip()]


def korrigiere(wort):
    ersatz = {"gta": "GTA", "vicecity": "Vice City", "leonida": "Leonida", "lucia": "Lucia", "jason": "Jason"}
    return ersatz.get(re.sub(r"\W", "", wort.lower()), wort)


def bloecke(woerter, max_w=3, max_c=18):
    chunks, cur = [], []
    for w in woerter:
        neu = " ".join(x["word"] for x in cur + [w])
        pause = cur and w["start"] - cur[-1]["end"] > 0.5
        if cur and (len(cur) >= max_w or len(neu) > max_c or pause or cur[-1]["word"][-1] in ".!?"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def zeit_ass(s):
    cs = int(round(max(s, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def zeit_srt(s):
    ms = int(round(max(s, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def schreibe_untertitel(woerter, ordner, schrift):
    for w in woerter:
        w["word"] = korrigiere(w["word"])
    # .srt satzweise (für den YouTube-Upload)
    saetze = bloecke(woerter, 8, 32)
    with open(ordner / "untertitel.srt", "w", encoding="utf-8") as f:
        for i, c in enumerate(saetze, 1):
            f.write(f"{i}\n{zeit_srt(c[0]['start'])} --> {zeit_srt(max(c[-1]['end'], c[0]['start'] + 0.4))}\n"
                    f"{' '.join(x['word'] for x in c)}\n\n")
    # .ass Wort-Stil: 1-3 Wörter, Großbuchstaben, aktuelles Wort gelb, über der Bedienleiste
    kopf = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clip,{schrift},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,90,90,620,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    zeilen = []
    for c in bloecke(woerter):
        e = max(c[-1]["end"], c[0]["start"] + 0.35)
        for i, w in enumerate(c):
            bis = c[i + 1]["start"] if i + 1 < len(c) else e
            txt = " ".join(("{\\c&H00FFFF&}" + x["word"].upper() + "{\\c&HFFFFFF&}") if j == i
                           else x["word"].upper() for j, x in enumerate(c))
            zeilen.append(f"Dialogue: 0,{zeit_ass(w['start'])},{zeit_ass(bis)},Clip,,0,0,0,,{txt}")
    (ordner / "clip.ass").write_text(kopf + "\n".join(zeilen) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- Schnitt 9:16

def filter_9x16(layout, b, h, zoom_t, mit_ass, hat_ton):
    zoom = zoom_t is not None and zoom_t > 0.5
    fh = int(round(1080 * h / b / 2)) * 2  # Höhe des Vordergrunds, gerade Zahl

    def zweig(eingang, basis, zoomed, aus):
        if not zoom:
            return f"[{eingang}]{basis}[{aus}];"
        return (f"[{eingang}]split=2[{aus}1][{aus}2];"
                f"[{aus}1]trim=end={zoom_t:.2f},setpts=PTS-STARTPTS,{basis}[{aus}a];"
                f"[{aus}2]trim=start={zoom_t:.2f},setpts=PTS-STARTPTS,{zoomed}[{aus}b];"
                f"[{aus}a][{aus}b]concat=n=2:v=1:a=0[{aus}];")

    if layout == "mitte":
        g = zweig("0:v", "crop=ih*9/16:ih,scale=1080:1920,setsar=1",
                  "crop=ih*9/16/1.25:ih/1.25,scale=1080:1920,setsar=1", "v0")
        letzter = "v0"
    else:  # unschaerfe: ganzes Bild sichtbar, unscharfer Hintergrund
        g = ("[0:v]split=2[bgi][fgi];"
             "[bgi]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5,setsar=1[bg];")
        g += zweig("fgi", f"scale=1080:{fh},setsar=1",
                   f"crop=iw/1.25:ih/1.25,scale=1080:{fh},setsar=1", "fg")
        g += "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];"
        letzter = "v0"
    g += f"[{letzter}]format=yuv420p" + (",ass=clip.ass" if mit_ass else "") + "[v]"
    if hat_ton:
        g += ";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    return g


UT_QUEUE = queue.Queue()


def render_short(ordner, roh, cfg, b, h, hat_ton, zoom_t, mit_ass, ziel):
    g = filter_9x16(cfg.get("layout", "unschaerfe"), b, h, zoom_t, mit_ass, hat_ton)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", Path(roh).name, "-filter_complex", g, "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if hat_ton else []
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", ziel]
    return subprocess.run(cmd, cwd=ordner, capture_output=True, text=True, encoding="utf-8", errors="replace")


def info_setze(ordner, praefix, wert):
    p = Path(ordner) / "info.txt"
    zeilen = p.read_text(encoding="utf-8").splitlines()
    zeilen = [f"{praefix}{wert}" if z.startswith(praefix) else z for z in zeilen]
    p.write_text("\n".join(zeilen) + "\n", encoding="utf-8")


def untertitel_job(roh, ordner, cfg, moment, b, h, hat_ton, zoom_t, quelle):
    """Phase 2: Whisper, dann derselbe Clip noch einmal mit Untertiteln. Ersetzt short.mp4."""
    ordner = Path(ordner)
    try:
        woerter = transkribiere(roh, cfg)
        if not woerter:
            (ordner / "untertitel.keine").write_text("keine", encoding="utf-8")
            info_setze(ordner, "Gesagt:", "        (keine Untertitel)")
            return
        (ordner / "woerter.json").write_text(json.dumps(woerter, ensure_ascii=False, indent=1), encoding="utf-8")
        schreibe_untertitel(woerter, ordner, cfg.get("schrift", "Arial"))
        r = render_short(ordner, roh, cfg, b, h, hat_ton, zoom_t, True, "short_ut.mp4")
        if r.returncode != 0:
            log(quelle, f"Untertitel-Schnitt fehlgeschlagen: {r.stderr.strip()[-300:]}")
            (ordner / "untertitel.keine").write_text("fehler", encoding="utf-8")
            return
        os.replace(ordner / "short_ut.mp4", ordner / "short.mp4")
        info_setze(ordner, "Gesagt:", "        " + " ".join(w["word"] for w in woerter))
        (ordner / "untertitel.fertig").write_text("ok", encoding="utf-8")
        info = (ordner / "info.txt").read_text(encoding="utf-8")
        m = re.search(r"^Hook: (.+)$", info, re.M)
        if m:  # Hook war schon gesetzt: auf das neue Video anwenden
            subprocess.run([sys.executable, str(BASIS / "hook.py"), str(ordner), m.group(1)], capture_output=True)
        log(quelle, f"Untertitel fertig: {ordner.relative_to(BASIS)}")
    except Exception as e:
        log(quelle, f"Untertitel-Fehler: {e}")
        (ordner / "untertitel.keine").write_text("fehler", encoding="utf-8")


def ut_arbeiter():
    while True:
        job = UT_QUEUE.get()
        try:
            untertitel_job(*job)
        finally:
            UT_QUEUE.task_done()


threading.Thread(target=ut_arbeiter, daemon=True).start()


def verarbeite(roh, ordner, cfg, moment, meta):
    """Phase 1 (schnell): 9:16 + Zoom ohne Untertitel, sofort sichtbar. Phase 2 folgt im Hintergrund."""
    ordner = Path(ordner)
    b, h, hat_ton, dauer = proben(roh)
    zoom_t = moment if cfg.get("zoom_auf_pointe", True) and moment < dauer - 1.5 else None
    r = render_short(ordner, roh, cfg, b, h, hat_ton, zoom_t, False, "short.mp4")
    if r.returncode != 0:
        log(meta["quelle"], f"Schnitt fehlgeschlagen: {r.stderr.strip()[-400:]}")
        return False
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(moment, 0.5):.2f}", "-i", "short.mp4",
                    "-frames:v", "1", "-vf", "scale=540:-2", "vorschau.jpg"], cwd=ordner)
    mit_ut = bool(cfg.get("untertitel", True) and hat_ton)
    info = [
        f"Quelle:        {meta['quelle']}",
        f"Rechte:        {meta['rechte']}  {meta.get('nachweis', '')}".rstrip(),
        f"Aufgenommen:   {meta['zeit']}",
        f"Auslöser:      {meta['ausloeser']}",
        f"Pointe bei:    Sekunde {moment:.0f} im Clip (dort setzt der Zoom ein)",
        f"Chat-Wörter:   {', '.join(meta.get('chat', [])) or '-'}",
        "Gesagt:        " + ("(Untertitel folgen …)" if mit_ut else "(keine Untertitel)"),
        "",
        "Vor dem Posten:",
        "  1. Clip ansehen. Spoiler, Zwischensequenz, Musik im Hintergrund? Dann nicht posten.",
        "  2. Hook-Text drauf: in der Oberfläche oder  python hook.py \"<dieser Ordner>\" \"HOOK\"",
        f"  3. Credit in die Beschreibung: {meta.get('credit', '')}",
        "  4. Bezahlte Kampagne? 'Werbung' in Titel oder Bild.",
    ]
    (ordner / "info.txt").write_text("\n".join(info) + "\n", encoding="utf-8")

    liste = BASIS / "clips" / "liste.csv"
    neu = not liste.exists()
    with open(liste, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if neu:
            w.writerow(["Datum", "Plattform", "Clip", "Link", "Quelle", "Hook-Typ", "Länge", "Aufrufe",
                        "Durchgesehen %", "Kampagne", "Rate pro 1000", "Deckel", "Gebühr %",
                        "Mindestauszahlung", "Status", "Notiz"])
        w.writerow([datetime.now().strftime("%d.%m.%Y"), "", str(ordner.relative_to(BASIS)), "", meta["quelle"],
                    "", f"{dauer:.0f} s", "", "", meta["rechte"] if meta["rechte"] == "kampagne" else "",
                    "", "", "", "", "geschnitten, nicht gepostet", meta["ausloeser"]])
    log(meta["quelle"], f"Clip FERTIG (ohne Untertitel, die folgen): {ordner.relative_to(BASIS)}")
    if mit_ut:
        UT_QUEUE.put((roh, ordner, cfg, moment, b, h, hat_ton, zoom_t, meta["quelle"]))
    return True


# ---------------------------------------------------------------- Kanal-Wächter

class Kanal(threading.Thread):
    def __init__(self, eintrag, cfg, stop, test_quelle=None, test_chat=None):
        super().__init__(daemon=True)
        self.name = eintrag["name"].strip().lower()
        self.eintrag, self.cfg, self.stop = eintrag, cfg, stop
        self.test_quelle, self.test_chat = test_quelle, test_chat
        self.jobs = queue.Queue()
        self.anzahl = 0

    def ist_live(self):
        if self.test_quelle:
            return True
        r = subprocess.run(streamlink_cmd() + ["--json", f"https://www.twitch.tv/{self.name}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            return bool(json.loads(r.stdout).get("streams"))
        except (ValueError, AttributeError, TypeError):
            return False

    def run(self):
        log(self.name, "Warte, bis der Kanal live geht …")
        while not self.stop.is_set():
            try:
                if self.ist_live():
                    self.session()
                    if self.test_quelle:
                        return
                    log(self.name, "Stream vorbei. Warte auf den nächsten …")
            except Exception as e:  # ein Fehler darf den Kanal nicht dauerhaft beenden
                log(self.name, f"Fehler, versuche es weiter: {type(e).__name__}: {e}")
            self.stop.wait(self.cfg.get("pruef_intervall_s", 60))

    def session(self):
        cfg = self.cfg
        stempel = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ordner = BASIS / "aufnahmen" / f"{self.name}_{stempel}"
        if self.test_quelle:
            quelle = ["ffmpeg", "-v", "error", "-re", "-i", str(self.test_quelle), "-c", "copy", "-f", "mpegts", "pipe:1"]
        else:
            quelle = streamlink_cmd() + ["--stdout", "--retry-streams", "5", "--retry-open", "3",
                                         f"https://www.twitch.tv/{self.name}", cfg.get("qualitaet", "best")]
        rec = Aufnahme(ordner, quelle)
        rec.starte()
        ende_ses = threading.Event()
        chat = Chat(self.name, ende_ses, self.test_chat)
        chat.start()
        arbeiter = threading.Thread(target=self.arbeite, args=(rec, ende_ses), daemon=True)
        arbeiter.start()
        log(self.name, "LIVE – Aufnahme läuft.")

        fenster = cfg.get("fenster_s", 10)
        verlauf = deque(maxlen=30)
        letzter_clip = 0.0
        naechste = time.time() + fenster
        while not self.stop.is_set() and rec.laeuft():
            rec.aktualisiere()
            if time.time() < naechste:
                time.sleep(0.5)
                continue
            von, bis = naechste - fenster, naechste
            naechste += fenster
            nachr = chat.hole(von, bis)
            score = chat_score(nachr)
            basis = statistics.median(verlauf) if len(verlauf) >= 6 else None
            verlauf.append(score)
            if basis is None or rec.start_wall is None:
                continue
            schwelle = max(cfg.get("min_score", 8), basis * cfg.get("schwelle_faktor", 2.5))
            if score >= schwelle and bis - letzter_clip >= cfg.get("pause_zwischen_clips_s", 45):
                letzter_clip = bis
                moment = rec.rec_zeit(von) - cfg.get("chat_verzug_s", 4)
                vor, nach = clip_fenster(cfg)
                rvon = max(0.0, moment - vor)
                rbis = moment + nach
                log(self.name, f"Ausschlag! Chat {score:.0f} (normal {basis:.0f}) -> Clip {rvon:.0f}-{rbis:.0f} s")
                self.jobs.put((rvon, rbis, moment - rvon,
                               f"Chat-Ausschlag {score:.0f} statt {basis:.0f} pro {fenster} s", top_woerter(nachr)))
            offen = [j[0] for j in list(self.jobs.queue)]
            rec.aufraeumen(cfg.get("puffer_min", 15) * 60, min(offen) - 30 if offen else 1e12)
        rec.aktualisiere()
        ende_ses.set()
        rec.stop()
        rec.aktualisiere()
        self.jobs.put(None)
        arbeiter.join()
        if not cfg.get("aufnahme_behalten", False):
            shutil.rmtree(ordner, ignore_errors=True)
        log(self.name, f"Session beendet, {self.anzahl} Clips.")

    def arbeite(self, rec, ende_ses):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            rvon, rbis, moment, ausloeser, chat = job
            while rec.ende() < rbis + 1 and not ende_ses.is_set():
                time.sleep(1)
                rec.aktualisiere()
            ziel = BASIS / "clips" / datetime.now().strftime("%Y-%m-%d") / \
                f"{self.name}_{datetime.now():%H%M%S}"
            ziel.mkdir(parents=True, exist_ok=True)
            if not rec.schneide(rvon, rbis, ziel / "roh.mp4"):
                log(self.name, "Schnitt aus der Aufnahme fehlgeschlagen.")
                continue
            meta = {"quelle": self.name, "rechte": self.eintrag["rechte"], "nachweis": self.eintrag.get("nachweis", ""),
                    "zeit": datetime.now().strftime("%d.%m.%Y %H:%M"), "ausloeser": ausloeser, "chat": chat,
                    "credit": f"Clip aus dem Stream von twitch.tv/{self.name}"}
            if verarbeite(ziel / "roh.mp4", ziel, self.cfg, moment, meta):
                self.anzahl += 1


# ---------------------------------------------------------------- Datei-Modus (eigene Aufnahmen)

def lautstaerke_spitzen(datei, n, abstand):
    af = ("aresample=16000,asetnsamples=n=8000,"
          "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level")
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(datei), "-vn", "-af", af,
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    zeiten = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    pegel = [-90.0 if x == "-inf" else max(float(x), -90.0) for x in re.findall(r"RMS_level=(-?[\d.]+|-inf)", err)]
    pts = list(zip(zeiten, pegel))
    bewertet = []
    for i, (t, lv) in enumerate(pts):
        umgebung = [l for (_, l) in pts[max(0, i - 60):i + 61]]
        bewertet.append((lv - statistics.median(umgebung), t))
    bewertet.sort(reverse=True)
    gewaehlt = []
    for s, t in bewertet:
        if s < 3:
            break
        if all(abs(t - g[1]) >= abstand for g in gewaehlt):
            gewaehlt.append((s, t))
        if len(gewaehlt) == n:
            break
    return sorted(gewaehlt, key=lambda g: g[1])


def datei_modus(datei, rechte, nachweis, cfg):
    ok, msg = pruefe_rechte(rechte, nachweis)
    if not ok:
        sys.exit("Gestoppt: " + msg)
    datei = Path(datei).resolve()
    _, _, hat_ton, dauer = proben(datei)
    if not hat_ton:
        sys.exit("Die Datei hat keinen Ton, Ausschläge lassen sich so nicht finden.")
    log("datei", f"Suche Lautstärke-Spitzen in {datei.name} ({dauer / 60:.0f} min) …")
    vor, nach = clip_fenster(cfg)
    spitzen = lautstaerke_spitzen(datei, cfg.get("max_clips_datei", 8), vor + nach)
    if not spitzen:
        sys.exit("Keine deutlichen Spitzen gefunden.")
    for s, t in spitzen:
        von = max(0.0, t - vor)
        bis = min(dauer, t + nach)
        ziel = BASIS / "clips" / datetime.now().strftime("%Y-%m-%d") / f"{datei.stem}_{int(t // 60):02d}m{int(t % 60):02d}s"
        ziel.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{von:.2f}", "-i", str(datei), "-t", f"{bis - von:.2f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", str(ziel / "roh.mp4")])
        meta = {"quelle": datei.name, "rechte": rechte, "nachweis": nachweis,
                "zeit": f"Minute {t / 60:.1f} der Datei", "ausloeser": f"Lautstärke +{s:.1f} dB über Umgebung",
                "chat": [], "credit": "eigene Aufnahme" if rechte == "eigen" else nachweis}
        verarbeite(ziel / "roh.mp4", ziel, cfg, t - von, meta)
    if UT_QUEUE.unfinished_tasks:
        log("datei", "Warte, bis die Untertitel fertig sind …")
    UT_QUEUE.join()


# ---------------------------------------------------------------- Start

def main():
    ap = argparse.ArgumentParser(description="clip-watcher: Twitch-Streams automatisch clippen.")
    ap.add_argument("--datei", help="eigene Aufnahme statt Live-Stream verarbeiten")
    ap.add_argument("--rechte", help="beim Datei-Modus: eigen, kampagne oder erlaubnis")
    ap.add_argument("--nachweis", default="", help="Link oder Notiz zur Freigabe")
    ap.add_argument("--test-quelle", help=argparse.SUPPRESS)
    ap.add_argument("--test-chat", help=argparse.SUPPRESS)
    a = ap.parse_args()

    ffmpeg_da()
    cfg = lade_config()
    (BASIS / "clips").mkdir(exist_ok=True)

    if a.datei:
        return datei_modus(a.datei, a.rechte, a.nachweis, cfg)

    if not a.test_quelle:
        streamlink_cmd()
    stop = threading.Event()
    waechter = []
    for e in cfg.get("kanaele", []):
        name = str(e.get("name", "")).strip()
        if not name or name.startswith("KANAL"):
            continue
        ok, msg = pruefe_rechte(e.get("rechte"), e.get("nachweis"))
        if not ok:
            log(name, f"ÜBERSPRUNGEN – {msg}")
            continue
        waechter.append(Kanal(e, cfg, stop, a.test_quelle, a.test_chat))
    if not waechter:
        sys.exit("Kein Kanal mit gültigen Rechten in config.json. Siehe ANLEITUNG.md.")
    for w in waechter:
        w.start()
    log("clip-watcher", f"Läuft für {len(waechter)} Kanal/Kanäle. Beenden mit Strg+C.")
    try:
        while any(w.is_alive() for w in waechter):
            time.sleep(1)
    except KeyboardInterrupt:
        log("clip-watcher", "Beende … (laufende Clips werden noch fertig geschnitten)")
        stop.set()
        for w in waechter:
            w.join(timeout=120)
        if UT_QUEUE.unfinished_tasks:
            log("clip-watcher", "Warte, bis die Untertitel fertig sind …")
        UT_QUEUE.join()


if __name__ == "__main__":
    main()
