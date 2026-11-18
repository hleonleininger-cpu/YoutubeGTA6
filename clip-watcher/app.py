#!/usr/bin/env python3
"""
clip-watcher App: startet den Watcher und eine Oberfläche im Browser.
  python app.py            -> http://localhost:8765  (nur auf diesem Rechner)
  python app.py --lan      -> auch vom Handy im selben WLAN erreichbar
  python app.py --kein-watcher   (nur die Oberfläche, z. B. zum Ansehen alter Clips)
"""
import argparse, json, mimetypes, os, re, subprocess, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import aktualisieren, caption, konto, tracker, trend, wartung

BASIS = Path(__file__).resolve().parent
CLIPS = BASIS / "clips"
LOG = BASIS / "watcher.log"
ERLAUBT = {"eigen", "kampagne", "erlaubnis"}
WATCHER = {"proc": None, "gewollt": False, "neustarts": [], "hinweis": ""}
LOCK = threading.Lock()
MAX_NEUSTARTS_STUNDE = 5
SERVER = {"srv": None}
PORT = {"port": 8765}
NEUSTART = {"an": False}   # True: App beendet sich mit Code 3, starten.bat startet sie neu
UPDATE = {"info": None, "fehler": ""}
FEHLER = {}   # Meldungen der Hintergrundjobs (Trend, Tracker, Aufräumen) für die Oberfläche


# ------------------------------------------------------------ Watcher-Prozess

def watcher_start(auto=False):
    """auto=True: automatischer Neustart nach Absturz (Protokoll bleibt erhalten)."""
    with LOCK:
        watcher_stop_unlocked()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        f = open(LOG, "a" if auto else "w", encoding="utf-8")
        if auto:
            f.write(f"\n[{time.strftime('%H:%M:%S')}] [app] Watcher war abgestürzt, automatischer Neustart.\n")
            f.flush()
        else:
            WATCHER["neustarts"] = []
            WATCHER["hinweis"] = ""
        WATCHER["gewollt"] = True
        WATCHER["proc"] = subprocess.Popen([sys.executable, "-u", str(BASIS / "watcher.py")],
                                           stdout=f, stderr=subprocess.STDOUT, cwd=BASIS, env=env)


def watcher_ueberwachen():
    """Startet den Watcher nach einem Absturz neu: höchstens 5 Versuche pro Stunde, danach Hinweis."""
    while True:
        time.sleep(5)
        with LOCK:
            p = WATCHER["proc"]
            abgestuerzt = WATCHER["gewollt"] and p is not None and p.poll() is not None
        if not abgestuerzt:
            continue
        jetzt = time.time()
        WATCHER["neustarts"] = [t for t in WATCHER["neustarts"] if jetzt - t < 3600]
        if len(WATCHER["neustarts"]) >= MAX_NEUSTARTS_STUNDE:
            with LOCK:
                WATCHER["gewollt"] = False
                WATCHER["hinweis"] = (f"Der Watcher ist in der letzten Stunde {MAX_NEUSTARTS_STUNDE}-mal abgestürzt und "
                                      "wurde nicht mehr neu gestartet. Bitte das Protokoll ansehen und den Watcher von Hand starten.")
            continue
        verlauf = list(WATCHER["neustarts"])
        time.sleep(10)  # kurze Pause vor dem Neustart
        watcher_start(auto=True)
        WATCHER["neustarts"] = verlauf + [time.time()]


def watcher_stop_unlocked(gewollt=False):
    WATCHER["gewollt"] = gewollt
    p = WATCHER["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
    WATCHER["proc"] = None


def watcher_laeuft():
    p = WATCHER["proc"]
    return bool(p and p.poll() is None)


# ------------------------------------------------------------ Daten

def lade_config():
    return json.loads((BASIS / "config.json").read_text(encoding="utf-8"))


def config_oeffentlich():
    """Config für den Browser: der API-Schlüssel verlässt den Rechner nie."""
    cfg = lade_config()
    cfg["youtube_api_key_gesetzt"] = bool(trend.schluessel(cfg))
    cfg.pop("youtube_api_key", None)
    return cfg


def speichere_config(cfg):
    (BASIS / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def clip_liste():
    out = []
    links = tracker.link_karte()
    hooktypen = tracker.feld_karte("Hook-Typ")
    laengen = tracker.feld_karte("Länge")
    def entstanden(info):  # vorschau.jpg entsteht einmal beim Schnitt; Hook und Untertitel ändern sie nicht
        v = info.parent / "vorschau.jpg"
        return (v if v.exists() else info).stat().st_mtime
    for info in sorted(CLIPS.glob("*/*/info.txt"), key=entstanden, reverse=True):
        d = info.parent
        if not (d / "short.mp4").exists():
            continue
        felder = {}
        for z in info.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(Quelle|Rechte|Aufgenommen|Auslöser|Chat-Wörter|Gesagt|Hook|Pointe bei):\s*(.*)$", z)
            if m:
                felder[m.group(1)] = m.group(2).strip()
        rel = d.relative_to(CLIPS).as_posix()
        st = (d / "status.txt").read_text(encoding="utf-8").strip() if (d / "status.txt").exists() else "neu"
        ut = "fertig" if (d / "untertitel.fertig").exists() else ("keine" if (d / "untertitel.keine").exists() else "laeuft")
        video = "short_hook.mp4" if (d / "short_hook.mp4").exists() else "short.mp4"
        pointe = re.search(r"Sekunde\s+([\d.]+)", felder.get("Pointe bei", ""))
        laenge = re.match(r"\s*([\d.]+)", laengen.get(f"clips/{rel}", ""))
        score = re.search(r"Chat-Ausschlag\s+(\d+)\s+statt\s+(\d+)", felder.get("Auslöser", ""))
        frist = re.search(r"innerhalb\s+(\d+)\s*Min", felder.get("Rechte", ""), re.I)
        try:
            zeit_ts = int(datetime.strptime(felder.get("Aufgenommen", ""), "%d.%m.%Y %H:%M").timestamp() * 1000)
        except ValueError:
            zeit_ts = None
        bild = "vorschau_hook.jpg" if (d / "vorschau_hook.jpg").exists() else "vorschau.jpg"
        out.append({"id": rel, "status": st, "video": f"/files/{rel}/{video}", "ohne_hook": f"/files/{rel}/short.mp4", "ut": ut,
                    "link": links.get(f"clips/{rel}", ""),
                    "vorschau": f"/files/{rel}/{bild}" if (d / bild).exists() else "",
                    "pointe": float(pointe.group(1)) if pointe else None,
                    "laenge": float(laenge.group(1)) if laenge else None,
                    "score": int(score.group(1)) if score else None, "basis": int(score.group(2)) if score else None,
                    "frist_min": int(frist.group(1)) if frist else None, "zeit_ts": zeit_ts, "hook_typ": hooktypen.get(f"clips/{rel}", ""),
                    "srt": f"/files/{rel}/untertitel.srt" if (d / "untertitel.srt").exists() else "",
                    "hook": felder.get("Hook", ""), "quelle": felder.get("Quelle", ""),
                    "rechte": felder.get("Rechte", ""), "zeit": felder.get("Aufgenommen", ""),
                    "ausloeser": felder.get("Auslöser", ""), "chat": felder.get("Chat-Wörter", ""),
                    "gesagt": felder.get("Gesagt", "")})
    return out


def sicher(rel):
    p = (CLIPS / rel).resolve()
    if CLIPS.resolve() not in p.parents and p != CLIPS.resolve():
        raise ValueError("Pfad nicht erlaubt")
    return p


# ------------------------------------------------------------ Hintergrundjobs

def job(name, fn):
    try:
        r = fn()
        FEHLER.pop(name, None)
        return r
    except trend.ApiFehler as e:
        FEHLER[name] = str(e)
    except Exception as e:  # ein Job darf die App nie beenden
        FEHLER[name] = f"{type(e).__name__}: {e}"
    print(f"[app] {name}: {FEHLER[name]}", flush=True)


def hintergrund():
    """Trends alle 3 Stunden, Tracker stündlich, Aufräumen täglich."""
    while True:
        try:
            cfg = lade_config()
            if trend.schluessel(cfg):
                if trend.faellig() and trend.suchen_heute() < trend.MAX_SUCHEN_TAG:
                    job("trend", lambda: trend.abrufen(cfg))
                if tracker.faellig():
                    job("tracker", lambda: tracker.aktualisiere(cfg))
            if konto.status()["verbunden"] and tracker.durchsehrate_faellig():
                job("analytics", tracker.aktualisiere_durchsehrate)
            if time.time() - UPDATE.get("zeit", 0) > 3600:
                UPDATE["zeit"] = time.time()
                job("update", lambda: UPDATE.update(info=aktualisieren.pruefen(cfg)))
            if wartung.faellig(cfg):
                job("aufraeumen", lambda: wartung.aufraeumen(cfg))
        except Exception as e:
            print(f"[app] Hintergrundjob: {e}", flush=True)
        time.sleep(120)


# ------------------------------------------------------------ Oberfläche

HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>clip-watcher</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap">
<style>
/* Layout: Schnittplatz. Ablage links, Sichtgerät mit eigener Zeitleiste in der Mitte, Arbeitsbereich rechts.
   Farben aus der Vice-City-Nacht: indigofarbene Neutraltöne, Koralle markiert Hook und Pointe, Aqua markiert Abspielkopf und Auswahl. */
:root{
  --nacht:#100f17;--deck:#16141f;--deck2:#1d1a28;--deck3:#262234;--deck4:#302b41;
  --rule:rgba(190,175,255,.075);--rule2:rgba(190,175,255,.14);--rule3:rgba(190,175,255,.24);
  --ink:#f1eef9;--ink2:#a7a0bb;--ink3:#6f6985;
  --koralle:#ff6b8b;--koralle2:#ff8ea8;--koralleT:rgba(255,107,139,.14);
  --aqua:#4fd6c8;--aquaT:rgba(79,214,200,.14);
  --gut:#63dba5;--gutT:rgba(99,219,165,.12);--weg:#ff7d7d;--wegT:rgba(255,125,125,.1);--bern:#ffbf5e;--bernT:rgba(255,191,94,.12);
  --f-disp:"Space Grotesk","Segoe UI",system-ui,sans-serif;
  --f-text:Inter,"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,Consolas,monospace;
  color-scheme:dark;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;overflow:hidden;background:var(--nacht);color:var(--ink);font:14px/1.5 var(--f-text);-webkit-font-smoothing:antialiased;font-feature-settings:"cv11","ss01"}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--aqua);outline-offset:2px}
::selection{background:var(--koralleT)}
.mono{font-family:var(--f-mono);font-variant-numeric:tabular-nums}

/* Rahmen: Seitenleiste links, Inhalt rechts */
.app{flex:1;min-height:0;display:flex}
.side{width:232px;flex:none;display:flex;flex-direction:column;gap:18px;padding:18px 12px 14px max(12px,env(safe-area-inset-left,0px));border-right:1px solid var(--rule);background:var(--deck)}
.haupt{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}
.brand{display:flex;align-items:center;gap:10px;padding:0 8px;font:600 16px/1 var(--f-disp);letter-spacing:-.01em;white-space:nowrap}
.logo{width:28px;height:28px;border-radius:8px;background:var(--koralle);display:grid;place-items:center;color:var(--nacht);box-shadow:0 6px 18px -6px rgba(255,107,139,.6)}
#tabs{display:flex;flex-direction:column;gap:2px;flex:1;min-height:0;overflow-y:auto}
#tabs button,#mnav button{display:flex;align-items:center;gap:11px;width:100%;text-align:left;background:none;border:0;border-radius:9px;padding:9px 10px;color:var(--ink2);font-weight:500;transition:background .12s,color .12s}
#tabs button svg{flex:none;opacity:.85}
#tabs button:hover{background:var(--deck2);color:var(--ink)}
#tabs button.akt{background:var(--deck3);color:var(--ink)}
#tabs button.akt svg{color:var(--koralle);opacity:1}
#tabs .zahl,#mnav .zahl{margin-left:auto;font:600 11px/1 var(--f-mono);padding:3px 7px;border-radius:999px;background:var(--koralle);color:var(--nacht)}
#tabs .trenn{height:1px;background:var(--rule);margin:8px 10px}
.sfuss{display:flex;flex-direction:column;gap:10px}
.wk{display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:11px 12px;border-radius:12px;border:1px solid var(--rule2);background:var(--deck2);color:var(--ink)}
button.wk:hover{border-color:var(--rule3)}
.wk.rec{border-color:rgba(255,107,139,.35);background:linear-gradient(0deg,var(--koralleT),var(--koralleT)),var(--deck2)}
.wk .wt{display:flex;flex-direction:column;min-width:0;flex:1}
.wk .wt b{font-size:13px;font-weight:600}
.wk .wt span{font-size:11.5px;color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rec{font:600 10.5px/1 var(--f-mono);letter-spacing:.08em;color:var(--koralle)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ink3);flex:none}
.dot.bereit{background:var(--aqua);box-shadow:0 0 0 3px var(--aquaT)}
.dot.rec{background:var(--koralle);animation:rec 1.6s ease-in-out infinite}
@keyframes rec{50%{opacity:.35}}
.side .fuss{border:0;background:none;padding:0 4px;flex-direction:column;align-items:flex-start;gap:3px;justify-content:flex-start}
#mnav{display:none}

.band{flex:none;padding:0 max(20px,env(safe-area-inset-right,0px)) 0 20px}
.band:empty{display:none}
.band>*{margin-top:12px}
main{flex:1;min-height:0;overflow-y:auto;padding:20px max(20px,env(safe-area-inset-right,0px)) 20px 20px}

/* Schnittplatz */
.platz{display:grid;grid-template-columns:304px minmax(0,1fr) 392px;gap:16px;height:100%;min-height:540px}

/* Ablage */
.ablage{display:flex;flex-direction:column;min-height:0;background:var(--deck);border:1px solid var(--rule);border-radius:16px;overflow:hidden}
.ablkopf{flex:none;padding:12px;border-bottom:1px solid var(--rule);display:flex;flex-direction:column;gap:10px}
.filter{display:grid;grid-template-columns:repeat(3,1fr);padding:3px;gap:3px;background:var(--nacht);border-radius:10px}
.filter button{border:0;background:none;border-radius:7px;padding:7px 4px;color:var(--ink2);font-weight:500;font-size:13px;display:flex;align-items:center;justify-content:center;gap:6px;transition:background .15s,color .15s}
.filter button:hover{color:var(--ink)}
.filter button.akt{background:var(--deck3);color:var(--ink)}
.filter .n{font:500 11px/1 var(--f-mono);color:var(--ink3)}
.filter button.akt .n{color:var(--koralle2)}
.ablinfo{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--ink3)}
.neuhin{border:0;background:var(--aquaT);color:var(--aqua);font-size:12px;font-weight:600;padding:4px 9px;border-radius:6px}
.items{flex:1;min-height:0;overflow-y:auto;padding:6px;display:flex;flex-direction:column;gap:2px}
.item{display:grid;grid-template-columns:52px minmax(0,1fr);gap:12px;text-align:left;background:none;border:0;border-radius:11px;padding:8px;transition:background .12s}
.item:hover{background:var(--deck2)}
.item.akt{background:var(--deck3)}
.thumb{position:relative;width:52px;height:92px;border-radius:7px;overflow:hidden;background:var(--deck3);display:grid;place-items:center;color:var(--ink3)}
.item.akt .thumb{box-shadow:0 0 0 2px var(--aqua)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.thumb .dauer{position:absolute;right:3px;bottom:3px;font:500 10px/1 var(--f-mono);padding:2px 4px;border-radius:4px;background:rgba(16,15,23,.82);color:var(--ink)}
.itxt{display:flex;flex-direction:column;gap:4px;min-width:0;padding-top:1px}
.izeile{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.izeile b{font:600 14px/1.2 var(--f-disp);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.izeile .mono{font-size:11.5px;color:var(--ink3);flex:none}
.iquote{font-size:12.5px;line-height:1.4;color:var(--ink2);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.iquote.hook{color:var(--ink)}
.ifuss{display:flex;align-items:center;gap:8px;margin-top:auto}
.schritte{display:flex;gap:3px}
.schritte i{width:14px;height:3px;border-radius:2px;background:var(--deck4)}
.schritte i.an{background:var(--aqua)}
.frist{font:500 11px/1 var(--f-mono);padding:3px 6px;border-radius:5px;background:var(--bernT);color:var(--bern)}
.frist.knapp{background:var(--koralleT);color:var(--koralle2)}
.frist.vorbei{background:var(--deck3);color:var(--ink3)}

/* Sichtgerät */
.sicht{display:flex;flex-direction:column;min-height:0;min-width:0;background:radial-gradient(80% 70% at 50% 35%,#1c1929 0%,var(--nacht) 100%);border:1px solid var(--rule);border-radius:16px;padding:16px 18px 12px;gap:12px}
.rahmen-box{flex:1;min-height:0;display:flex;justify-content:center}
.rahmen{position:relative;height:100%;aspect-ratio:9/16;max-width:100%;border-radius:12px;overflow:hidden;background:#000;box-shadow:0 30px 80px -20px rgba(0,0,0,.7),0 0 0 1px var(--rule2);container-type:size}
.rahmen video{width:100%;height:100%;object-fit:contain;display:block;cursor:pointer}
.gross-play{position:absolute;inset:0;display:grid;place-items:center;pointer-events:none;opacity:0;transition:opacity .2s}
.gross-play span{width:64px;height:64px;border-radius:50%;background:rgba(16,15,23,.55);backdrop-filter:blur(6px);display:grid;place-items:center;color:#fff}
.rahmen.pause .gross-play{opacity:1}
.hookvor{position:absolute;left:6%;right:6%;top:17.2cqh;text-align:center;font:700 3.75cqh/4.69cqh Arial,"Helvetica Neue",sans-serif;color:#fff;text-transform:uppercase;pointer-events:none;
  text-shadow:.31cqh 0 #000,-.31cqh 0 #000,0 .31cqh #000,0 -.31cqh #000,.22cqh .22cqh #000,-.22cqh .22cqh #000,.22cqh -.22cqh #000,-.22cqh -.22cqh #000}
.vorlabel{position:absolute;left:10px;top:10px;font:600 10.5px/1 var(--f-mono);letter-spacing:.08em;text-transform:uppercase;padding:5px 7px;border-radius:5px;background:var(--koralle);color:var(--nacht)}

.transport{flex:none;display:grid;grid-template-columns:auto auto minmax(0,1fr) auto auto;align-items:center;gap:12px}
.tbtn{width:34px;height:34px;border-radius:9px;border:1px solid var(--rule2);background:var(--deck2);display:grid;place-items:center;color:var(--ink);transition:background .12s}
.tbtn:hover{background:var(--deck3)}
.tbtn.play{background:var(--ink);color:var(--nacht);border-color:var(--ink)}
.tbtn.play:hover{background:#fff}
.tc{font:500 12.5px/1 var(--f-mono);color:var(--ink);min-width:44px}
.tc.dim{color:var(--ink3)}
.leiste{position:relative;height:38px;cursor:pointer;touch-action:none}
.leiste .spur{position:absolute;left:0;right:0;top:20px;height:6px;border-radius:3px;background:var(--deck4)}
.leiste .fort{position:absolute;left:0;top:20px;height:6px;border-radius:3px;background:var(--aqua);width:0}
.leiste .kopf{position:absolute;top:16px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:var(--aqua);box-shadow:0 0 0 4px rgba(79,214,200,.2);left:0}
.leiste .hookzone{position:absolute;top:4px;bottom:4px;left:0;border-radius:4px;background:repeating-linear-gradient(135deg,var(--koralleT) 0 5px,transparent 5px 9px);border:1px solid rgba(255,107,139,.35)}
.leiste .hookzone span,.leiste .pointe span{position:absolute;top:-1px;font:600 9.5px/1 var(--f-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--koralle2);white-space:nowrap}
.leiste .hookzone span{left:5px;top:3px}
.leiste .pointe{position:absolute;top:2px;bottom:2px;width:2px;margin-left:-1px;background:var(--koralle);border-radius:1px}
.leiste .pointe::after{content:"";position:absolute;left:50%;bottom:-3px;width:8px;height:8px;margin-left:-4px;transform:rotate(45deg);background:var(--koralle)}
.leiste .pointe span{left:6px;top:1px}
.leiste .geist{position:absolute;top:-20px;transform:translateX(-50%);font:500 11px/1 var(--f-mono);padding:4px 6px;border-radius:5px;background:var(--deck4);color:var(--ink);pointer-events:none;opacity:0;transition:opacity .12s}
.leiste:hover .geist{opacity:1}
.tasten{flex:none;display:flex;justify-content:center;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--ink3)}
.tasten span{display:flex;align-items:center;gap:5px}
kbd{font:500 10.5px/1 var(--f-mono);min-width:20px;text-align:center;padding:4px 5px;border-radius:5px;background:var(--deck3);border:1px solid var(--rule2);border-bottom-width:2px;color:var(--ink2)}

/* Arbeitsbereich */
.arbeit{display:flex;flex-direction:column;min-height:0;background:var(--deck);border:1px solid var(--rule);border-radius:16px;overflow:hidden}
.akopf{flex:none;padding:18px 20px 0}
.atitel{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h2{font:600 22px/1.15 var(--f-disp);letter-spacing:-.02em;margin:0}
.ameta{margin-top:5px;font-size:12.5px;color:var(--ink2);display:flex;flex-wrap:wrap;gap:4px 12px}
.ameta b{color:var(--ink);font-weight:500}
.pills{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;padding-top:2px}
.pill{font-size:11.5px;font-weight:500;padding:3px 8px;border-radius:6px;background:var(--deck3);color:var(--ink2);white-space:nowrap}
.pill.koralle{background:var(--koralleT);color:var(--koralle2)}
.pill.gut{background:var(--gutT);color:var(--gut)}
.pill.bern{background:var(--bernT);color:var(--bern)}
.fristbox{margin-top:14px;padding:10px 12px;border-radius:10px;background:var(--bernT);color:var(--bern);font-size:12.5px;display:flex;flex-direction:column;gap:7px}
.fristbox.knapp{background:var(--koralleT);color:var(--koralle2)}
.fristbox.vorbei{background:var(--deck2);color:var(--ink3)}
.fristbox div{display:flex;justify-content:space-between;gap:10px}
.fristbox .bar{height:3px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}
.fristbox .bar i{display:block;height:100%;background:currentColor}
.moment{margin-top:14px;padding:12px 14px;border-radius:10px;background:var(--deck2)}
.moment q{display:block;font-size:14px;line-height:1.5;color:var(--ink);quotes:"„" "“"}
.chat{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.chat span{font:500 11px/1 var(--f-mono);padding:4px 7px;border-radius:5px;background:var(--deck4);color:var(--ink2)}
.moment .chat:first-child{margin-top:0}
.entscheid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
.ent{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:11px;font:600 14.5px/1 var(--f-text);border:1px solid;transition:background .12s,transform .06s}
.ent:active{transform:translateY(1px)}
.ent.gut{color:var(--gut);background:var(--gutT);border-color:rgba(99,219,165,.28)}
.ent.gut:hover{background:rgba(99,219,165,.2)}
.ent.weg{color:var(--weg);background:var(--wegT);border-color:rgba(255,125,125,.24)}
.ent.weg:hover{background:rgba(255,125,125,.17)}
.ent.an{box-shadow:inset 0 0 0 1.5px currentColor}
.ent kbd{background:rgba(0,0,0,.25);border-color:transparent;color:inherit;opacity:.8}
.stepper{display:grid;grid-template-columns:repeat(3,1fr);margin:18px -20px 0;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.stepper button{background:none;border:0;padding:12px 6px 11px;display:flex;align-items:center;justify-content:center;gap:7px;color:var(--ink2);font-weight:500;font-size:13.5px;position:relative;transition:color .15s}
.stepper button+button{border-left:1px solid var(--rule)}
.stepper button:hover{color:var(--ink)}
.stepper button.akt{color:var(--ink)}
.stepper button.akt::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--koralle)}
.stepper .hak{width:16px;height:16px;border-radius:50%;border:1.5px solid var(--deck4);display:grid;place-items:center;flex:none}
.stepper .hak.an{background:var(--aqua);border-color:var(--aqua);color:var(--nacht)}
.pane{flex:1;min-height:0;overflow-y:auto;padding:4px 20px 20px}
.lbl{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:18px 0 8px;font:600 11px/1.2 var(--f-text);text-transform:uppercase;letter-spacing:.07em;color:var(--ink2)}
.lbl span{text-transform:none;letter-spacing:0;font-weight:400;color:var(--ink3)}
.feld{display:flex;gap:8px}
.feld input{flex:1;min-width:0}
input,select,textarea{background:var(--nacht);border:1px solid var(--rule2);border-radius:10px;padding:10px 12px;width:100%;transition:border-color .15s,box-shadow .15s}
input::placeholder,textarea::placeholder{color:var(--ink3)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--koralle);box-shadow:0 0 0 3px var(--koralleT)}
input[type=checkbox]{width:16px;height:16px;padding:0;margin:0;accent-color:var(--aqua);flex:none;box-shadow:none}
textarea{font:12.5px/1.6 var(--f-mono);resize:vertical;min-height:130px}
.zaehler{font:500 11.5px/1 var(--f-mono);color:var(--ink3)}
.zaehler.warn{color:var(--bern)}
.vorschlaege{display:flex;flex-direction:column;border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.vorschlaege button{display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;background:none;border:0;padding:10px 12px;font-size:13.5px;color:var(--ink);transition:background .12s}
.vorschlaege button+button{border-top:1px solid var(--rule)}
.vorschlaege button:hover{background:var(--deck2)}
.vorschlaege button.an{background:var(--koralleT)}
.vorschlaege button i{font-style:normal;font-size:11.5px;color:var(--ink3);flex:none}
.vorschlaege button.an i{color:var(--koralle2)}
.typen{display:flex;flex-wrap:wrap;gap:6px}
.typ{border:1px solid var(--rule2);background:none;border-radius:8px;padding:6px 11px;font-size:13px;color:var(--ink2);transition:.12s}
.typ:hover{color:var(--ink);border-color:var(--rule3)}
.typ.an{background:var(--aquaT);border-color:rgba(79,214,200,.45);color:var(--aqua)}
.checks{display:flex;flex-direction:column;border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.check{display:flex;gap:11px;align-items:center;padding:10px 12px;font-size:13.5px;cursor:pointer;transition:background .12s}
.check+.check{border-top:1px solid var(--rule)}
.check:hover{background:var(--deck2)}
.check input:checked+span{color:var(--ink3);text-decoration:line-through}
.leise{color:var(--ink3);font-size:12.5px;margin-top:10px}

.fuss{flex:none;display:flex;justify-content:flex-end;gap:14px;padding:7px max(16px,env(safe-area-inset-right,0px)) calc(7px + env(safe-area-inset-bottom,0px)) 16px;border-top:1px solid var(--rule);font-size:12px;color:var(--ink3);background:var(--nacht)}
.fuss a{color:var(--ink2)}
.fuss a:hover{color:var(--aqua);text-decoration:none}
.fuss .mono{color:var(--ink2)}


/* Karten und Übersicht */
.karte{background:var(--deck);border:1px solid var(--rule);border-radius:16px;padding:18px 20px;margin-bottom:16px;min-width:0}
.khead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
.khead>div{display:flex;flex-direction:column;gap:2px}
h3{font:600 16px/1.25 var(--f-disp);letter-spacing:-.01em;margin:0}
.link{background:none;border:0;padding:0;color:var(--ink2);font-size:12.5px;font-weight:500}
.link:hover{color:var(--aqua)}
.klein-btn{padding:6px 11px;font-size:12.5px;border-radius:8px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.kpi{display:flex;flex-direction:column;gap:4px;text-align:left;padding:16px 18px;border-radius:16px;border:1px solid var(--rule);background:var(--deck);color:var(--ink);min-width:0;transition:border-color .12s,transform .08s}
button.kpi:not(:disabled):hover{border-color:var(--rule3)}
button.kpi:not(:disabled):active{transform:translateY(1px)}
.kpi:disabled{cursor:default}
.kpi .kt{font-size:12.5px;color:var(--ink2);font-weight:500}
.kpi b{font:600 30px/1.1 var(--f-disp);letter-spacing:-.03em;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.kpi .ks{font-size:12px;color:var(--ink3)}
.raster{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:16px;align-items:start}
.spalte{min-width:0}
.setup .khead{align-items:flex-end}
.setup{border-color:rgba(79,214,200,.28)}
.pbar{width:120px;max-width:40%;height:6px;border-radius:3px;background:var(--deck3);overflow:hidden}
.pbar i{display:block;height:100%;background:var(--aqua);border-radius:3px}
.schrittliste{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.schrittliste li{display:flex;align-items:center;gap:10px;min-height:44px;padding:6px 2px;border-top:1px solid var(--rule);font-size:13.5px}
.schrittliste li span:nth-child(2){flex:1}
.schrittliste li.ok span:nth-child(2){color:var(--ink3);text-decoration:line-through}
.schrittliste .hak,.stepper .hak{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--deck4);display:grid;place-items:center;flex:none}
.schrittliste li.ok .hak{background:var(--aqua);border-color:var(--aqua);color:var(--nacht)}
.zeile-k{display:flex;align-items:center;gap:12px;width:100%;text-align:left;background:none;border:0;border-top:1px solid var(--rule);padding:10px 2px;color:var(--ink)}
.zeile-k:first-of-type{border-top:0}
.zeile-k:hover .zt b{color:var(--aqua)}
.mini{width:30px;height:52px;border-radius:5px;overflow:hidden;background:var(--deck3);flex:none}
.mini img{width:100%;height:100%;object-fit:cover;display:block}
.zt{display:flex;flex-direction:column;min-width:0;flex:1}
.zt b{font:600 14px/1.3 var(--f-disp)}
.zt>span{font-size:12.5px;color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.streifen{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:12px}
.kachel{display:flex;flex-direction:column;gap:3px;text-align:left;background:none;border:0;padding:0;color:var(--ink);min-width:0}
.kbild{position:relative;aspect-ratio:9/16;border-radius:10px;overflow:hidden;background:var(--deck3);margin-bottom:5px;transition:box-shadow .12s}
.kbild img{width:100%;height:100%;object-fit:cover;display:block}
.kachel:hover .kbild{box-shadow:0 0 0 2px var(--aqua)}
.kbild .dauer{position:absolute;left:6px;bottom:6px;font:500 10.5px/1 var(--f-mono);padding:3px 5px;border-radius:4px;background:rgba(16,15,23,.82)}
.kachel b{font:600 13px/1.25 var(--f-disp)}
.kachel>span:last-child{font-size:12px;color:var(--ink2);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.balken{display:flex;flex-direction:column;gap:10px}
.bz{display:grid;grid-template-columns:minmax(90px,150px) minmax(0,1fr) 78px;gap:12px;align-items:center}
.balken.gross .bz{grid-template-columns:minmax(120px,210px) minmax(0,1fr) 96px}
.bl{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bl b{font-weight:600}
.bl .mono{color:var(--ink3);font-size:11.5px}
.bb{height:10px;border-radius:5px;background:var(--deck3);overflow:hidden}
.bb i{display:block;height:100%;border-radius:5px;background:linear-gradient(90deg,var(--aqua),#7fe6db)}
.bz:first-child .bb i{background:linear-gradient(90deg,var(--koralle),var(--koralle2))}
.bw{text-align:right;font-size:13px;display:flex;flex-direction:column;align-items:flex-end;line-height:1.2}
.bw small{font-size:10.5px;color:var(--ink3)}
.kliste{display:flex;flex-direction:column}
.kl{display:flex;align-items:center;gap:12px;padding:10px 2px;border-top:1px solid var(--rule)}
.kl:first-child{border-top:0}
.ava{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--deck3);color:var(--ink2);font:600 14px/1 var(--f-disp)}
.ava.on{background:var(--koralleT);color:var(--koralle);box-shadow:0 0 0 2px var(--koralle)}
.kliste.gross .kl{padding:14px 2px;flex-wrap:wrap}
.kliste.gross .zt b{display:flex;align-items:center;gap:8px}
.nachweis{white-space:normal!important;overflow-wrap:anywhere}
.kmeta{display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.feed{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.feed li{display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--rule);font-size:13px}
.feed li:first-child{border-top:0}
.feed .fi{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;flex:none;background:var(--deck3);color:var(--ink2)}
.feed .aus .fi{background:var(--koralleT);color:var(--koralle)}
.feed .fertig .fi{background:var(--gutT);color:var(--gut)}
.feed .live .fi{background:var(--aquaT);color:var(--aqua)}
.feed .fehler .fi{background:var(--wegT);color:var(--weg)}
.feed .skip{color:var(--ink3)}
.feed .ft{flex:1;min-width:0}
/* Formulare und Einstellungen */
.formgrid{display:grid;grid-template-columns:1fr 220px;gap:12px}
.formgrid label{display:flex;flex-direction:column;gap:6px;font-size:12.5px;font-weight:500;color:var(--ink2)}
.formgrid .voll{grid-column:1/-1}
.sprung{display:flex;gap:6px;flex-wrap:wrap;margin:-4px 0 16px;position:sticky;top:-20px;z-index:2;padding:10px 0;background:var(--nacht)}
.sprung a{font-size:12.5px;padding:6px 11px;border-radius:999px;border:1px solid var(--rule2);color:var(--ink2)}
.sprung a:hover{color:var(--ink);border-color:var(--rule3);text-decoration:none}
.schalter{display:flex;align-items:center;gap:14px;padding:12px 0;border-top:1px solid var(--rule);cursor:pointer}
.schalter:first-child{border-top:0;padding-top:0}
.schalter .st{flex:1;display:flex;flex-direction:column;gap:2px}
.schalter .st b{font-weight:600;font-size:14px}
.schalter .st span{font-size:12.5px;color:var(--ink2)}
.schalter input{position:absolute;opacity:0;width:1px;height:1px}
.sw{width:40px;height:24px;border-radius:12px;background:var(--deck4);position:relative;flex:none;transition:background .15s}
.sw::after{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;border-radius:50%;background:var(--ink);transition:transform .15s}
.schalter input:checked+.sw{background:var(--aqua)}
.schalter input:checked+.sw::after{transform:translateX(16px);background:var(--nacht)}
.schalter input:focus-visible+.sw{outline:2px solid var(--aqua);outline-offset:2px}
.status{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--ink2)}
.status.gut{color:var(--gut)}
.trenner{height:1px;background:var(--rule);margin:16px 0 12px}
.hilfe-d{margin-top:12px;font-size:13px;color:var(--ink2)}
.hilfe-d summary{cursor:pointer;color:var(--ink);font-weight:500}
.hilfe-d ol{margin:8px 0 0;padding-left:20px}
.prog{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.prog>div:first-child{display:flex;flex-direction:column;gap:2px}
.zeile-h{display:flex;align-items:center;justify-content:space-between;gap:12px}
.tabelle .tscroll{overflow-x:auto;margin:0 -20px -18px;padding:0 20px 8px}
.hilfe-k{display:flex;align-items:center;gap:5px;background:none;border:0;color:var(--ink3);font-size:11.5px;padding:0}
.hilfe-k:hover{color:var(--ink)}
#hilfe{position:fixed;inset:0;z-index:50;background:rgba(8,7,12,.6);backdrop-filter:blur(4px);display:grid;place-items:center;padding:20px}
#hilfe[hidden]{display:none}
.modal{width:min(440px,100%);background:var(--deck);border:1px solid var(--rule2);border-radius:18px;padding:20px 22px;box-shadow:0 30px 80px rgba(0,0,0,.5)}
.kuerzel{display:grid;grid-template-columns:auto 1fr;gap:10px 18px;margin:0;align-items:center}
.kuerzel dt{display:flex;gap:4px}
.kuerzel dd{margin:0;color:var(--ink2);font-size:13.5px}

/* Knöpfe */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;background:var(--deck3);border:1px solid var(--rule2);border-radius:10px;padding:9px 14px;font-weight:500;text-decoration:none;color:var(--ink);white-space:nowrap;transition:background .12s,border-color .12s,transform .06s}
.btn:hover{background:var(--deck4);border-color:var(--rule3);text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.5;cursor:default;transform:none}
.btn.pri{background:var(--koralle);border-color:var(--koralle);color:#1a0e14;font-weight:600}
.btn.pri:hover{background:var(--koralle2);border-color:var(--koralle2)}
.btn.ghost{background:none}
.btn.weg{color:var(--weg)}
.btn.gut{color:var(--gut)}

/* Seiten */
.seite{max-width:1080px;margin:0 auto;padding-bottom:24px}
.seite.breit{max-width:1240px}
.skopf{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin:6px 0 18px;flex-wrap:wrap}
h1{font:600 26px/1.15 var(--f-disp);letter-spacing:-.02em;margin:0;text-wrap:balance}
.skopf p{margin:6px 0 0;color:var(--ink2);max-width:62ch}
.skopf p.satz{font-size:15px}
.skopf p.satz b{color:var(--ink);font-weight:600}
.sakt{display:flex;gap:8px;flex-wrap:wrap}
.box{background:var(--deck);border:1px solid var(--rule);border-radius:16px;padding:18px 20px;margin-bottom:14px;min-width:0}
.box>b:first-child{display:block;font:600 16px/1.3 var(--f-disp);margin-bottom:6px}
.zeile{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.zeile input{flex:1;min-width:180px}
.zeile select{width:auto}
.klein{color:var(--ink2);font-size:12.5px}
.tag{font-size:11.5px;font-weight:500;padding:2px 7px;border-radius:6px;background:var(--deck3);color:var(--ink2)}
.tag.gut{background:var(--gutT);color:var(--gut)}
.hinweis{background:var(--bernT);border:1px solid rgba(255,191,94,.22);color:#f6dfb8;border-radius:12px;padding:11px 14px;font-size:13px;margin:12px 0}
.warn{background:var(--wegT);border:1px solid rgba(255,125,125,.24);color:#ffd6d6;border-radius:12px;padding:11px 14px;font-size:13px;margin:12px 0}
.update{display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--aquaT);border:1px solid rgba(79,214,200,.3);border-radius:12px;padding:8px 8px 8px 14px;font-size:13px}
.update b{color:var(--aqua)}
.update .btn.pri{background:var(--aqua);border-color:var(--aqua);color:var(--nacht)}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:10px 12px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{color:var(--ink3);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.07em}
td.z,th.z{text-align:right;font-family:var(--f-mono);font-size:12.5px}
th.z{font-family:var(--f-text);font-size:11px}
tr:last-child td{border-bottom:0}
tr:hover td{background:rgba(255,255,255,.015)}
a{color:var(--aqua);text-decoration:none}
a:hover{text-decoration:underline}
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.stat div{background:var(--deck2);border-radius:12px;padding:14px 16px;color:var(--ink2);font-size:12.5px;min-width:0}
.stat b{display:block;font:600 24px/1.2 var(--f-disp);color:var(--ink);letter-spacing:-.02em;margin-bottom:2px;overflow-wrap:anywhere}
.log{background:var(--deck);border:1px solid var(--rule);border-radius:16px;padding:16px 18px;font:12.5px/1.75 var(--f-mono);white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink2);margin:0}
.log .t{color:var(--ink3)}
.log .k{color:var(--aqua)}
.log .a{color:var(--koralle2);font-weight:500}
.log .f{color:var(--gut)}
.log .e{color:var(--weg)}
code{font-family:var(--f-mono);background:var(--deck3);padding:1px 5px;border-radius:5px;font-size:.9em}
ul{margin:8px 0;padding-left:20px}
li{margin:4px 0}
.laengen{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}
.auswahl{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-top:12px}
.auswahl button{display:flex;flex-direction:column;gap:3px;text-align:left;padding:12px 14px;border-radius:12px;border:1px solid var(--rule2);background:var(--deck2);color:var(--ink2);transition:.12s}
.auswahl button:hover{border-color:var(--rule3);color:var(--ink)}
.auswahl button b{font:600 14.5px/1.2 var(--f-disp);color:var(--ink)}
.auswahl button span{font-size:12.5px;line-height:1.4}
.auswahl button.an{background:var(--aquaT);border-color:rgba(79,214,200,.5)}
.auswahl button.an b{color:var(--aqua)}
ul.regeln{margin:10px 0;padding-left:18px;color:var(--ink2)}

.leer{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center;padding:40px 24px;color:var(--ink2);height:100%}
.leer .ikon{width:56px;height:56px;border-radius:14px;background:var(--deck2);display:grid;place-items:center;color:var(--ink3);margin-bottom:4px}
.leer b{font:600 17px/1.3 var(--f-disp);color:var(--ink)}
.leer span{max-width:340px;font-size:13.5px}

#toast{position:fixed;left:50%;bottom:calc(22px + env(safe-area-inset-bottom,0px));transform:translate(-50%,14px);opacity:0;display:flex;align-items:center;gap:8px;background:var(--ink);color:var(--nacht);padding:10px 16px;border-radius:10px;font-weight:600;font-size:13.5px;transition:opacity .2s,transform .2s;pointer-events:none;z-index:40;box-shadow:0 12px 30px rgba(0,0,0,.4)}
#toast.an{opacity:1;transform:translate(-50%,0)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--deck4);border-radius:10px;border:3px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-track{background:transparent}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media(max-width:1240px){.platz{grid-template-columns:260px minmax(0,1fr) 360px}.side{width:200px}}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.raster{grid-template-columns:1fr}}
@media(max-width:1000px){
  body{overflow:auto;height:auto;min-height:100%;display:block;padding-bottom:calc(66px + env(safe-area-inset-bottom,0px))}
  .app{display:block}.side{display:none}.haupt{display:block}
  main{overflow:visible;padding:16px max(16px,env(safe-area-inset-right,0px)) 16px max(16px,env(safe-area-inset-left,0px))}
  .band{padding:0 max(16px,env(safe-area-inset-right,0px)) 0 max(16px,env(safe-area-inset-left,0px))}
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:30;background:rgba(22,20,31,.94);backdrop-filter:blur(10px);border-top:1px solid var(--rule);padding:6px max(6px,env(safe-area-inset-right,0px)) calc(6px + env(safe-area-inset-bottom,0px)) max(6px,env(safe-area-inset-left,0px))}
  #mnav button{flex-direction:column;gap:3px;padding:6px 2px;font-size:10.5px;justify-content:center;position:relative;text-align:center}
  #mnav button span:not(.zahl){white-space:nowrap}
  #mnav button.akt{color:var(--ink)}
  #mnav button.akt svg{color:var(--koralle)}
  #mnav .zahl{position:absolute;top:2px;left:calc(50% + 6px);margin:0;padding:2px 5px;font-size:9.5px}
  .platz{grid-template-columns:1fr;height:auto}
  .items{flex-direction:row;overflow-x:auto;padding:8px}
  .item{flex:none;width:260px}
  .sicht{padding:12px}
  .rahmen-box{flex:none;height:min(72vh,640px)}
  .tasten{display:none}
  .pane{overflow:visible}
  .sprung{top:0}
  #toast{bottom:calc(80px + env(safe-area-inset-bottom,0px))}
}
@media(max-width:640px){
  .transport{grid-template-columns:auto minmax(0,1fr) auto;gap:10px}
  .transport .tc{display:none}
  .akopf,.pane{padding-left:16px;padding-right:16px}
  .stepper{margin-left:-16px;margin-right:-16px}
  .kpi b{font-size:24px}
  .formgrid{grid-template-columns:1fr}
  .bz,.balken.gross .bz{grid-template-columns:minmax(0,1fr) 64px;grid-template-areas:"l w" "b b";row-gap:4px}
  .bl{grid-area:l}.bb{grid-area:b}.bw{grid-area:w}
  .karte{padding:16px}
  .streifen{display:flex;overflow-x:auto;gap:10px;margin:0 -16px;padding:0 16px 4px;scroll-snap-type:x mandatory}
  .kachel{flex:none;width:112px;scroll-snap-align:start}
  h1{font-size:23px}
}
</style></head><body>
<div class="app"><aside class="side"><div class="brand"><span class="logo"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M8 5.2v13.6L19 12z"/></svg></span>clip-watcher</div>
<nav id="tabs" aria-label="Hauptnavigation"></nav><div class="sfuss"><div id="stat"></div><footer id="fuss" class="fuss"></footer></div></aside>
<div class="haupt"><div id="banner" class="band"></div><div id="upd" class="band"></div><main id="main"></main></div></div>
<nav id="mnav" aria-label="Navigation"></nav><div id="toast" role="status" aria-live="polite"></div><div id="hilfe" hidden></div>
<script>
let tab='start',bekannt=null,filter='neu',clips=[],cfg=null,st={},kontoSt={},sel=null,sig='',neuWartet=false,autoplay=false;
let pane={},caps={},hv={},hookE={},busy={},ups={},check={},hookV={};
let stumm=false;try{stumm=localStorage.getItem('cw_stumm')=='1'}catch(e){}
const $=s=>document.querySelector(s);
const api=(u,b)=>fetch(u,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{}).then(r=>r.json());
const sv=(d,w,f)=>'<svg viewBox="0 0 24 24" width="'+(w||18)+'" height="'+(w||18)+'" fill="'+(f?'currentColor':'none')+'" stroke="'+(f?'none':'currentColor')+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+d+'</svg>';
const P={play:'<path d="M8 5.2v13.6L19 12z"/>',pause:'<rect x="6.5" y="5" width="3.8" height="14" rx="1"/><rect x="13.7" y="5" width="3.8" height="14" rx="1"/>',
 laut:'<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.8 7.8 0 0 1 0 11"/>',stumm:'<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
 check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>',x:'<path d="M6 6l12 12M18 6L6 18"/>',copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
 down:'<path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19.5h14"/>',film:'<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
 blitz:'<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',minus:'<path d="M6 12h12"/>',punkt:'<circle cx="12" cy="12" r="3" fill="currentColor"/>',
 radar:'<circle cx="12" cy="12" r="2"/><path d="M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 19a10 10 0 0 1 0-14M19 5a10 10 0 0 1 0 14"/>'};
const TYPEN=['Frage','Zahl','Schock','Reaktion','Zitat'];
const CHECKS=['Kein Spoiler, keine Zwischensequenz allein','Kein Leak, keine laute Musik im Hintergrund','Hook und Untertitel sind drin','Bei Kampagne: „Werbung“ sichtbar, Pflichtangaben drin'];

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function tippt(){const a=document.activeElement&&document.activeElement.tagName;return a=='INPUT'||a=='TEXTAREA'||a=='SELECT'}
function toast(t){const e=$('#toast');e.innerHTML=sv(P.check,16)+esc(t);e.classList.add('an');clearTimeout(window.__tt);window.__tt=setTimeout(()=>e.classList.remove('an'),1900)}
function gesagt(c){const g=(c.gesagt||'').trim();return g.startsWith('(')?'':g}
function chatWoerter(c){return (c.chat&&c.chat!='-')?c.chat.split(',').map(w=>w.trim()).filter(Boolean).slice(0,5):[]}
function liste(){return clips.filter(c=>c.status==filter)}
function aktuell(){return clips.find(c=>c.id==sel)}
function uhr(c){return (c.zeit||'').split(' ')[1]||c.zeit||''}
function fmt(t){t=Math.max(0,t||0);return Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0')+'.'+(Math.floor(t*10)%10)}
function kampagne(c){return (c.rechte||'').startsWith('kampagne')}
function kopiert(id){try{return localStorage.getItem('cw_kop_'+id)=='1'}catch(e){return false}}
function merkeKopiert(id){try{localStorage.setItem('cw_kop_'+id,'1')}catch(e){}}
function schritte(c){return {hook:!!c.hook,caption:kopiert(c.id),online:!!c.link}}
function fristEnde(c){return kampagne(c)&&c.frist_min&&c.zeit_ts&&!c.link&&c.status!='weg'?c.zeit_ts+c.frist_min*60000:null}
function fristText(ende){const r=ende-Date.now();if(r<=0)return['Frist vorbei','vorbei'];const m=Math.ceil(r/60000);return[m+' Min',m<=10?'knapp':'']}
function umbruch(t,w){const out=[];let z='';for(let wort of t.split(' ').filter(Boolean)){while(wort.length>w){if(z){out.push(z);z=''}out.push(wort.slice(0,w));wort=wort.slice(w)}if(!z)z=wort;else if((z+' '+wort).length<=w)z+=' '+wort;else{out.push(z);z=wort}}if(z)out.push(z);return out}

/* Navigation */
function setzeFilter(f){filter=f;sel=null;render()}

function logHtml(txt){if(!txt)return 'Noch nichts protokolliert.';return txt.split('\\n').map(z=>{let h='';
 if(z.startsWith('[')&&z.indexOf(']')==9){h+=`<span class="t">${esc(z.slice(0,10))}</span>`;z=z.slice(10)}
 const a=z.indexOf('['),b=z.indexOf(']');if(a>-1&&b>a&&a<3){h+=esc(z.slice(0,a))+`<span class="k">${esc(z.slice(a,b+1))}</span>`;z=z.slice(b+1)}
 let k='';if(z.includes('Ausschlag!'))k='a';else if(z.includes('FERTIG'))k='f';else if(/Fehler|fehlgeschlagen|abgestürzt|fehlt|ÜBERSPRUNGEN/.test(z))k='e';
 return h+(k?`<span class="${k}">${esc(z)}</span>`:esc(z))}).join('\\n')}

/* Schnittplatz */
function platzHtml(){const l=liste();if(!l.find(c=>c.id==sel))sel=l[0]?l[0].id:null;const c=aktuell();
 const n=k=>clips.filter(x=>x.status==k).length;
 return `<div class="platz"><aside class="ablage"><div class="ablkopf">
 <div class="filter">${[['neu','Neu'],['gut','Gut'],['weg','Verworfen']].map(([k,t])=>`<button class="${filter==k?'akt':''}" onclick="setzeFilter('${k}')">${t}<span class="n">${n(k)}</span></button>`).join('')}</div>
 <div class="ablinfo"><span>${cfg&&cfg.clip_laenge_s?cfg.clip_laenge_s:18} s pro Clip · neueste oben</span>${neuWartet?'<button class="neuhin" onclick="neuLaden()">Neue Clips</button>':''}</div></div>
 <div class="items" id="items">${itemsHtml(l)}</div></aside>
 ${c?sichtHtml(c)+`<section class="arbeit" id="arbeit">${arbeitHtml(c)}</section>`:`<section class="sicht" style="grid-column:span 2">${leerHtml()}</section>`}</div>`}
function itemsHtml(l){return l.length?l.map(item).join(''):`<div class="leise" style="padding:14px 10px">${filter=='neu'?'Keine neuen Clips.':'Noch leer.'}</div>`}
function item(c){const s=schritte(c),txt=c.hook||gesagt(c)||(c.score?'Chat '+c.score+' statt '+c.basis:c.ausloeser)||'–',ende=fristEnde(c);
 return `<button class="item ${c.id==sel?'akt':''}" onclick="waehle('${c.id}')">
 <span class="thumb">${c.vorschau?`<img src="${c.vorschau}" loading="lazy" alt="">`:sv(P.play,18,1)}${c.laenge?`<span class="dauer">0:${String(Math.round(c.laenge)).padStart(2,'0')}</span>`:''}</span>
 <span class="itxt"><span class="izeile"><b>${esc(c.quelle)}</b><span class="mono">${esc(uhr(c))}</span></span>
 <span class="iquote ${c.hook?'hook':''}">${esc(txt)}</span>
 <span class="ifuss"><span class="schritte" title="Hook · Caption · Online"><i class="${s.hook?'an':''}"></i><i class="${s.caption?'an':''}"></i><i class="${s.online?'an':''}"></i></span>${ende?`<span class="frist" data-ende="${ende}"></span>`:''}</span></span></button>`}
function leerHtml(){const live=st.aufnahme||[];
 const [t,u]=filter!='neu'?[filter=='gut'?'Noch keine guten Clips':'Nichts verworfen','Sortiere Clips unter „Neu“ mit G für gut oder W für weg.']:
  !st.laeuft?['Der Watcher ist aus','Starte ihn unter „Kanäle“, dann wartet er auf deine Livestreams.']:
  live.length?[esc(live.join(', '))+' ist live','Die Aufnahme läuft. Sobald der Chat ausschlägt, erscheint hier der erste Clip.']:['Noch keine neuen Clips','Der Watcher wartet. Wenn ein überwachter Kanal live geht und der Chat ausschlägt, landet der Clip hier.'];
 return `<div class="leer"><div class="ikon">${sv(live.length&&filter=='neu'?P.radar:P.film,26)}</div><b>${t}</b><span>${u}</span>${!st.laeuft&&filter=='neu'?`<button class="btn" onclick="geheZu('kanaele')">Zu den Kanälen</button>`:''}</div>`}

function sichtHtml(c){const src=c.video+(hookV[c.id]?'?v='+hookV[c.id]:'');
 return `<section class="sicht"><div class="rahmen-box"><div class="rahmen pause" id="rahmen">
 <video id="vid" src="${src}" playsinline loop preload="auto"></video>
 <div class="gross-play"><span>${sv(P.play,26,1)}</span></div><div id="hookvor"></div></div></div>
 <div class="transport"><button class="tbtn play" id="pbtn" aria-label="Abspielen">${sv(P.play,16,1)}</button><span class="tc mono" id="tcA">0:00.0</span>
 <div class="leiste" id="leiste" aria-label="Zeitleiste"><div class="hookzone" id="hz" title="Hook-Fenster: die ersten 2,5 Sekunden"><span>Hook</span></div><div class="spur"></div><div class="fort" id="fort"></div>
 ${c.pointe!=null?'<div class="pointe" id="pm" title="Pointe: hier hat der Chat ausgeschlagen"><span>Pointe</span></div>':''}<div class="kopf" id="kopf"></div><div class="geist" id="geist"></div></div>
 <span class="tc dim mono" id="tcD">–</span><button class="tbtn" id="mbtn" aria-label="Ton an oder aus">${sv(stumm?P.stumm:P.laut,17)}</button></div>
 <div class="tasten"><span><kbd>Leertaste</kbd>Play</span><span><kbd>↑</kbd><kbd>↓</kbd>Clip</span><span><kbd>G</kbd>Gut</span><span><kbd>W</kbd>Weg</span><button class="hilfe-k" onclick="hilfe()"><kbd>?</kbd>Alle Kürzel</button></div></section>`}

let rafToken=0;
function bindPlayer(){const v=$('#vid');if(!v)return;const rahmen=$('#rahmen'),fort=$('#fort'),kopf=$('#kopf'),tcA=$('#tcA'),pb=$('#pbtn'),le=$('#leiste'),ge=$('#geist');
 v.muted=stumm;
 const upd=()=>{const d=v.duration||0,p=d?v.currentTime/d*100:0;fort.style.width=p+'%';kopf.style.left=p+'%';tcA.textContent=fmt(v.currentTime);
  rahmen.classList.toggle('pause',v.paused);pb.innerHTML=sv(v.paused?P.play:P.pause,16,1);pb.setAttribute('aria-label',v.paused?'Abspielen':'Pause')};
 const loop=tok=>{if(tok!==rafToken)return;upd();if(!v.paused)requestAnimationFrame(()=>loop(tok))};
 v.addEventListener('loadedmetadata',()=>{const d=v.duration||1,c=aktuell();$('#tcD').textContent=fmt(d);$('#hz').style.width=Math.min(100,2.5/d*100)+'%';
  const pm=$('#pm');if(pm&&c&&c.pointe!=null)pm.style.left=Math.min(100,c.pointe/d*100)+'%';upd()});
 v.addEventListener('play',()=>{rafToken++;loop(rafToken)});v.addEventListener('pause',upd);v.addEventListener('seeked',upd);
 v.onclick=()=>v.paused?v.play():v.pause();pb.onclick=v.onclick;
 $('#mbtn').onclick=stummUmschalten;
 const zeit=e=>{const r=le.getBoundingClientRect();return Math.min(1,Math.max(0,(e.clientX-r.left)/r.width))*(v.duration||0)};
 let zieht=false;
 le.onpointerdown=e=>{zieht=true;le.setPointerCapture(e.pointerId);v.currentTime=zeit(e);upd()};
 le.onpointermove=e=>{const t=zeit(e),r=le.getBoundingClientRect();ge.style.left=(e.clientX-r.left)+'px';ge.textContent=fmt(t);if(zieht){v.currentTime=t;upd()}};
 le.onpointerup=()=>{zieht=false};
 hookVorschau();
 if(autoplay){v.play().catch(()=>{})}autoplay=false}
function stummUmschalten(){stumm=!stumm;try{localStorage.setItem('cw_stumm',stumm?'1':'0')}catch(e){}const v=$('#vid');if(v)v.muted=stumm;const b=$('#mbtn');if(b)b.innerHTML=sv(stumm?P.stumm:P.laut,17);toast(stumm?'Ton aus':'Ton an')}
function hookVorschau(){const box=$('#hookvor'),c=aktuell();if(!box||!c)return;const e=hookE[c.id],t=(e||'').trim();
 if(e==null||!t||t==(c.hook||'').trim()){box.innerHTML='';return}
 box.innerHTML='<span class="vorlabel">Vorschau</span><div class="hookvor">'+umbruch(t.toUpperCase(),20).slice(0,2).map(esc).join('<br>')+'</div>'}

/* Arbeitsbereich */
function arbeitHtml(c){const p=pane[c.id]||'hook',s=schritte(c),g=gesagt(c),w=chatWoerter(c),ende=fristEnde(c);
 const rechte=(c.rechte||'').split(' ')[0];
 return `<div class="akopf"><div class="atitel"><div><h2>${esc(c.quelle)}</h2><div class="ameta"><span>${esc(c.zeit)}</span>${c.score?`<span>Chat <b>${c.score}</b> statt ${c.basis}</span>`:''}${c.laenge?`<span>${Math.round(c.laenge)} s</span>`:''}</div></div>
 <div class="pills">${kampagne(c)?'<span class="pill koralle">Kampagne</span>':(rechte?`<span class="pill">${esc(rechte)}</span>`:'')}${c.ut=='laeuft'?'<span class="pill bern">Untertitel folgen</span>':c.ut=='fertig'?'<span class="pill gut">Untertitel</span>':''}</div></div>
 ${ende?`<div class="fristbox" data-ende="${ende}" data-dauer="${c.frist_min*60000}"><div><span data-text></span><span class="mono" data-bis></span></div><div class="bar"><i></i></div></div>`:''}
 ${g||w.length?`<div class="moment">${g?`<q>${esc(g)}</q>`:''}${w.length?`<div class="chat">${w.map(x=>`<span>${esc(x)}</span>`).join('')}</div>`:''}</div>`:''}
 <div class="entscheid"><button class="ent gut ${c.status=='gut'?'an':''}" onclick="setze('${c.id}','gut')">${sv(P.check,17)}Gut<kbd>G</kbd></button><button class="ent weg ${c.status=='weg'?'an':''}" onclick="setze('${c.id}','weg')">${sv(P.x,17)}Weg<kbd>W</kbd></button></div>
 ${c.status!='neu'?`<div class="leise" style="text-align:center">Als ${c.status=='gut'?'gut':'verworfen'} markiert · <a href="#" onclick="setze('${c.id}','neu');return false">zurück zu Neu</a></div>`:''}
 <div class="stepper">${[['hook','Hook',s.hook],['caption','Caption',s.caption],['post','Posten',s.online]].map(([k,t,ok])=>`<button class="${p==k?'akt':''}" onclick="zeigePane('${c.id}','${k}')"><span class="hak ${ok?'an':''}">${ok?sv(P.check,11):''}</span>${t}</button>`).join('')}</div></div>
 <div class="pane" id="pane">${p=='hook'?paneHook(c):p=='caption'?paneCaption(c):panePost(c)}</div>`}
function zeigePane(id,k){pane[id]=k;teil()}
function teil(){if(tab!='clips')return;const c=aktuell(),a=$('#arbeit'),it=$('#items');if(!c||!a){render();return}
 const ae=document.activeElement,fid=ae&&a.contains(ae)&&ae.id,pos=fid&&ae.selectionStart,sc=($('#pane')||{}).scrollTop||0;
 a.innerHTML=arbeitHtml(c);if(it)it.innerHTML=itemsHtml(liste());const pn=$('#pane');if(pn)pn.scrollTop=sc;
 if(fid){const n=document.getElementById(fid);if(n){n.focus();try{n.setSelectionRange(pos,pos)}catch(e){}}}
 hookVorschau();tick()}

function paneHook(c){const v=hv[c.id];if(v===undefined)ladeVorschlag(c.id);
 const w=hookE[c.id]!=null?hookE[c.id]:(c.hook||''),n=w.trim()?w.trim().split(/ +/).length:0;
 return `<div class="lbl">Hook-Text <span class="zaehler ${n>8?'warn':''}" id="wz">${n} / 8 Wörter</span></div>
 <div class="feld"><input id="h_${c.id}" value="${esc(w)}" placeholder="z. B. Das hat keiner kommen sehen" maxlength="80" autocomplete="off" oninput="hookTipp('${c.id}',this.value)" onkeydown="if(event.key=='Enter')hook('${c.id}')">
 <button class="btn pri" ${busy[c.id]?'disabled':''} onclick="hook('${c.id}')">${busy[c.id]?'Wird eingebaut …':(c.hook?'Ändern':'Einbauen')}</button></div>
 <div class="leise">Steht in den ersten 2,5 Sekunden oben im Bild. Beim Tippen siehst du links eine Vorschau.</div>
 <div class="lbl">Vorschläge <span>aus Gesagtem und Chat</span></div>
 <div class="vorschlaege">${v?v.map(t=>`<button class="${w==t?'an':''}" data-t="${esc(t)}" onclick="hookWahl('${c.id}',this.dataset.t)"><span>${esc(t)}</span><i>${w==t?'gewählt':'übernehmen'}</i></button>`).join(''):'<div class="leise" style="margin:0;padding:11px 12px">Lädt …</div>'}</div>
 <div class="lbl">Hook-Typ <span>für die Auswertung im Tracker</span></div>
 <div class="typen">${TYPEN.map(t=>`<button class="typ ${c.hook_typ==t?'an':''}" onclick="typSetzen('${c.id}','${t}')">${t}</button>`).join('')}</div>`}
function hookTipp(id,val){hookE[id]=val;delete caps[id];const n=val.trim()?val.trim().split(/ +/).length:0,z=$('#wz');if(z){z.textContent=n+' / 8 Wörter';z.classList.toggle('warn',n>8)}hookVorschau()}
async function ladeVorschlag(id){if(hv[id]!==undefined)return;hv[id]=null;const r=await api('/api/hookvorschlag?id='+encodeURIComponent(id));hv[id]=r.vorschlaege||[];if(id==sel)teil()}
function hookWahl(id,t){hookE[id]=t;delete caps[id];teil();const i=document.getElementById('h_'+id);if(i){i.focus();i.setSelectionRange(t.length,t.length)}}
async function typSetzen(id,t){const c=clips.find(x=>x.id==id),neu=c&&c.hook_typ==t?'':t;const r=await api('/api/feld',{id,spalte:'Hook-Typ',wert:neu});
 if(r.fehler)return alert(r.fehler);toast(neu?'Hook-Typ: '+neu:'Hook-Typ entfernt');load({still:true})}
async function hook(id){const i=document.getElementById('h_'+id),t=(i?i.value:'').trim();if(!t){if(i)i.focus();return}
 busy[id]=true;teil();const r=await api('/api/hook',{id,text:t});busy[id]=false;
 if(r.fehler){teil();return alert('Hook konnte nicht eingebaut werden: '+r.fehler)}
 delete hookE[id];delete caps[id];hookV[id]=Date.now();autoplay=true;toast('Hook eingebaut');load({voll:true})}

function paneCaption(c){const k=caps[c.id];if(k===undefined)ladeCaption(c.id);if(!k)return '<div class="leise">Caption wird erstellt …</div>';
 return `<div class="lbl">Titel, Beschreibung, Hashtags <span class="zaehler ${k.laenge>100?'warn':''}">Titel ${k.laenge} / 100</span></div>
 <textarea id="cp_${c.id}" readonly rows="10">${esc(k.block)}</textarea>
 <div class="zeile"><button class="btn pri" style="flex:1" onclick="kopiere('${c.id}')">${sv(P.copy,16)}Caption kopieren</button></div>
 ${k.kampagne?`<div class="hinweis"><b>Kampagne:</b> „Werbung“ und die Pflichtangaben sind drin. Nachweis: ${esc(k.hinweis)}</div>`:''}
 <div class="leise">${hookE[c.id]&&hookE[c.id].trim()&&hookE[c.id].trim()!=c.hook?'Nutzt deinen getippten Hook. Er ist noch nicht im Video, bau ihn unter „Hook“ ein.':c.hook?'Der Titel nutzt deinen Hook.':'Mit einem Hook wird der Titel stärker. Bau zuerst einen ein.'}</div>`}
async function ladeCaption(id){if(caps[id]!==undefined)return;caps[id]=null;const e=hookE[id];const r=await api('/api/caption?id='+encodeURIComponent(id)+(e&&e.trim()?'&hook='+encodeURIComponent(e.trim()):''));
 if(r.fehler){delete caps[id];return alert(r.fehler)}caps[id]=r;if(id==sel)teil()}
function kopiere(id){const t=document.getElementById('cp_'+id);if(!t)return;const fertig=ok=>{if(ok){merkeKopiert(id);teil();toast('Caption kopiert')}else{t.select();toast('Bitte mit Strg+C kopieren')}};
 if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(t.value).then(()=>fertig(true),()=>{t.select();let ok=false;try{ok=document.execCommand('copy')}catch(e){}fertig(ok)});
 else{t.select();let ok=false;try{ok=document.execCommand('copy')}catch(e){}fertig(ok)}}

function panePost(c){const ch=check[c.id]||[];
 return `<div class="lbl">Vor dem Posten</div>
 <div class="checks">${CHECKS.map((t,i)=>`<label class="check"><input type="checkbox" id="ck_${i}" ${ch[i]?'checked':''} onchange="(check['${c.id}']=check['${c.id}']||[])[${i}]=this.checked"><span>${t}</span></label>`).join('')}</div>
 <div class="lbl">Dateien</div>
 <div class="zeile" style="margin-top:0"><a class="btn" href="${c.video}" download>${sv(P.down,16)}Video laden</a>${c.srt?`<a class="btn ghost" href="${c.srt}" download>Untertitel .srt</a>`:''}${kontoSt.upload_aktiv&&kontoSt.upload_scope&&c.status=='gut'?`<button class="btn" onclick="uploadOeffnen('${c.id}')">Hochladen …</button>`:''}</div>
 ${ups[c.id]?uploadPanel(c):''}
 <div class="lbl">YouTube-Link <span>nach dem Hochladen eintragen</span></div>
 <div class="feld"><input id="l_${c.id}" value="${esc(c.link)}" placeholder="https://youtube.com/shorts/…" autocomplete="off" onkeydown="if(event.key=='Enter')link('${c.id}')"><button class="btn" onclick="link('${c.id}')">Speichern</button></div>
 ${c.link?'<div class="leise">Der Tracker holt ab jetzt stündlich die Aufrufe.</div>':''}`}
async function link(id){const r=await api('/api/link',{id,link:document.getElementById('l_'+id).value});if(r.fehler)return alert(r.fehler);toast('Link gespeichert');load({still:true})}

function waehle(id){if(id==sel)return;sel=id;autoplay=true;render()}
async function setze(id,s){const l=liste(),i=l.findIndex(c=>c.id==id),nach=l[i+1]||l[i-1];
 const r=await api('/api/status',{id,status:s});if(r.fehler)return alert(r.fehler);
 if(s!=filter){sel=nach?nach.id:null;autoplay=!!nach}toast(s=='gut'?'Als gut markiert':s=='weg'?'Verworfen':'Zurück in Neu');load({voll:true})}

function tick(){const jetzt=Date.now();
 document.querySelectorAll('.frist[data-ende]').forEach(e=>{const [t,k]=fristText(+e.dataset.ende);e.textContent=t;e.className='frist '+k});
 document.querySelectorAll('.fristbox[data-ende]').forEach(e=>{const ende=+e.dataset.ende,d=+e.dataset.dauer,r=ende-jetzt,[t,k]=fristText(ende);
  e.className='fristbox '+k;e.querySelector('[data-text]').textContent=r>0?'Kampagne: noch '+t+' zum Einreichen':'Einreichfrist der Kampagne ist vorbei';
  const b=new Date(ende);e.querySelector('[data-bis]').textContent='bis '+String(b.getHours()).padStart(2,'0')+':'+String(b.getMinutes()).padStart(2,'0');
  e.querySelector('i').style.width=Math.max(0,Math.min(100,r/d*100))+'%'})}

const AUSW=[['locker','Locker','Viele Clips, auch kleine Ausschläge'],['normal','Normal','Deutliche Ausschläge'],['streng','Streng','Nur starke Momente, höchstens 3 pro Stunde und Kanal'],['highlights','Nur Highlights','Die allerbesten, höchstens 2 pro Stunde und Kanal']];
let wa={};
async function laenge(s){const r=await api('/api/einstellung',{clip_laenge_s:s});if(r.fehler)return alert(r.fehler);toast('Neue Clips: '+s+' Sekunden');load({voll:true})}
async function auswahl(k){const r=await api('/api/einstellung',{auswahl:k});if(r.fehler)return alert(r.fehler);toast('Auswahl: '+(AUSW.find(a=>a[0]==k)||[])[1]);load({voll:true})}
async function ladeWartung(){wa=await api('/api/wartung');if(tab=='einstellungen'){const b=$('#speicher');if(b)b.outerHTML=speicherHtml()}}
function speicherHtml(){const r=wa.regeln;if(!r)return '<section class="karte" id="speicher"><div class="khead"><h3>Speicher</h3></div><div class="leise">Lädt …</div></section>';
 const z=wa.zuletzt?new Date(wa.zuletzt):null,t=z?String(z.getHours()).padStart(2,'0')+':'+String(z.getMinutes()).padStart(2,'0'):null,n=(wa.bericht||[]).length;
 return `<section class="karte" id="speicher"><div class="khead"><div><h3>Speicher</h3><span class="klein">Aufgeräumt wird automatisch alle paar Minuten.</span></div></div><div class="klein">Frei auf der Festplatte: <b class="mono" style="color:var(--ink)">${wa.frei_gb} GB</b>. </div>
 <ul class="regeln"><li>Verworfene Clips: nach <b>${r.weg_minuten} Min</b> gelöscht</li><li>Nie angesehene Clips: nach <b>${r.neu_stunden} Std</b> gelöscht</li><li>Clips mit YouTube-Link: nach <b>${r.gut_online_stunden} Std</b> gelöscht</li><li>Rohdatei (roh.mp4) übriger Clips: nach <b>${r.roh_stunden} Std</b> gelöscht</li></ul>
 <div class="klein">Gut markierte Clips ohne Link bleiben, bis du den Link einträgst. Die Zahlen im Tracker bleiben immer erhalten.</div>
 <div class="zeile"><button class="btn" onclick="aufraeumenJetzt()">Jetzt aufräumen</button>${t?`<span class="klein">Zuletzt ${t} Uhr, ${n} ${n==1?'Eintrag':'Einträge'} entfernt</span>`:''}</div></section>`}
async function aufraeumenJetzt(){const r=await api('/api/aufraeumen',{});if(r.fehler)return alert(r.fehler);await ladeWartung();toast('Aufgeräumt')}

async function load(opt){opt=opt||{};let c,cf,s,ko;try{[c,cf,s,ko]=await Promise.all([api('/api/clips'),api('/api/config'),api('/api/status-watcher'),api('/api/konto')])}catch(e){return}
 clips=c.clips;cfg=cf;st=s;kontoSt=ko;const live=s.aufnahme||[];
 $('#stat').innerHTML=watcherKarte();
 if(bekannt){clips.filter(x=>!bekannt.has(x.id)&&x.status=='neu').slice(0,3).forEach(melde)}bekannt=new Set(clips.map(x=>x.id));
 $('#banner').innerHTML=(s.hinweis?`<div class="warn">${esc(s.hinweis)}</div>`:'')+(s.platz_warnung?`<div class="warn">Nur noch ${s.frei_gb} GB Festplatte frei (Warnung unter ${s.min_frei_gb} GB). Clips mit „Weg“ markieren oder alte Aufnahmen löschen.</div>`:'')+Object.entries(s.fehler||{}).map(([k,v])=>`<div class="warn">${esc(k)}: ${esc(v)}</div>`).join('');
 const neu=JSON.stringify([c.clips,cf,ko,live,s.laeuft]);tabs();
 if(opt.still){sig=neu;teil();return}
 if(tab=='log'){if(!tippt())render();return}
 if(neu===sig&&!opt.voll)return;
 if(!opt.voll&&tab=='clips'&&(tippt()||videoBenutzt())){if(!neuWartet){neuWartet=true;const a=$('.ablinfo');if(a&&!a.querySelector('.neuhin'))a.insertAdjacentHTML('beforeend','<button class="neuhin" onclick="neuLaden()">Neue Clips</button>')}return}
 sig=neu;neuWartet=false;render()}
function videoBenutzt(){const v=$('#vid');return !!v&&(!v.paused||v.currentTime>0.3)}
function neuLaden(){neuWartet=false;load({voll:true})}

document.addEventListener('keydown',e=>{if(e.key=='Escape'&&!$('#hilfe').hidden){hilfeZu();return}if(e.key=='?'&&!tippt()){e.preventDefault();hilfe();return}});
document.addEventListener('keydown',e=>{if(tippt()||tab!='clips'||e.ctrlKey||e.metaKey||e.altKey)return;
 const l=liste(),i=l.findIndex(c=>c.id==sel),v=$('#vid'),k=e.key.toLowerCase();
 if(k=='g'&&sel){e.preventDefault();setze(sel,'gut')}else if(k=='w'&&sel){e.preventDefault();setze(sel,'weg')}
 else if(k=='arrowdown'||k=='j'){e.preventDefault();if(l[i+1])waehle(l[i+1].id)}
 else if(k=='arrowup'||k=='k'){e.preventDefault();if(i>0)waehle(l[i-1].id)}
 else if(k=='arrowright'&&v){e.preventDefault();v.currentTime=Math.min(v.duration||0,v.currentTime+1)}
 else if(k=='arrowleft'&&v){e.preventDefault();v.currentTime=Math.max(0,v.currentTime-1)}
 else if(k=='m'){e.preventDefault();stummUmschalten()}
 else if(k==' '&&v){e.preventDefault();v.paused?v.play():v.pause()}});
setInterval(tick,15000);
/* ---------- Navigation: Seitenleiste und Handy-Leiste ---------- */
const NAV=[['start','Übersicht','<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>'],
 ['clips','Clips','<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>'],
 ['tracker','Tracker','<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'],
 ['trends','Trends','<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>'],
 ['kanaele','Kanäle','<rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M8 2.5 12 6l4-3.5"/>'],
 ['einstellungen','Einstellungen','<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'],
 ['log','Protokoll','<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>']];
function tabs(){const n=clips.filter(c=>c.status=='neu').length;
 const btn=([k,t,ic],mob)=>`<button class="${tab==k?'akt':''}" onclick="geheZu('${k}')" ${tab==k?'aria-current="page"':''}>${sv(ic,mob?20:18)}<span>${t}</span>${k=='clips'&&n?`<span class="zahl">${n}</span>`:''}</button>`;
 $('#tabs').innerHTML=NAV.slice(0,6).map(x=>btn(x)).join('')+'<span class="trenn"></span>'+btn(NAV[6]);
 $('#mnav').innerHTML=NAV.slice(0,6).map(x=>btn(x,1)).join('')}
function geheZu(k){tab=k;render();const m=$('#main');if(m)m.scrollTop=0;window.scrollTo(0,0)}
function oeffneClip(id){const c=clips.find(x=>x.id==id);if(!c)return;filter=c.status;sel=id;autoplay=true;tab='clips';render()}

function render(){tabs();const m=$('#main');
 if(tab=='clips'){m.innerHTML=platzHtml();bindPlayer();tick();return}
 if(tab=='start'){m.innerHTML=startHtml();tick();if(Date.now()-startZeit>20000)ladeStartDaten();return}
 if(tab=='kanaele'&&cfg){m.innerHTML=seite('Kanäle','Die Twitch-Kanäle, die der Watcher überwacht. Fremde Kanäle nur mit Nachweis.',kanaeleHtml(),watcherKnopf());return}
 if(tab=='einstellungen'&&cfg){m.innerHTML=seite('Einstellungen','Alles, was du an clip-watcher einstellen kannst, an einem Ort.',einstellungenHtml());if(!wa.regeln)ladeWartung();return}
 if(tab=='trends'){api('/api/trend').then(r=>{if(tab=='trends')m.innerHTML=seite('Trends','GTA-6-Shorts der letzten 72 Stunden aus Deutschland, sortiert nach Aufrufen pro Stunde. Zum Lernen von Mustern, nicht zum Kopieren.',trendSeite(r),r.schluessel?'<button class="btn" onclick="trendNeu()">Jetzt abrufen</button>':'')});return}
 if(tab=='tracker'){api('/api/tracker').then(r=>{if(tab=='tracker')m.innerHTML=seite('Tracker','Wie deine hochgeladenen Shorts laufen. Den YouTube-Link trägst du beim Clip unter „Posten“ ein.',trackerSeite(r),r.schluessel?'<button class="btn" onclick="trackerNeu()">Jetzt aktualisieren</button>':'')});return}
 if(tab=='log'){api('/api/log').then(r=>{if(tab!='log')return;m.innerHTML=seite('Protokoll','Was der Watcher gerade tut. Die neuesten Zeilen stehen unten.','<pre class="log">'+logHtml(r.log)+'</pre>');m.scrollTop=m.scrollHeight})}}
function seite(t,p,inhalt,akt){return `<div class="seite"><div class="skopf"><div><h1>${t}</h1><p>${p}</p></div>${akt?`<div class="sakt">${akt}</div>`:''}</div>${inhalt}</div>`}

/* ---------- Übersicht ---------- */
let trk=null,logTxt='',startZeit=0;
async function ladeStartDaten(){startZeit=Date.now();try{const [t,l]=await Promise.all([api('/api/tracker'),api('/api/log')]);trk=t;logTxt=l.log||''}catch(e){return}if(tab=='start')render()}
function gruss(){const h=new Date().getHours();return h<5?'Gute Nacht':h<11?'Guten Morgen':h<18?'Hallo':'Guten Abend'}
function heute(c){const d=new Date(),s=String(d.getDate()).padStart(2,'0')+'.'+String(d.getMonth()+1).padStart(2,'0')+'.'+d.getFullYear();return (c.zeit||'').startsWith(s)}
function einrichtung(){const T=trk&&trk.daten;return [
 ['Kanal eintragen',!!(cfg&&cfg.kanaele&&cfg.kanaele.length),"geheZu('kanaele')",'Kanäle'],
 ['Watcher starten',!!st.laeuft,"watcherStart()",'Starten'],
 ['YouTube-Daten verbinden (API-Schlüssel)',!!(cfg&&cfg.youtube_api_key_gesetzt),"geheZu('einstellungen')",'Einrichten'],
 ['YouTube-Konto verbinden',!!kontoSt.verbunden,"geheZu('einstellungen')",'Verbinden'],
 ['Ersten Short hochladen und Link eintragen',clips.some(c=>c.link)||!!(T&&T.zeilen.some(z=>z.hat_link)),"geheZu('clips')",'Zu den Clips']]}
function startHtml(){const neu=clips.filter(c=>c.status=='neu'),live=st.aufnahme||[],T=trk&&trk.daten,w=T&&T.woche;
 const satz=[neu.length?`<b>${neu.length} ${neu.length==1?'Clip wartet':'Clips warten'}</b> aufs Sichten`:'Alles gesichtet',
  !st.laeuft?'der Watcher ist aus':live.length?`<b>${live.length} ${live.length==1?'Kanal':'Kanäle'} live</b>`:'gerade ist kein Kanal live'].join(', ')+'.';
 const e=einrichtung(),fertig=e.filter(x=>x[1]).length;
 const fristen=clips.filter(c=>fristEnde(c)&&fristEnde(c)>Date.now()).sort((a,b)=>fristEnde(a)-fristEnde(b));
 const kpi=(wert,titel,sub,ziel)=>`<button class="kpi" ${ziel?`onclick="${ziel}"`:'disabled'}><span class="kt">${titel}</span><b>${wert}</b><span class="ks">${sub||''}</span></button>`;
 const aelteste=neu.length?Math.round((Date.now()-Math.min(...neu.map(c=>c.zeit_ts||Date.now())))/60000):0;
 return `<div class="seite breit"><div class="skopf"><div><h1>${gruss()}</h1><p class="satz">${satz}</p></div>${neu.length?`<div class="sakt"><button class="btn pri" onclick="filter='neu';geheZu('clips')">Clips sichten</button></div>`:''}</div>
 <div class="kpis">${kpi(neu.length,'Zu sichten',neu.length?`ältester vor ${aelteste<60?aelteste+' Min':Math.round(aelteste/60)+' Std'}`:'nichts offen',"filter='neu';geheZu('clips')")}
  ${kpi(clips.filter(heute).length,'Heute geschnitten',`${clips.filter(c=>heute(c)&&c.status=='gut').length} davon gut`,"filter='gut';geheZu('clips')")}
  ${kpi(w?w.hochgeladen:'–','Hochgeladen',`letzte 7 Tage`,"geheZu('tracker')")}
  ${kpi(w?zahl(w.aufrufe):'–','Aufrufe',w&&w.median!=null?`Median ${zahl(w.median)} pro Short`:'letzte 7 Tage',"geheZu('tracker')")}</div>
 <div class="raster"><div class="spalte">
  ${fristen.length?`<section class="karte"><div class="khead"><h3>Jetzt einreichen</h3><span class="klein">Kampagnen-Clips mit laufender Frist</span></div>${fristen.map(c=>`<button class="zeile-k" onclick="oeffneClip('${c.id}')"><span class="mini">${c.vorschau?`<img src="${c.vorschau}" alt="">`:''}</span><span class="zt"><b>${esc(c.quelle)}</b><span>${esc(c.hook||gesagt(c)||'Ohne Hook')}</span></span><span class="frist" data-ende="${fristEnde(c)}"></span></button>`).join('')}</section>`:''}
  <section class="karte"><div class="khead"><h3>Zu sichten</h3>${neu.length>6?`<button class="link" onclick="filter='neu';geheZu('clips')">Alle ${neu.length}</button>`:''}</div>
   ${neu.length?`<div class="streifen">${neu.slice(0,6).map(c=>`<button class="kachel" onclick="oeffneClip('${c.id}')"><span class="kbild">${c.vorschau?`<img src="${c.vorschau}" alt="">`:''}<span class="dauer">${uhr(c)}</span></span><b>${esc(c.quelle)}</b><span>${esc(c.hook||gesagt(c)||'')}</span></button>`).join('')}</div>`:'<div class="leise">Keine neuen Clips. Sobald ein Kanal live ist und der Chat ausschlägt, erscheinen sie hier.</div>'}</section>
  <section class="karte"><div class="khead"><h3>Beste Shorts</h3><button class="link" onclick="geheZu('tracker')">Zum Tracker</button></div>${besteHtml(T)}</section>
 </div><div class="spalte">
  ${fertig<e.length?`<section class="karte setup"><div class="khead"><div><h3>Einrichtung</h3><span class="klein">${fertig} von ${e.length} erledigt</span></div><div class="pbar"><i style="width:${fertig/e.length*100}%"></i></div></div>
  <ol class="schrittliste">${e.map(([t,ok,ziel,knopf])=>`<li class="${ok?'ok':''}"><span class="hak">${ok?sv(P.check,12):''}</span><span>${t}</span>${ok?'':`<button class="btn klein-btn" onclick="${ziel}">${knopf}</button>`}</li>`).join('')}</ol></section>`:''}
  <section class="karte"><div class="khead"><h3>Kanäle</h3><button class="link" onclick="geheZu('kanaele')">Verwalten</button></div>${liveHtml()}</section>
  <section class="karte"><div class="khead"><h3>Aktivität</h3><button class="link" onclick="geheZu('log')">Protokoll</button></div>${aktivitaetHtml()}</section>
 </div></div></div>`}
function besteHtml(T){if(!T)return '<div class="leise">Lädt …</div>';const z=T.zeilen.filter(x=>x.hat_link&&x.aufrufe!=null).sort((a,b)=>b.aufrufe-a.aufrufe).slice(0,5);
 if(!z.length)return '<div class="leise">Noch keine Zahlen. Trag beim Clip den YouTube-Link ein, dann holt der Tracker stündlich die Aufrufe.</div>';
 const max=Math.max(...z.map(x=>x.aufrufe),1);return `<div class="balken">${z.map(x=>`<div class="bz"><span class="bl"><b>${esc(x.quelle)}</b> <span class="mono">${esc((x.clip.split('_').pop()||'').replace(/^(..)(..).*/,'$1:$2'))}</span></span><span class="bb"><i style="width:${Math.max(2,x.aufrufe/max*100)}%"></i></span><span class="bw mono">${zahl(x.aufrufe)}</span></div>`).join('')}</div>`}
function liveHtml(){const live=st.aufnahme||[],k=(cfg&&cfg.kanaele)||[];if(!k.length)return '<div class="leise">Noch kein Kanal eingetragen.</div>';
 return `<div class="kliste">${k.slice().sort((a,b)=>live.includes(b.name)-live.includes(a.name)).map(x=>{const on=live.includes(x.name),n=clips.filter(c=>c.quelle==x.name&&heute(c)).length;
  return `<div class="kl"><span class="ava ${on?'on':''}">${esc(x.name.slice(0,1).toUpperCase())}</span><span class="zt"><b>${esc(x.name)}</b><span>${on?'<span class="rec">REC</span> Aufnahme läuft':'offline'}</span></span><span class="mono klein">${n} heute</span></div>`}).join('')}</div>`}
function ereignisse(){return (logTxt||'').split('\\n').filter(z=>z.startsWith('[')).slice(-60).map(z=>{const zeit=z.slice(1,6),r=z.slice(11);const a=r.indexOf('['),b=r.indexOf(']');
 const kanal=a==0&&b>0?r.slice(1,b):'',t=a==0&&b>0?r.slice(b+2):r;let art='info',text=t;
 if(t.startsWith('Ausschlag!')){art='aus';const m=t.match(/Chat ([0-9]+) .normal ([0-9]+)/);text=m?`Ausschlag erkannt: Chat ${m[1]} statt ${m[2]}`:'Ausschlag erkannt'}
 else if(t.includes('FERTIG')){art='fertig';text='Clip fertig geschnitten'}
 else if(t.includes('übersprungen')){art='skip';text=t.replace(/ Stundenlimit der Auswahl.*$/,' Stundenlimit erreicht').replace('Ausschlag','Ausschlag')}
 else if(t.startsWith('LIVE')){art='live';text='ist live, Aufnahme läuft'}
 else if(t.startsWith('Session beendet')){art='info';text='Stream vorbei, '+t.replace('Session beendet, ','')}
 else if(/Fehler|fehlgeschlagen|abgestürzt|fehlt/.test(t)){art='fehler'}
 else if(t.startsWith('Warte')||t.startsWith('Läuft für')||t.startsWith('Untertitel')||t.startsWith('Lade Modell'))return null;
 return {zeit,kanal,art,text}}).filter(Boolean).reverse().slice(0,8)}
function aktivitaetHtml(){const e=ereignisse();if(!e.length)return '<div class="leise">Noch nichts passiert.</div>';
 const ic={aus:P.blitz,fertig:P.check,live:P.radar,skip:P.minus,fehler:P.x,info:P.punkt};
 return `<ol class="feed">${e.map(x=>`<li class="${x.art}"><span class="fi">${sv(ic[x.art]||P.punkt,14)}</span><span class="ft">${x.kanal?`<b>${esc(x.kanal)}</b> `:''}${esc(x.text)}</span><span class="mono klein">${esc(x.zeit)}</span></li>`).join('')}</ol>`}

/* ---------- Watcher-Karte in der Seitenleiste ---------- */
function watcherKarte(){const live=st.aufnahme||[];
 if(!st.laeuft)return `<div class="wk aus"><span class="dot"></span><span class="wt"><b>Watcher aus</b><span>Nimmt nichts auf</span></span><button class="btn klein-btn pri" onclick="watcherStart()">Starten</button></div>`;
 if(live.length)return `<button class="wk rec" onclick="geheZu('kanaele')"><span class="dot rec"></span><span class="wt"><b><span class="rec">REC</span> ${live.length} live</b><span>${esc(live.slice(0,3).join(', '))}${live.length>3?' +'+(live.length-3):''}</span></span></button>`;
 return `<button class="wk" onclick="geheZu('kanaele')"><span class="dot bereit"></span><span class="wt"><b>Bereit</b><span>Wartet auf Livestreams</span></span></button>`}
function watcherKnopf(){return st.laeuft?'<button class="btn ghost weg" onclick="watcherStop()">Watcher stoppen</button><button class="btn" onclick="watcherStart()">Neu starten</button>':'<button class="btn pri" onclick="watcherStart()">Watcher starten</button>'}
async function watcherStart(){await api('/api/watcher',{aktion:'start'});toast('Watcher gestartet');load({voll:true})}
async function watcherStop(){if(!confirm('Watcher stoppen? Laufende Aufnahmen enden.'))return;await api('/api/watcher',{aktion:'stop'});toast('Watcher gestoppt');load({voll:true})}

/* ---------- Kanäle ---------- */
function kanaeleHtml(){const live=st.aufnahme||[],k=cfg.kanaele||[];
 return `<section class="karte"><div class="khead"><h3>Überwachte Kanäle</h3><span class="klein">${k.length} ${k.length==1?'Kanal':'Kanäle'}${live.length?`, ${live.length} live`:''}</span></div>
 ${k.length?`<div class="kliste gross">${k.map(x=>{const on=live.includes(x.name),n=clips.filter(c=>c.quelle==x.name&&heute(c)).length;
  return `<div class="kl"><span class="ava ${on?'on':''}">${esc(x.name.slice(0,1).toUpperCase())}</span><span class="zt"><b>${esc(x.name)} <span class="tag">${esc(x.rechte)}</span></b><span class="nachweis">${esc(x.nachweis||'eigener Kanal')}</span></span>
  <span class="kmeta">${on?'<span class="pill koralle"><span class="rec">REC</span> live</span>':'<span class="pill">offline</span>'}<span class="mono klein">${n} Clips heute</span></span><button class="btn ghost weg klein-btn" onclick="loesche('${esc(x.name)}')">Entfernen</button></div>`}).join('')}</div>`:'<div class="leise">Noch kein Kanal eingetragen.</div>'}</section>
 <section class="karte"><div class="khead"><h3>Kanal hinzufügen</h3></div><div class="hinweis">Fremde Kanäle nur mit Grundlage: Kampagne oder Erlaubnis des Streamers. Trag als Nachweis den Link oder eine Notiz ein, sonst startet der Kanal nicht.</div>
 <div class="formgrid"><label>Kanalname<input id="kn" placeholder="z. B. clix (ohne twitch.tv/)" autocomplete="off"></label><label>Grundlage<select id="kr"><option value="kampagne">Kampagne</option><option value="erlaubnis">Erlaubnis</option><option value="eigen">Eigener Kanal</option></select></label>
 <label class="voll">Nachweis<input id="kv" placeholder="Link zur Kampagne oder Notiz zur Erlaubnis" autocomplete="off"></label></div>
 <div class="zeile"><button class="btn pri" onclick="kanal()">Kanal hinzufügen</button><span class="klein">Der Watcher startet dafür kurz neu.</span></div></section>`}

/* ---------- Einstellungen ---------- */
function einstellungenHtml(){const L=cfg.clip_laenge_s||18,A=cfg.auswahl||'streng';
 const abschnitt=(id,t,u,inhalt)=>`<section class="karte" id="e_${id}"><div class="khead"><div><h3>${t}</h3>${u?`<span class="klein">${u}</span>`:''}</div></div>${inhalt}</section>`;
 return `<nav class="sprung">${[['auswahl','Clip-Auswahl'],['laenge','Clip-Länge'],['speicher','Speicher'],['meldung','Benachrichtigungen'],['daten','YouTube-Daten'],['konto','YouTube-Konto'],['programm','Programm']].map(([k,t])=>`<a href="#e_${k}" onclick="document.getElementById('e_${k}').scrollIntoView({behavior:'smooth'});return false">${t}</a>`).join('')}</nav>
 ${abschnitt('auswahl','Clip-Auswahl','Wie streng der Watcher ist. Gilt sofort, ohne Neustart.',`<div class="auswahl">${AUSW.map(([k,t,u])=>`<button class="${A==k?'an':''}" onclick="auswahl('${k}')"><b>${t}</b><span>${u}</span></button>`).join('')}</div>`)}
 ${abschnitt('laenge','Clip-Länge','Gilt für neue Clips. Die Pointe liegt bei etwa zwei Dritteln.',`<div class="laengen">${[15,16,17,18,19,20].map(s=>`<button class="typ ${L==s?'an':''}" onclick="laenge(${s})">${s} s</button>`).join('')}</div>`)}
 ${speicherHtml()}
 ${abschnitt('meldung','Benachrichtigungen','Damit du keinen Clip verpasst, vor allem bei Kampagnen mit Frist.',meldungHtml())}
 ${abschnitt('daten','YouTube-Daten','Für Trends und die Aufrufe im Tracker. Kostenlos, 10.000 Punkte pro Tag.',keyHtml())}
 ${abschnitt('konto','YouTube-Konto','Für Durchsehrate und Wiedergabedauer deiner eigenen Shorts. Nur lesend, Upload ist aus.',kontoHtml())}
 ${abschnitt('programm','Programm','',programmHtml())}`}
function schalter(id,an,onchange,titel,text){return `<label class="schalter"><span class="st"><b>${titel}</b>${text?`<span>${text}</span>`:''}</span><input type="checkbox" id="${id}" ${an?'checked':''} onchange="${onchange}"><span class="sw" aria-hidden="true"></span></label>`}
function lsGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
function lsSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
function meldungHtml(){const erlaubt=typeof Notification!='undefined'?Notification.permission:'nicht';
 return schalter('mdesk',lsGet('cw_meld')=='1'&&erlaubt=='granted',"meldungUmschalten(this.checked)",'Desktop-Meldung bei neuem Clip',erlaubt=='denied'?'Im Browser blockiert. Erlaube Benachrichtigungen für localhost in den Website-Einstellungen.':'Erscheint auch, wenn das Fenster im Hintergrund ist.')
 +schalter('mton',lsGet('cw_ton')!='0',"lsSet('cw_ton',this.checked?'1':'0');if(this.checked)klang()",'Kurzer Ton bei neuem Clip','Zwei leise Töne, wenn die App offen ist.')
 +'<div class="zeile"><button class="btn ghost" onclick="testMeldung()">Test-Meldung senden</button></div>'}
async function meldungUmschalten(an){if(!an){lsSet('cw_meld','0');return}
 if(typeof Notification=='undefined'){alert('Dieser Browser kann keine Desktop-Meldungen.');return render()}
 const p=Notification.permission=='granted'?'granted':await Notification.requestPermission();lsSet('cw_meld',p=='granted'?'1':'0');if(p!='granted')toast('Benachrichtigungen wurden nicht erlaubt');render()}
function klang(){try{const a=new (window.AudioContext||window.webkitAudioContext)();[0,.16].forEach((t,i)=>{const o=a.createOscillator(),g=a.createGain();o.frequency.value=i?1046:784;g.gain.setValueAtTime(.0001,a.currentTime+t);g.gain.exponentialRampToValueAtTime(.12,a.currentTime+t+.02);g.gain.exponentialRampToValueAtTime(.0001,a.currentTime+t+.22);o.connect(g);g.connect(a.destination);o.start(a.currentTime+t);o.stop(a.currentTime+t+.25)})}catch(e){}}
function melde(c){const ende=fristEnde(c),rest=ende?ende-Date.now():0,text=rest>0?`Kampagne: noch ${Math.ceil(rest/60000)} Min zum Einreichen`:(c.hook||gesagt(c)||'Neuer Moment erkannt');
 toast('Neuer Clip von '+c.quelle);if(lsGet('cw_ton')!='0')klang();
 if(lsGet('cw_meld')=='1'&&typeof Notification!='undefined'&&Notification.permission=='granted'){try{const n=new Notification('Neuer Clip von '+c.quelle,{body:text,tag:c.id});n.onclick=()=>{window.focus();oeffneClip(c.id);n.close()}}catch(e){}}}
function testMeldung(){const c=clips[0]||{quelle:'clix',id:'',hook:'So sieht eine Meldung aus'};melde(c)}
function keyHtml(){const ok=cfg.youtube_api_key_gesetzt;
 return `<div class="status ${ok?'gut':''}">${ok?sv(P.check,14)+'Verbunden, der Schlüssel ist gespeichert.':'Noch kein API-Schlüssel eingetragen.'}</div>
 <div class="feld" style="margin-top:12px"><input id="ak" type="password" placeholder="${ok?'Neuen Schlüssel eintragen, um ihn zu ersetzen':'API-Schlüssel einfügen'}" autocomplete="off"><button class="btn ${ok?'':'pri'}" onclick="schluessel()">Speichern</button></div>
 <details class="hilfe-d"><summary>So bekommst du den Schlüssel</summary><ol><li>console.cloud.google.com öffnen und mit deinem Google-Konto anmelden.</li><li>Oben ein Projekt wählen oder neu anlegen.</li><li>„APIs &amp; Dienste“ → „Bibliothek“ → „YouTube Data API v3“ → Aktivieren.</li><li>„Anmeldedaten“ → „Anmeldedaten erstellen“ → „API-Schlüssel“.</li><li>Schlüssel kopieren und hier einfügen.</li></ol></details>`}
function kontoHtml(){const k=kontoSt;let h='';
 if(!k.client_da)h+=`<div class="status">Die Datei <code>client_secret.json</code> fehlt im Programmordner.</div><details class="hilfe-d"><summary>So richtest du es ein</summary><ol><li>Google Cloud Console → Google Auth Platform.</li><li>Audience: External, Status Testing, dich selbst als Test user eintragen.</li><li>Clients → Create client → Desktop app → Download JSON.</li><li>Datei in <code>client_secret.json</code> umbenennen (Windows: nur eine Endung .json) und neben app.py legen.</li><li>YouTube Analytics API aktivieren.</li></ol></details>`;
 else if(!k.verbunden)h+=`<div class="status">Nicht verbunden.</div><div class="zeile"><button class="btn pri" onclick="kontoLogin()">Mit Google verbinden</button><span class="klein">Bei „App nicht überprüft“: Erweitert → Weiter.</span></div>`;
 else h+=`<div class="status gut">${sv(P.check,14)}Verbunden. Die Durchsehrate wird stündlich geholt.</div><div class="zeile"><button class="btn" onclick="durchNeu()">Jetzt holen</button><button class="btn ghost weg" onclick="kontoTrennen()">Verbindung trennen</button></div>`;
 h+='<div class="trenner"></div>'+schalter('upsw',k.upload_aktiv,"uploadSchalter(this.checked)",'Upload per API erlauben','Standard: aus. Dann erscheint bei guten Clips „Hochladen …“, immer mit Vorschau und Bestätigung, Sichtbarkeit standardmäßig privat.');
 if(k.upload_aktiv&&!k.upload_scope)h+='<div class="hinweis">Für den Upload fehlt die Berechtigung. Trenne die Verbindung und verbinde neu.</div>';
 return h}
function programmHtml(){const u=window.updInfo||{},i=u.info||{};
 return `<div class="prog"><div><b>clip-watcher</b><span class="klein">Version <span class="mono">${esc(i.installiert||'unbekannt')}</span>${i.neu?' · Update verfügbar':i.installiert?' · aktuell':''}</span></div><div class="zeile" style="margin:0"><button class="btn" onclick="updatePruefen()">Auf Update prüfen</button>${i.neu?'<button class="btn pri" onclick="updateStart()">Jetzt aktualisieren</button>':''}</div></div>
 <div class="zeile"><button class="btn ghost" onclick="hilfe()">Tastenkürzel</button><button class="btn ghost" onclick="geheZu('log')">Protokoll</button></div>`}

/* ---------- Tracker und Trends ---------- */
function fehltKey(was){return `<div class="hinweis zeile-h"><span>${was} braucht den YouTube-API-Schlüssel.</span><button class="btn klein-btn" onclick="geheZu('einstellungen')">Einrichten</button></div>`}
function trendSeite(r){let h=r.schluessel?'':fehltKey('Trends');if(!r.daten&&!r.schluessel)return h;return h+trendHtml(Object.assign({},r,{schluessel:true}))}
function trackerSeite(r){const t=r.daten,w=t.woche;let h=r.schluessel?'':fehltKey('Der Tracker');if(r.fehler)h+=`<div class="warn">${esc(r.fehler)}</div>`;
 const best=t.bester,schl=t.schlechtester,mit=t.zeilen.filter(z=>z.hat_link&&z.aufrufe!=null).sort((a,b)=>b.aufrufe-a.aufrufe),max=Math.max(1,...mit.map(z=>z.aufrufe));
 const kpi=(b,t,s)=>`<div class="kpi"><span class="kt">${t}</span><b>${b}</b><span class="ks">${s||''}</span></div>`;
 h+=`<div class="kpis">${kpi(zahl(t.median),'Median Aufrufe',`aus ${mit.length} Shorts`)}${kpi(best?zahl(best.aufrufe):'–','Bester Short',best?esc(best.quelle):'')}${kpi(w.hochgeladen,'Hochgeladen',`letzte 7 Tage, ${zahl(w.aufrufe)} Aufrufe`)}${kpi(t.ohne_link,'Ohne Link',`von ${t.gesamt} Clips`)}</div>`;
 h+=`<section class="karte"><div class="khead"><div><h3>Aufrufe pro Short</h3><span class="klein">Stand ${t.stand?esc(t.stand.replace('T',' ').slice(0,16)):'noch nicht abgerufen'}</span></div></div>${mit.length?`<div class="balken gross">${mit.slice(0,12).map(z=>`<div class="bz"><span class="bl"><b>${esc(z.quelle)}</b> <span class="mono">${esc(z.datum.slice(0,5))}</span>${z.hook_typ?` <span class="tag">${esc(z.hook_typ)}</span>`:''}</span><span class="bb"><i style="width:${Math.max(2,z.aufrufe/max*100)}%"></i></span><span class="bw mono">${zahl(z.aufrufe)}${z.durchgesehen!=null?`<small>${z.durchgesehen} %</small>`:''}</span></div>`).join('')}</div>`:'<div class="leise">Noch keine Zahlen. Trag beim Clip unter „Posten“ den YouTube-Link ein.</div>'}</section>`;
 h+=`<section class="karte tabelle"><div class="khead"><h3>Alle Clips</h3><span class="klein">${t.gesamt} Einträge</span></div><div class="tscroll"><table><tr><th>Datum</th><th>Quelle</th><th>Clip</th><th class="z">Aufrufe</th><th class="z">Likes</th><th class="z">Komm.</th><th class="z">Durchges.</th><th>Link</th></tr>`+t.zeilen.slice().reverse().map(z=>`<tr><td class="mono">${esc(z.datum)}</td><td>${esc(z.quelle)}</td><td class="mono klein">${esc(z.clip.split('/').pop())}</td><td class="z">${zahl(z.aufrufe)}</td><td class="z">${zahl(z.likes)}</td><td class="z">${zahl(z.kommentare)}</td><td class="z">${z.durchgesehen==null?'–':z.durchgesehen+' %'}</td><td>${z.hat_link?`<a href="${esc(z.link)}" target="_blank" rel="noopener">ansehen</a>`:'<span class="klein">–</span>'}</td></tr>`).join('')+'</table></div></section>';
 return h+auswHtml(t.auswertung)}

/* ---------- Tastenkürzel ---------- */
function hilfe(){const h=$('#hilfe');h.innerHTML=`<div class="modal" role="dialog" aria-label="Tastenkürzel"><div class="khead"><h3>Tastenkürzel</h3><button class="btn ghost klein-btn" onclick="hilfeZu()">Schließen</button></div>
 <dl class="kuerzel">${[['G','Clip als gut markieren'],['W','Clip verwerfen'],['↑ ↓','vorheriger, nächster Clip'],['Leertaste','Play und Pause'],['← →','eine Sekunde zurück, vor'],['M','Ton an oder aus'],['?','diese Übersicht']].map(([k,t])=>`<dt>${k.split(' ').map(x=>`<kbd>${x}</kbd>`).join('')}</dt><dd>${t}</dd>`).join('')}</dl></div>`;h.hidden=false}
function hilfeZu(){$('#hilfe').hidden=true}
async function updateLaden(){let u;try{u=await api('/api/update')}catch(e){return}window.updInfo=u;
 $('#upd').innerHTML=u.info&&u.info.neu?`<div class="update"><span><b>Update verfügbar</b> <span class="klein">Version ${esc(u.info.kurz)} · Clips und Einstellungen bleiben erhalten</span></span><button class="btn pri" onclick="updateStart()">Jetzt aktualisieren</button></div>`:(u.fehler?`<div class="warn">${esc(u.fehler)}</div>`:'');
 const v=u.info&&u.info.installiert;
 $('#fuss').innerHTML=`<span>Version ${v?'<span class="mono">'+esc(v)+'</span>':'unbekannt'}${u.info&&!u.info.neu?' · aktuell':''}</span><a href="#" id="updlink" onclick="updatePruefen();return false">Auf Update prüfen</a>`}
async function updatePruefen(){const l=$('#updlink');if(l)l.textContent='Prüfe …';const r=await api('/api/update/pruefen',{});
 if(r.fehler){alert(r.fehler)}else{const u=await api('/api/update');if(u.info&&!u.info.neu)toast('Du hast die neueste Version')}await updateLaden()}
function uploadPanel(c){const u=ups[c.id];return `<div class="box" style="margin-top:8px"><b>Upload zu YouTube</b><div class="hinweis">Nichts wird automatisch hochgeladen. Vorschau siehst du links im Video. Titel und Beschreibung prüfen, dann bewusst bestätigen.</div>
<input id="ut_${c.id}" value="${esc(u.titel)}" maxlength="100" style="margin-bottom:6px"><textarea id="ub_${c.id}">${esc(u.beschreibung)}</textarea>
<select id="us_${c.id}" style="margin:6px 0"><option value="private">privat (Standard)</option><option value="unlisted">nicht gelistet</option><option value="public">öffentlich</option></select>
<div class="zeile"><button class="btn pri" onclick="uploadGo('${c.id}')">Ja, jetzt hochladen</button><button class="btn" onclick="delete ups['${c.id}'];teil()">Abbrechen</button></div></div>`}
async function uploadOeffnen(id){const r=await api('/api/caption?id='+encodeURIComponent(id));if(r.fehler)return alert(r.fehler);ups[id]={titel:r.titel,beschreibung:r.beschreibung};teil()}
async function uploadGo(id){const sv=$('[id="us_'+id+'"]').value;if(sv!='private'&&!confirm('Sichtbarkeit „'+sv+'“ wirklich? Standard ist privat.'))return;
 const r=await api('/api/upload',{id,bestaetigt:true,sichtbarkeit:sv,titel:$('[id="ut_'+id+'"]').value,beschreibung:$('[id="ub_'+id+'"]').value});
 if(r.fehler)return alert(r.fehler);delete ups[id];alert('Hochgeladen ('+sv+'). Link wurde in die Liste eingetragen.');load({voll:true})}
async function updateStart(){const u=window.updInfo||{};
 if(!confirm((u.aufnahme_aktiv?'ACHTUNG: Es läuft gerade eine Aufnahme, ein laufender Clip kann verloren gehen.\\n\\n':'')+'Update installieren? Clips, Einstellungen und Token bleiben erhalten. Die App startet kurz neu.'))return;
 const r=await api('/api/update/installieren',{});if(r.fehler)return alert(r.fehler);
 $('#upd').innerHTML='<div class="hinweis">Update eingespielt ('+r.geaendert.length+' Dateien). App startet neu …'+(u.neustart_automatisch?'':' Falls die Seite nicht zurückkommt: starten.bat einfach neu öffnen.')+'</div>';
 for(let i=0;i<40;i++){await new Promise(x=>setTimeout(x,1500));try{await fetch('/api/status-watcher');if(i>2){location.reload();return}}catch(e){}}}
async function kontoLogin(){const r=await api('/api/konto/login',{});if(r.fehler)return alert(r.fehler);window.open(r.url,'_blank')}
async function kontoTrennen(){if(!confirm('Token löschen und Verbindung trennen?'))return;await api('/api/konto/trennen',{});load();render()}
async function uploadSchalter(a){await api('/api/konto/upload',{aktiv:a});load();render()}
async function durchNeu(){const r=await api('/api/durchsehrate/aktualisieren',{});if(r.fehler)alert(r.fehler);render()}
function auswHtml(a){const t=(titel,l)=>`<div style="margin-top:8px"><b>${titel}</b>`+(l.length?`<table><tr><th>Gruppe</th><th class="z">Clips</th><th class="z">Median Durchgesehen</th></tr>`+l.map(x=>`<tr><td>${esc(x.gruppe)}</td><td class="z">${x.clips}</td><td class="z">${x.median==null?'<span class="klein">zu wenig Daten (min. '+a.min+')</span>':x.median+' %'}</td></tr>`).join('')+'</table>':'<div class="klein">Noch keine Daten.</div>')+'</div>';
 return `<div class="box"><b>Auswertung Durchsehrate</b> <span class="klein">(Median, erst ab ${a.min} Clips pro Gruppe)</span>`+t('Nach Hook-Typ',a.hook)+t('Nach Clip-Länge',a.laenge)+'</div>'}
const zahl=n=>n==null?'–':Number(n).toLocaleString('de-DE');
function keyBox(r){return r.schluessel?'':`<div class="box"><b>YouTube-API-Schlüssel fehlt</b><div class="hinweis">So bekommst du ihn kostenlos: 1) console.cloud.google.com öffnen, mit Google-Konto anmelden. 2) Oben ein neues Projekt anlegen. 3) „APIs &amp; Dienste“ → „Bibliothek“ → „YouTube Data API v3“ → Aktivieren. 4) „Anmeldedaten“ → „Anmeldedaten erstellen“ → „API-Schlüssel“. 5) Schlüssel hier eintragen (oder in config.json bei <code>youtube_api_key</code>). Das Tageslimit von 10.000 Punkten ist kostenlos.</div><input id="ak" placeholder="API-Schlüssel" type="password" autocomplete="off" style="margin-bottom:8px"><button class="btn pri" onclick="schluessel()">Schlüssel speichern</button></div>`}
function trendHtml(r){let h=keyBox(r);if(r.fehler)h+=`<div class="warn">${esc(r.fehler)}</div>`;
 const d=r.daten;
 if(!d){return h+(r.schluessel?'<div class="leer">Noch kein Abruf. Der erste Abruf läuft in wenigen Minuten, oder gleich starten:</div><div style="text-align:center"><button class="btn pri" onclick="trendNeu()">Jetzt abrufen</button></div>':'')}
 h+=`<div class="box"><b>Erkannte Muster</b> <span class="klein">(nur wenn mindestens 3 Videos verschiedener Kanäle sie zeigen, aus ${d.analysiert} Videos)</span>`+(d.muster.length?'<ul>'+d.muster.map(x=>`<li>${esc(x.text)}</li>`).join('')+'</ul>':'<div class="klein">Noch keine Muster erkennbar.</div>')+`<div class="klein">Ziel: Muster lernen, nicht fremde Videos kopieren.</div></div>`;
 h+=`<div class="box"><b>Top 20 nach Aufrufen pro Stunde</b> <span class="klein">Stand ${esc(d.abgerufen.replace('T',' '))} · Suchen heute: ${r.suchen}/${r.max_suchen}</span> <button class="btn" onclick="trendNeu()">Jetzt abrufen</button><div style="overflow:auto"><table><tr><th>#</th><th>Titel</th><th>Kanal</th><th class="z">Aufrufe</th><th class="z">Aufr./Std.</th><th>Link</th></tr>`+d.videos.map((v,i)=>`<tr><td>${i+1}</td><td>${esc(v.titel)}</td><td>${esc(v.kanal)}</td><td class="z">${zahl(v.aufrufe)}</td><td class="z">${zahl(v.pro_stunde)}</td><td><a href="${esc(v.link)}" target="_blank" rel="noopener">ansehen</a></td></tr>`).join('')+'</table></div></div>';return h}
async function schluessel(){const r=await api('/api/schluessel',{schluessel:$('#ak').value});if(r.fehler)alert(r.fehler);load()}
async function trendNeu(){const r=await api('/api/trend/aktualisieren',{});if(r.fehler)alert(r.fehler);render()}
async function trackerNeu(){const r=await api('/api/tracker/aktualisieren',{});if(r.fehler)alert(r.fehler);render()}
async function kanal(){const r=await api('/api/kanal',{name:$('#kn').value,rechte:$('#kr').value,nachweis:$('#kv').value});if(r.fehler)alert(r.fehler);load()}
async function loesche(n){if(!confirm(n+' entfernen?'))return;await api('/api/kanal/loeschen',{name:n});load()}
load({voll:true});updateLaden();setInterval(load,8000);setInterval(updateLaden,600000);
</script></body></html>"""


# ------------------------------------------------------------ HTTP

class H(BaseHTTPRequestHandler):
    server_version = "clipwatcher"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            b = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif p == "/api/clips":
            self._json({"clips": clip_liste()})
        elif p == "/api/config":
            self._json(config_oeffentlich())
        elif p == "/api/status-watcher":
            cfg = lade_config()
            frei = wartung.frei_gb()
            mind = float(cfg.get("min_frei_gb", 10))
            aufn = BASIS / "aufnahmen"
            def frisch(p):  # Ordner, in den in den letzten 90 s ein Segment geschrieben wurde
                try:
                    return time.time() - max(p.stat().st_mtime, (p / "segmente.csv").stat().st_mtime) < 90
                except OSError:
                    return time.time() - p.stat().st_mtime < 90
            live = sorted({p.name.rsplit("_", 2)[0] for p in aufn.glob("*") if p.is_dir() and frisch(p)}) if aufn.is_dir() else []
            self._json({"laeuft": watcher_laeuft(), "aufnahme": live if watcher_laeuft() else [], "hinweis": WATCHER["hinweis"], "frei_gb": round(frei, 1),
                        "min_frei_gb": mind, "platz_warnung": frei < mind, "fehler": dict(FEHLER)})
        elif p == "/api/wartung":
            cfg = lade_config()
            self._json(dict(wartung.letzter_lauf(), regeln=wartung.einstellungen(cfg), frei_gb=round(wartung.frei_gb(), 1)))
        elif p == "/api/trend":
            cfg = lade_config()
            self._json({"schluessel": bool(trend.schluessel(cfg)), "daten": trend.lade(), "fehler": FEHLER.get("trend", ""),
                        "suchen": trend.suchen_heute(), "max_suchen": trend.MAX_SUCHEN_TAG})
        elif p == "/api/tracker":
            self._json({"schluessel": bool(trend.schluessel(lade_config())), "daten": tracker.uebersicht(),
                        "fehler": FEHLER.get("tracker", "")})
        elif p == "/api/update":
            aktiv = any(time.time() - (d.stat().st_mtime) < 120 for d in (BASIS / "aufnahmen").glob("*")) if (BASIS / "aufnahmen").is_dir() else False
            self._json({"info": UPDATE["info"], "fehler": FEHLER.get("update", ""), "aufnahme_aktiv": aktiv,
                        "neustart_automatisch": os.environ.get("CLIPWATCHER_LOOP") == "1"})
        elif p == "/api/konto":
            self._json(dict(konto.status(), upload_aktiv=bool(lade_config().get("upload_aktiv", False))))
        elif p == "/oauth/callback":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_error(403)
            try:
                konto.fertig_login(parse_qs(urlparse(self.path).query))
                msg = "Verbunden. Du kannst dieses Fenster schließen."
            except trend.ApiFehler as e:
                msg = str(e)
            b = f"<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;padding:30px'><h2>clip-watcher</h2><p>{msg}</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif p == "/api/hookvorschlag":
            try:
                ordner = sicher(parse_qs(urlparse(self.path).query).get("id", [""])[0])
                self._json({"vorschlaege": caption.hook_vorschlaege(ordner)})
            except (ValueError, OSError) as e:
                self._json({"fehler": str(e) or "Fehler"}, 400)
        elif p == "/api/caption":
            try:
                q = parse_qs(urlparse(self.path).query)
                ordner = sicher(q.get("id", [""])[0])
                if not (ordner / "info.txt").is_file():
                    raise ValueError("Clip nicht gefunden")
                self._json(caption.baue(ordner, lade_config(), q.get("hook", [""])[0]))
            except (ValueError, OSError) as e:
                self._json({"fehler": str(e) or "Fehler"}, 400)
        elif p == "/api/log":
            txt = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-80:] if LOG.exists() else []
            self._json({"log": "\n".join(txt)})
        elif p.startswith("/files/"):
            self._datei(unquote(p[len("/files/"):]))
        else:
            self.send_error(404)

    def _datei(self, rel):
        try:
            f = sicher(rel)
            if not f.is_file():
                raise ValueError
        except ValueError:
            return self.send_error(404)
        size = f.stat().st_size
        start, end, code = 0, size - 1, 200
        r = self.headers.get("Range")
        if r:
            m = re.match(r"bytes=(\d*)-(\d*)", r)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                code = 206
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                rest = end - start + 1
                while rest > 0:
                    blk = fh.read(min(65536, rest))
                    if not blk:
                        break
                    self.wfile.write(blk)
                    rest -= len(blk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"fehler": "Ungültige Daten"}, 400)
        p = urlparse(self.path).path
        try:
            if p == "/api/status":
                ordner = sicher(d["id"])
                if d["status"] not in ("neu", "gut", "weg") or not ordner.is_dir():
                    raise ValueError("Ungültig")
                (ordner / "status.txt").write_text(d["status"], encoding="utf-8")
                return self._json({"ok": True})
            if p == "/api/link":
                if not sicher(d["id"]).is_dir():
                    raise ValueError("Clip nicht gefunden")
                tracker.setze_link(d["id"], d.get("link", ""))
                return self._json({"ok": True})
            if p == "/api/update/pruefen":
                UPDATE["zeit"] = time.time()
                cfg = lade_config()
                job("update", lambda: UPDATE.update(info=aktualisieren.pruefen(cfg)))
                return self._json({"fehler": FEHLER.get("update", "")} if FEHLER.get("update") else {"ok": True})
            if p == "/api/update/installieren":
                cfg = lade_config()
                try:
                    geaendert = aktualisieren.installieren(cfg)
                except aktualisieren.UpdateFehler as e:
                    return self._json({"fehler": str(e)})
                UPDATE["info"] = None
                NEUSTART["an"] = True
                threading.Timer(1.0, lambda: SERVER["srv"].shutdown()).start()
                return self._json({"ok": True, "geaendert": geaendert})
            if p == "/api/einstellung":
                cfg = lade_config()
                if "clip_laenge_s" in d:
                    laenge = int(d["clip_laenge_s"])
                    if not 15 <= laenge <= 20:
                        raise ValueError("Clip-Länge: 15 bis 20 Sekunden.")
                    cfg["clip_laenge_s"] = laenge
                if "auswahl" in d:
                    if d["auswahl"] not in ("locker", "normal", "streng", "highlights"):
                        raise ValueError("Unbekannte Auswahl.")
                    cfg["auswahl"] = d["auswahl"]
                speichere_config(cfg)   # der Watcher liest die Datei von selbst neu, kein Neustart nötig
                return self._json({"ok": True})
            if p == "/api/feld":
                if d.get("spalte") != "Hook-Typ" or not sicher(d["id"]).is_dir():
                    raise ValueError("Ungültig")
                tracker.setze_feld(d["id"], "Hook-Typ", d.get("wert", ""))
                return self._json({"ok": True})
            if p == "/api/konto/login":
                return self._json({"url": konto.start_login(PORT["port"], bool(lade_config().get("upload_aktiv", False)))})
            if p == "/api/konto/trennen":
                konto.trennen()
                return self._json({"ok": True})
            if p == "/api/konto/upload":
                cfg = lade_config()
                cfg["upload_aktiv"] = bool(d.get("aktiv"))
                speichere_config(cfg)
                return self._json({"ok": True})
            if p == "/api/durchsehrate/aktualisieren":
                job("analytics", tracker.aktualisiere_durchsehrate)
                return self._json({"fehler": FEHLER["analytics"]} if FEHLER.get("analytics") else {"ok": True})
            if p == "/api/upload":
                # Nur auf ausdrücklichen Knopfdruck, nur wenn aktiviert, nur für Clips mit Status "gut".
                if d.get("bestaetigt") is not True:
                    raise ValueError("Upload nicht bestätigt.")
                if not lade_config().get("upload_aktiv", False):
                    raise ValueError("Upload ist ausgeschaltet.")
                ordner = sicher(d["id"])
                st = (ordner / "status.txt").read_text(encoding="utf-8").strip() if (ordner / "status.txt").exists() else "neu"
                if st != "gut":
                    raise ValueError("Nur Clips mit Status „Gut“ können hochgeladen werden.")
                video = ordner / ("short_hook.mp4" if (ordner / "short_hook.mp4").exists() else "short.mp4")
                vid = konto.upload(video, str(d.get("titel", "")).strip() or "GTA 6", str(d.get("beschreibung", "")),
                                   d.get("sichtbarkeit", "private"))
                tracker.setze_link(d["id"], f"https://www.youtube.com/shorts/{vid}")
                return self._json({"ok": True, "id": vid})
            if p == "/api/schluessel":
                key = str(d.get("schluessel", "")).strip()
                if not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", key):
                    return self._json({"fehler": "Das sieht nicht nach einem API-Schlüssel aus."})
                cfg = lade_config()
                cfg["youtube_api_key"] = key
                speichere_config(cfg)
                return self._json({"ok": True})
            if p in ("/api/trend/aktualisieren", "/api/tracker/aktualisieren", "/api/aufraeumen"):
                cfg = lade_config()
                fn = {"/api/trend/aktualisieren": lambda: trend.abrufen(cfg),
                      "/api/tracker/aktualisieren": lambda: tracker.aktualisiere(cfg),
                      "/api/aufraeumen": lambda: wartung.aufraeumen(cfg)}[p]
                name = p.split("/")[2]
                job(name, fn)
                return self._json({"fehler": FEHLER.get(name, "")} if FEHLER.get(name) else {"ok": True})
            if p == "/api/hook":
                ordner = sicher(d["id"])
                r = subprocess.run([sys.executable, str(BASIS / "hook.py"), str(ordner), str(d["text"])[:100]],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASIS)
                if r.returncode != 0:
                    return self._json({"fehler": (r.stderr or r.stdout)[-300:]})
                info = ordner / "info.txt"
                zeilen = [z for z in info.read_text(encoding="utf-8").splitlines() if not z.startswith("Hook:")]
                info.write_text("\n".join(zeilen) + f"\nHook: {d['text']}\n", encoding="utf-8")
                return self._json({"ok": True})
            if p == "/api/kanal":
                name = re.sub(r"[^\w]", "", str(d.get("name", "")).strip().lower().replace("twitch.tv/", ""))
                rechte, nachweis = str(d.get("rechte", "")).lower(), str(d.get("nachweis", "")).strip()
                if not name:
                    return self._json({"fehler": "Kanalname fehlt."})
                if rechte not in ERLAUBT:
                    return self._json({"fehler": "Rechte: eigen, kampagne oder erlaubnis."})
                if rechte != "eigen" and not nachweis:
                    return self._json({"fehler": "Bei kampagne/erlaubnis braucht es einen Link oder eine Notiz als Nachweis."})
                cfg = lade_config()
                cfg["kanaele"] = [k for k in cfg["kanaele"] if k["name"].lower() != name and not k["name"].startswith("KANAL")]
                cfg["kanaele"].append({"name": name, "rechte": rechte, "nachweis": nachweis})
                speichere_config(cfg)
                watcher_start()
                return self._json({"ok": True})
            if p == "/api/kanal/loeschen":
                cfg = lade_config()
                cfg["kanaele"] = [k for k in cfg["kanaele"] if k["name"].lower() != str(d.get("name", "")).lower()]
                speichere_config(cfg)
                watcher_start()
                return self._json({"ok": True})
            if p == "/api/watcher":
                if d.get("aktion") == "stop":
                    with LOCK:
                        watcher_stop_unlocked()
                        WATCHER["hinweis"] = ""
                else:
                    watcher_start()
                return self._json({"ok": True})
        except (KeyError, ValueError, trend.ApiFehler) as e:
            return self._json({"fehler": str(e) or "Fehler"}, 400)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="auch vom Handy im selben WLAN erreichbar")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--kein-watcher", action="store_true")
    ap.add_argument("--neustart", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    CLIPS.mkdir(exist_ok=True)
    PORT["port"] = a.port
    if not a.kein_watcher:
        watcher_start()
    threading.Thread(target=watcher_ueberwachen, daemon=True).start()
    threading.Thread(target=hintergrund, daemon=True).start()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), H)
    SERVER["srv"] = srv
    print(f"clip-watcher läuft: http://localhost:{a.port}" + ("  (im WLAN auch über die IP deines PCs)" if a.lan else ""))
    if not a.lan and not a.neustart:
        try:
            import webbrowser
            threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with LOCK:
            watcher_stop_unlocked()
        srv.server_close()
    if NEUSTART["an"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
