#!/usr/bin/env python3
"""
clip-watcher App: startet den Watcher und eine Oberfläche im Browser.
  python app.py            -> http://localhost:8765  (nur auf diesem Rechner)
  python app.py --lan      -> auch vom Handy im selben WLAN erreichbar
  python app.py --kein-watcher   (nur die Oberfläche, z. B. zum Ansehen alter Clips)
"""
import argparse, json, mimetypes, os, re, subprocess, sys, threading, time
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
    for info in sorted(CLIPS.glob("*/*/info.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
        d = info.parent
        if not (d / "short.mp4").exists():
            continue
        felder = {}
        for z in info.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(Quelle|Rechte|Aufgenommen|Auslöser|Chat-Wörter|Gesagt|Hook):\s*(.*)$", z)
            if m:
                felder[m.group(1)] = m.group(2).strip()
        rel = d.relative_to(CLIPS).as_posix()
        st = (d / "status.txt").read_text(encoding="utf-8").strip() if (d / "status.txt").exists() else "neu"
        ut = "fertig" if (d / "untertitel.fertig").exists() else ("keine" if (d / "untertitel.keine").exists() else "laeuft")
        video = "short_hook.mp4" if (d / "short_hook.mp4").exists() else "short.mp4"
        out.append({"id": rel, "status": st, "video": f"/files/{rel}/{video}", "ohne_hook": f"/files/{rel}/short.mp4", "ut": ut,
                    "link": links.get(f"clips/{rel}", ""),
                    "vorschau": f"/files/{rel}/vorschau.jpg" if (d / "vorschau.jpg").exists() else "", "hook_typ": hooktypen.get(f"clips/{rel}", ""),
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
        time.sleep(300 if trend.schluessel(lade_config()) else 600)


# ------------------------------------------------------------ Oberfläche

HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>clip-watcher</title>
<style>
*{box-sizing:border-box}
:root{--bg:#0b0c10;--p1:#121419;--p2:#181b22;--p3:#1f232c;--line:rgba(255,255,255,.07);--line2:rgba(255,255,255,.13);--txt:#eceef3;--mut:#8b909d;--dim:#5d6270;--acc:#ff5c8a;--acc2:#ff7aa0;--ok:#3ddc97;--bad:#ff6b6b;--warn:#ffb547}
html{color-scheme:dark}
body{margin:0;height:100vh;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);color:var(--txt);font:14px/1.5 Inter,"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
button,input,select,textarea{font:inherit;color:inherit}
.top{flex:none;position:relative;z-index:20;height:58px;display:flex;align-items:center;gap:22px;padding:0 20px;background:rgba(11,12,16,.86);backdrop-filter:saturate(1.4) blur(12px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:650;font-size:15px;letter-spacing:-.01em;white-space:nowrap}
.logo{width:28px;height:28px;border-radius:8px;background:var(--acc);display:grid;place-items:center;color:#fff}
#tabs{display:flex;align-items:center;gap:2px;flex:1;overflow-x:auto;scrollbar-width:none;height:100%}
#tabs button{position:relative;height:100%;background:none;border:0;padding:0 12px;color:var(--mut);cursor:pointer;font-weight:500;white-space:nowrap;display:flex;align-items:center;gap:7px}
#tabs button:hover{color:var(--txt)}
#tabs button.akt{color:var(--txt)}
#tabs button.akt::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;height:2px;border-radius:2px;background:var(--acc)}
.zahl{font-size:11.5px;font-variant-numeric:tabular-nums;min-width:20px;padding:1px 6px;border-radius:999px;background:var(--p3);color:var(--mut);text-align:center}
#tabs button.akt .zahl{background:rgba(255,92,138,.16);color:var(--acc2)}
.trenn{width:1px;height:18px;background:var(--line2);margin:0 8px;flex:none}
#stat{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--mut);white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad)}
.dot.an{background:var(--ok);animation:puls 2.2s infinite}
@keyframes puls{0%{box-shadow:0 0 0 0 rgba(61,220,151,.45)}70%{box-shadow:0 0 0 7px rgba(61,220,151,0)}100%{box-shadow:0 0 0 0 rgba(61,220,151,0)}}
.band{flex:none;width:100%;max-width:1480px;margin:0 auto;padding:0 20px}
.band:empty{display:none}
main{flex:1;min-height:0;overflow-y:auto;width:100%;max-width:1480px;margin:0 auto;padding:18px 20px 24px}

.review{display:grid;grid-template-columns:290px minmax(0,1fr) 400px;gap:18px;height:100%;min-height:520px}
.liste{overflow-y:auto;display:flex;flex-direction:column;gap:4px;padding-right:4px}
.listekopf{font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);padding:2px 8px 8px}
.item{display:flex;gap:12px;align-items:stretch;text-align:left;background:transparent;border:1px solid transparent;border-radius:12px;padding:8px;cursor:pointer;transition:background .12s,border-color .12s}
.item:hover{background:var(--p1)}
.item.akt{background:var(--p2);border-color:var(--line2)}
.thumb{width:48px;height:85px;flex:none;border-radius:8px;overflow:hidden;background:var(--p3);display:grid;place-items:center;color:var(--dim)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.itxt{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1;padding-top:1px}
.izeile{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.izeile b{font-weight:600;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.zeit{color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums;flex:none}
.iquote{color:var(--mut);font-size:12.5px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.imeta{display:flex;gap:4px;flex-wrap:wrap;margin-top:auto}

.buehne{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:radial-gradient(70% 60% at 50% 40%,#1a1d27 0,#0e0f13 100%);border:1px solid var(--line);border-radius:20px;padding:18px;min-height:0;min-width:0}
.buehne video{flex:1 1 0;min-height:0;height:0;width:auto;max-width:100%;aspect-ratio:9/16;border-radius:14px;background:#000;box-shadow:0 24px 70px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.06)}
.tasten{font-size:12px;color:var(--dim);display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:center}
.tasten i{font-style:normal;margin-right:8px}
kbd{font:11px/1 ui-monospace,SFMono-Regular,Consolas,monospace;padding:3px 6px;border-radius:5px;background:var(--p3);border:1px solid var(--line2);color:var(--mut)}

.panel{overflow-y:auto;background:var(--p1);border:1px solid var(--line);border-radius:20px;padding:20px}
.pkopf{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h2{font-size:20px;margin:0;letter-spacing:-.02em;font-weight:650}
.meta{color:var(--mut);font-size:12.5px;margin-top:2px}
.tags{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;padding-top:3px}
blockquote{margin:16px 0 0;padding:12px 14px;background:var(--p2);border-radius:12px;font-size:14px;line-height:1.5;color:#d6d8de;border-left:2px solid var(--acc)}
.chatzeile{margin-top:10px}
.pill{font-size:11.5px;font-weight:500;padding:3px 9px;border-radius:999px;background:var(--p3);color:var(--mut);white-space:nowrap}
.pill.ok{background:rgba(61,220,151,.12);color:var(--ok)}
.pill.gelb{background:rgba(255,181,71,.12);color:var(--warn)}
.pill.acc{background:rgba(255,92,138,.14);color:var(--acc2)}
.entscheid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:18px 0}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;background:var(--p3);border:1px solid var(--line2);border-radius:10px;padding:9px 14px;cursor:pointer;text-decoration:none;color:var(--txt);font-weight:500;transition:background .12s,border-color .12s,transform .06s;white-space:nowrap}
.btn:hover{background:#262a35;border-color:rgba(255,255,255,.2);text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.55;cursor:default}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.pri:hover{background:var(--acc2);border-color:var(--acc2)}
.btn.ghost{background:transparent}
.btn.gut{color:var(--ok)}
.btn.weg{color:var(--bad)}
.btn.gross{padding:13px;font-size:15px;font-weight:600;border-radius:12px}
.btn.gut.gross{background:rgba(61,220,151,.09);border-color:rgba(61,220,151,.28)}
.btn.gut.gross:hover{background:rgba(61,220,151,.17)}
.btn.weg.gross{background:rgba(255,107,107,.07);border-color:rgba(255,107,107,.26)}
.btn.weg.gross:hover{background:rgba(255,107,107,.14)}
.btn.gross kbd{margin-left:4px;background:rgba(0,0,0,.28);border-color:transparent;color:inherit;opacity:.75}
.btn.an{box-shadow:inset 0 0 0 1.5px currentColor}

.seg{display:flex;padding:3px;background:var(--bg);border:1px solid var(--line);border-radius:11px;gap:2px}
.seg button{flex:1;padding:8px;background:none;border:0;border-radius:8px;color:var(--mut);cursor:pointer;font-weight:500;transition:.12s}
.seg button:hover{color:var(--txt)}
.seg button.akt{background:var(--p3);color:var(--txt);box-shadow:0 1px 2px rgba(0,0,0,.4)}
.lbl{display:flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:20px 0 8px}
.lbl span{text-transform:none;letter-spacing:0;font-weight:400;color:var(--dim)}
.feld{display:flex;gap:8px}
.feld input{flex:1;min-width:0}
input,select,textarea{background:var(--bg);border:1px solid var(--line2);border-radius:10px;padding:10px 12px;width:100%;transition:border-color .12s,box-shadow .12s}
input::placeholder,textarea::placeholder{color:var(--dim)}
input[type=checkbox]{width:16px;height:16px;padding:0;margin:0 8px 0 0;accent-color:var(--acc);vertical-align:-3px;box-shadow:none}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(255,92,138,.18)}
textarea{font:12.5px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;resize:vertical;min-height:120px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--p2);border:1px solid var(--line2);border-radius:999px;padding:6px 12px;cursor:pointer;font-size:13px;color:#d6d8de;transition:.12s;text-align:left}
.chip:hover{border-color:var(--acc);color:#fff}
.chip.an{background:rgba(255,92,138,.15);border-color:var(--acc);color:#fff}
.chip.mini{padding:2px 8px;font-size:11.5px;cursor:default;color:var(--mut)}
.chip.mini:hover{border-color:var(--line2);color:var(--mut)}
.checks{display:flex;flex-direction:column;gap:2px}
.check{display:flex;gap:10px;align-items:center;padding:7px 10px;border-radius:9px;cursor:pointer;font-size:13.5px}
.check:hover{background:var(--p2)}
.check input{width:16px;height:16px;accent-color:var(--acc);margin:0;flex:none;padding:0}
.check input:checked+span{color:var(--dim);text-decoration:line-through}
.capkopf{display:flex;justify-content:space-between;align-items:center;margin:18px 0 8px}
.rot{color:var(--bad)}
.leise{color:var(--dim);font-size:12.5px;margin-top:8px}

.seite{max-width:1100px;margin:0 auto}
.box{background:var(--p1);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:14px}
.box>b:first-child{display:block;font-size:15px;font-weight:600;margin-bottom:6px}
.zeile{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.zeile input{flex:1;min-width:180px}
.klein{color:var(--mut);font-size:12.5px}
.tag{font-size:11.5px;padding:2px 8px;border-radius:999px;background:var(--p3);color:var(--mut)}
.tag.gut{background:rgba(61,220,151,.12);color:var(--ok)}
.hinweis{background:rgba(255,181,71,.07);border:1px solid rgba(255,181,71,.22);color:#f1dcb6;border-radius:12px;padding:10px 14px;font-size:13px;margin:12px 0}
.warn{background:rgba(255,107,107,.08);border:1px solid rgba(255,107,107,.25);color:#ffd0d0;border-radius:12px;padding:10px 14px;font-size:13px;margin:12px 0}
.band .hinweis,.band .warn{margin:12px 0 0}
.update{display:flex;align-items:center;justify-content:space-between;gap:12px;background:rgba(255,92,138,.08);border:1px solid rgba(255,92,138,.25);border-radius:12px;padding:8px 8px 8px 14px;margin-top:12px;font-size:13px}
.updlink{display:block;text-align:right;font-size:12px;color:var(--dim);margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
td.z,th.z{text-align:right}
tr:last-child td{border-bottom:0}
tr:hover td{background:rgba(255,255,255,.015)}
a{color:var(--acc2);text-decoration:none}
a:hover{text-decoration:underline}
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.stat div{background:var(--p2);border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:var(--mut);font-size:12.5px}
.stat b{display:block;font-size:22px;font-weight:650;color:var(--txt);letter-spacing:-.02em;margin-bottom:2px}
pre{background:var(--p1);border:1px solid var(--line);border-radius:16px;padding:16px 18px;font:12.5px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;overflow:auto;white-space:pre-wrap;color:#c9ccd4;margin:0}
code{font-family:ui-monospace,Consolas,monospace;background:var(--p3);padding:1px 5px;border-radius:5px;font-size:.92em}
ul{margin:8px 0;padding-left:20px}
li{margin:4px 0}
.laengen{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.leer{display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;padding:100px 20px;color:var(--mut)}
.leer svg{color:var(--dim);margin-bottom:6px}
.leer b{color:var(--txt);font-size:16px;font-weight:600}
.leer span{max-width:400px;font-size:13.5px}
#neuBtn{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:30}
#toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,16px);opacity:0;background:var(--txt);color:var(--bg);padding:10px 16px;border-radius:10px;font-weight:600;font-size:13.5px;transition:.2s;pointer-events:none;z-index:40}
#toast.an{opacity:1;transform:translate(-50%,0)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--p3);border-radius:10px;border:3px solid var(--bg)}
::-webkit-scrollbar-track{background:transparent}
@media(max-width:1200px){.review{grid-template-columns:240px minmax(0,1fr) 360px}}
@media(max-width:980px){.review{grid-template-columns:1fr;height:auto;min-height:0}.liste{flex-direction:row;overflow-x:auto;padding:0 0 6px}.listekopf{display:none}.item{flex:none;width:250px}.buehne video{flex:none;height:min(70vh,640px)}.tasten{display:none}}
@media(max-width:640px){body{height:auto;min-height:100vh;overflow:visible}main{overflow:visible}.top{position:sticky;top:0;flex-wrap:wrap;height:auto;padding:10px 14px 0;gap:8px}.top #tabs{order:3;flex-basis:100%;height:44px}#stat{margin-left:auto}main,.band{padding-left:14px;padding-right:14px}.item{width:220px}.panel{padding:16px}}
</style></head><body>
<header class="top"><div class="brand"><span class="logo"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l10-6.5z"/></svg></span>clip-watcher</div><nav id="tabs"></nav><div id="stat"></div></header>
<div id="banner" class="band"></div><div id="upd" class="band"></div>
<main id="main"></main><div id="neuBtn"></div><div id="toast"></div>
<script>
let tab='neu',clips=[],cfg=null,caps={},sig='',hookE={},hv={},ups={},kontoSt={},sel=null,pt={},check={},hookV={},autoplay=false,busy={};
const $=s=>document.querySelector(s);
const api=(u,b)=>fetch(u,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{}).then(r=>r.json());
const sv=(d,w)=>'<svg viewBox="0 0 24 24" width="'+(w||18)+'" height="'+(w||18)+'" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+d+'</svg>';
const P={check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>',x:'<path d="M6 6l12 12M18 6L6 18"/>',down:'<path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19.5h14"/>',
 spark:'<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
 film:'<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',play:'<path d="M8 5.5v13l10-6.5z" fill="currentColor"/>'};
const KLIPS=['neu','gut','weg'];
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function tippt(){const a=document.activeElement&&document.activeElement.tagName;return a=='INPUT'||a=='TEXTAREA'||a=='SELECT'}
function toast(t){const e=$('#toast');e.textContent=t;e.classList.add('an');clearTimeout(window.__tt);window.__tt=setTimeout(()=>e.classList.remove('an'),1800)}
function gesagt(c){const g=(c.gesagt||'').trim();return g.startsWith('(')?'':g}
function liste(){return clips.filter(c=>c.status==tab)}
function aktuell(){return clips.find(c=>c.id==sel)}

function tabs(){const n=k=>clips.filter(c=>c.status==k).length;
 const t=[['neu','Neu',n('neu')],['gut','Gut',n('gut')],['weg','Verworfen',n('weg')],null,['trends','Trends'],['tracker','Tracker'],['kanaele','Kanäle'],['log','Protokoll']];
 $('#tabs').innerHTML=t.map(x=>x?`<button class="${tab==x[0]?'akt':''}" onclick="geheZu('${x[0]}')">${x[1]}${x[2]!=null?`<span class="zahl">${x[2]}</span>`:''}</button>`:'<span class="trenn"></span>').join('')}
function geheZu(k){tab=k;sel=null;render()}

function render(){tabs();const m=$('#main');
 if(KLIPS.includes(tab)){m.innerHTML=reviewHtml(liste());nachRender()}
 else if(tab=='kanaele'&&cfg){m.innerHTML='<div class="seite">'+kanaeleHtml()+'</div>'}
 else if(tab=='trends'){api('/api/trend').then(r=>{m.innerHTML='<div class="seite">'+trendHtml(r)+'</div>'})}
 else if(tab=='tracker'){api('/api/tracker').then(r=>{m.innerHTML='<div class="seite">'+trackerHtml(r)+'</div>'})}
 else if(tab=='log'){api('/api/log').then(r=>{m.innerHTML='<div class="seite"><pre>'+esc(r.log||'(noch nichts)')+'</pre></div>';m.scrollTop=m.scrollHeight})}}
function nachRender(){const v=$('#vid');if(v&&autoplay){v.play().catch(()=>{})}autoplay=false}
function teil(){if(!KLIPS.includes(tab))return;const c=aktuell();const p=$('#panel'),l=$('#liste');if(!c||!p){render();return}
 const a=document.activeElement,id=a&&p.contains(a)&&a.id,wert=id&&a.value,pos=id&&a.selectionStart;
 p.innerHTML=panel(c);if(l)l.innerHTML=listeInnen(liste());
 if(id){const n=document.getElementById(id);if(n){n.focus();if(wert!=null)n.value=wert;try{n.setSelectionRange(pos,pos)}catch(e){}}}}

function reviewHtml(l){
 if(!l.length)return `<div class="leer">${sv(P.film,40)}<b>${tab=='neu'?'Noch keine neuen Clips':tab=='gut'?'Noch keine guten Clips':'Nichts verworfen'}</b><span>${tab=='neu'?'Sobald ein Kanal live ist und der Chat ausschlägt, landen die Clips hier.':'Sortiere Clips im Tab „Neu“ mit G (gut) oder W (weg).'}</span></div>`;
 if(!l.find(c=>c.id==sel))sel=l[0].id;const c=aktuell();
 return `<div class="review"><aside class="liste" id="liste">${listeInnen(l)}</aside>
 <section class="buehne"><video id="vid" src="${c.video}${hookV[c.id]?'?v='+hookV[c.id]:''}" controls playsinline loop preload="metadata"></video>
 <div class="tasten"><kbd>G</kbd><i>gut</i><kbd>W</kbd><i>weg</i><kbd>↑</kbd><kbd>↓</kbd><i>Clip wechseln</i><kbd>Leertaste</kbd><i>Play/Pause</i></div></section>
 <section class="panel" id="panel">${panel(c)}</section></div>`}
function listeInnen(l){return `<div class="listekopf">${l.length} ${l.length==1?'Clip':'Clips'}</div>`+l.map(item).join('')}
function kurzZeit(z){return (z||'').split(' ')[1]||z||''}
function item(c){const txt=c.hook||gesagt(c)||c.ausloeser||'–';
 return `<button class="item ${c.id==sel?'akt':''}" onclick="waehle('${c.id}')">
 <span class="thumb">${c.vorschau?`<img src="${c.vorschau}" loading="lazy" alt="">`:sv(P.play,18)}</span>
 <span class="itxt"><span class="izeile"><b>${esc(c.quelle)}</b><span class="zeit">${esc(kurzZeit(c.zeit))}</span></span>
 <span class="iquote">${esc(txt)}</span>
 <span class="imeta">${c.hook?'<span class="pill acc">Hook</span>':''}${c.ut=='laeuft'?'<span class="pill gelb">Untertitel …</span>':''}${c.link?'<span class="pill ok">online</span>':''}</span></span></button>`}

function panel(c){const p=pt[c.id]||'hook',kamp=(c.rechte||'').startsWith('kampagne');
 return `<div class="pkopf"><div><h2>${esc(c.quelle)}</h2><div class="meta">${esc(c.zeit)}${c.ausloeser?' · '+esc(c.ausloeser):''}</div></div>
 <div class="tags">${kamp?'<span class="pill acc">Kampagne</span>':`<span class="pill">${esc((c.rechte||'').split(' ')[0])}</span>`}${c.ut=='laeuft'?'<span class="pill gelb">Untertitel folgen</span>':c.ut=='fertig'?'<span class="pill ok">Untertitel</span>':''}</div></div>
 ${gesagt(c)?`<blockquote>„${esc(gesagt(c))}“</blockquote>`:''}
 ${c.chat&&c.chat!='-'?`<div class="chips chatzeile">${c.chat.split(',').map(w=>`<span class="chip mini">${esc(w.trim())}</span>`).join('')}</div>`:''}
 <div class="entscheid"><button class="btn gut gross ${c.status=='gut'?'an':''}" onclick="setze('${c.id}','gut')">${sv(P.check)}Gut<kbd>G</kbd></button><button class="btn weg gross ${c.status=='weg'?'an':''}" onclick="setze('${c.id}','weg')">${sv(P.x)}Weg<kbd>W</kbd></button></div>
 <div class="seg">${[['hook','Hook'],['caption','Caption'],['post','Posten']].map(([k,n])=>`<button class="${p==k?'akt':''}" onclick="pane('${c.id}','${k}')">${n}</button>`).join('')}</div>
 <div class="pane">${p=='hook'?paneHook(c):p=='caption'?paneCaption(c):panePost(c)}</div>`}
function pane(id,k){pt[id]=k;teil()}

const TYPEN=['Frage','Zahl','Schock','Reaktion','Zitat'];
function paneHook(c){const v=hv[c.id];if(v===undefined)ladeVorschlag(c.id);
 const wert=hookE[c.id]!=null?hookE[c.id]:c.hook;
 return `<label class="lbl">Hook-Text <span>max. 8 Wörter, oben in den ersten 2,5 s</span></label>
 <div class="feld"><input id="h_${c.id}" value="${esc(wert)}" placeholder="z. B. Das hat keiner kommen sehen" maxlength="80" oninput="hookE['${c.id}']=this.value" onkeydown="if(event.key=='Enter')hook('${c.id}')">
 <button class="btn pri" ${busy[c.id]?'disabled':''} onclick="hook('${c.id}')">${busy[c.id]?'Baue ein …':(c.hook?'Ändern':'Einbauen')}</button></div>
 <label class="lbl">${sv(P.spark,14)} Vorschläge <span>antippen, dann einbauen</span></label>
 <div class="chips">${v?v.map(t=>`<button class="chip ${wert==t?'an':''}" data-t="${esc(t)}" onclick="hookWahl('${c.id}',this.dataset.t)">${esc(t)}</button>`).join(''):'<span class="klein">Lädt …</span>'}</div>
 <label class="lbl">Hook-Typ <span>für die Auswertung im Tracker</span></label>
 <div class="chips">${TYPEN.map(t=>`<button class="chip ${c.hook_typ==t?'an':''}" onclick="typSetzen('${c.id}','${t}')">${t}</button>`).join('')}</div>`}
async function ladeVorschlag(id){if(hv[id]!==undefined)return;hv[id]=null;const r=await api('/api/hookvorschlag?id='+encodeURIComponent(id));hv[id]=r.vorschlaege||[];if(id==sel)teil()}
function hookWahl(id,t){hookE[id]=t;teil();const i=document.getElementById('h_'+id);if(i)i.focus()}
async function typSetzen(id,t){const c=clips.find(x=>x.id==id),neu=c&&c.hook_typ==t?'':t;
 const r=await api('/api/feld',{id,spalte:'Hook-Typ',wert:neu});if(r.fehler)return alert(r.fehler);toast(neu?'Hook-Typ: '+neu:'Hook-Typ entfernt');load({still:true})}
async function hook(id){const i=document.getElementById('h_'+id),t=(i?i.value:'').trim();if(!t)return alert('Bitte Hook-Text eingeben.');
 busy[id]=true;teil();const r=await api('/api/hook',{id,text:t});busy[id]=false;
 if(r.fehler){teil();return alert(r.fehler)}
 delete hookE[id];delete caps[id];hookV[id]=Date.now();toast('Hook eingebaut');load({voll:true})}

function paneCaption(c){const k=caps[c.id];if(k===undefined)ladeCaption(c.id);
 if(!k)return '<div class="leise">Caption wird erstellt …</div>';
 return `<div class="capkopf"><span class="klein">Titel: <b class="${k.laenge>100?'rot':''}">${k.laenge}</b> / 100 Zeichen</span><button class="btn pri" onclick="kopiere('${c.id}')">${sv(P.copy,16)}Kopieren</button></div>
 <textarea id="cp_${c.id}" readonly rows="11">${esc(k.block)}</textarea>
 ${k.kampagne?`<div class="hinweis"><b>Kampagne:</b> „Werbung“ und die Pflichtangaben sind drin. Nachweis: ${esc(k.hinweis)}</div>`:''}
 <div class="leise">Titel und Beschreibung nutzen deinen Hook. Nach einer Hook-Änderung wird die Caption neu erstellt.</div>`}
async function ladeCaption(id){if(caps[id]!==undefined)return;caps[id]=null;const r=await api('/api/caption?id='+encodeURIComponent(id));
 if(r.fehler){delete caps[id];return alert(r.fehler)}caps[id]=r;if(id==sel)teil()}
function kopiere(id){const t=document.getElementById('cp_'+id);if(!t)return;t.select();
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t.value).then(()=>toast('Caption kopiert'))}
 else{let ok=false;try{ok=document.execCommand('copy')}catch(e){}toast(ok?'Caption kopiert':'Bitte mit Strg+C kopieren')}}

const CHECKS=['Kein Spoiler, keine Zwischensequenz allein','Kein Leak, keine laute Musik im Hintergrund','Hook und Untertitel sind drin','Bei Kampagne: „Werbung“ sichtbar, Pflichtangaben drin'];
function panePost(c){const ch=check[c.id]||[];
 return `<label class="lbl">Vor dem Posten</label>
 <div class="checks">${CHECKS.map((t,i)=>`<label class="check"><input type="checkbox" ${ch[i]?'checked':''} onchange="hake('${c.id}',${i},this.checked)"><span>${t}</span></label>`).join('')}</div>
 <label class="lbl">Dateien</label>
 <div class="zeile" style="margin-top:0"><a class="btn" href="${c.video}" download>${sv(P.down,16)}Video laden</a>${c.srt?`<a class="btn ghost" href="${c.srt}" download>Untertitel .srt</a>`:''}${kontoSt.upload_aktiv&&kontoSt.upload_scope&&c.status=='gut'?`<button class="btn" onclick="uploadOeffnen('${c.id}')">Hochladen …</button>`:''}</div>
 ${ups[c.id]?uploadPanel(c):''}
 <label class="lbl">YouTube-Link <span>nach dem Hochladen eintragen</span></label>
 <div class="feld"><input id="l_${c.id}" value="${esc(c.link)}" placeholder="https://youtube.com/shorts/…" onkeydown="if(event.key=='Enter')link('${c.id}')"><button class="btn" onclick="link('${c.id}')">Speichern</button></div>`}
function hake(id,i,an){(check[id]=check[id]||[])[i]=an}
async function link(id){const r=await api('/api/link',{id,link:document.getElementById('l_'+id).value});if(r.fehler)return alert(r.fehler);toast('Link gespeichert');load({still:true})}

function waehle(id){if(id==sel)return;sel=id;autoplay=true;render()}
async function setze(id,s){const l=liste(),i=l.findIndex(c=>c.id==id),nach=l[i+1]||l[i-1];
 const r=await api('/api/status',{id,status:s});if(r.fehler)return alert(r.fehler);
 if(s!=tab){sel=nach?nach.id:null;autoplay=!!nach}toast(s=='gut'?'Als gut markiert':s=='weg'?'Verworfen':'Zurück in Neu');load({voll:true})}

function kanaeleHtml(){const L=cfg.clip_laenge_s||18;
 return `<div class="box"><b>Clip-Länge</b><div class="klein">Gilt für neue Clips. Der Höhepunkt liegt bei etwa zwei Dritteln, danach endet der Clip schnell.</div>
 <div class="laengen">${[15,16,17,18,19,20].map(s=>`<button class="chip ${L==s?'an':''}" onclick="laenge(${s})">${s} s</button>`).join('')}</div></div>
 <div class="box"><b>Überwachte Kanäle</b>`+cfg.kanaele.map(k=>`<div class="zeile"><div style="flex:1;min-width:0"><b>${esc(k.name)}</b> <span class="tag">${esc(k.rechte)}</span><div class="klein" style="overflow-wrap:anywhere">${esc(k.nachweis)}</div></div><button class="btn weg" onclick="loesche('${esc(k.name)}')">Entfernen</button></div>`).join('')+`</div>
 <div class="box"><b>Kanal hinzufügen</b><div class="hinweis">Fremde Kanäle nur mit Grundlage: Kampagne oder Erlaubnis des Streamers, dazu Link oder Notiz.</div>
 <div class="zeile"><input id="kn" placeholder="Kanalname (ohne twitch.tv/)"><select id="kr" style="width:auto"><option value="kampagne">kampagne</option><option value="erlaubnis">erlaubnis</option><option value="eigen">eigen</option></select></div>
 <div class="zeile"><input id="kv" placeholder="Nachweis: Link oder Notiz"><button class="btn pri" onclick="kanal()">Speichern und Watcher neu starten</button></div></div>
 <div class="box"><b>Watcher</b><div class="zeile"><button class="btn" onclick="api('/api/watcher',{aktion:'start'}).then(()=>load({voll:true}))">Watcher (neu) starten</button><button class="btn weg" onclick="api('/api/watcher',{aktion:'stop'}).then(()=>load({voll:true}))">Watcher stoppen</button></div></div>`}
async function laenge(s){if(!confirm('Clip-Länge auf '+s+' Sekunden stellen? Der Watcher startet dafür kurz neu, eine laufende Aufnahme beginnt von vorn.'))return;
 const r=await api('/api/einstellung',{clip_laenge_s:s});if(r.fehler)return alert(r.fehler);toast('Neue Clips: '+s+' Sekunden');load({voll:true})}

async function load(opt){opt=opt||{};const [c,cf,s,ko]=await Promise.all([api('/api/clips'),api('/api/config'),api('/api/status-watcher'),api('/api/konto')]);clips=c.clips;cfg=cf;kontoSt=ko;
 $('#stat').innerHTML=`<span class="dot ${s.laeuft?'an':''}"></span>${s.laeuft?'Watcher läuft':'Watcher aus'}`;
 $('#banner').innerHTML=(s.hinweis?`<div class="warn">${esc(s.hinweis)}</div>`:'')+(s.platz_warnung?`<div class="warn">Nur noch ${s.frei_gb} GB Festplatte frei (Warnung unter ${s.min_frei_gb} GB). Clips mit „Weg“ markieren oder alte Aufnahmen löschen.</div>`:'')+Object.entries(s.fehler||{}).map(([k,v])=>`<div class="warn">${esc(k)}: ${esc(v)}</div>`).join('');
 const neu=JSON.stringify([c.clips,cf,ko]);tabs();
 if(opt.still){sig=neu;teil();return}
 if(tab=='log'){if(!tippt())render();return}
 if(neu!==sig||opt.voll){if(!opt.voll&&(tippt()||videoBenutzt())){$('#neuBtn').innerHTML='<button class="btn pri" onclick="neuLaden()">Neue Clips – Anzeige aktualisieren</button>'}
  else{sig=neu;$('#neuBtn').innerHTML='';render()}}}
function videoBenutzt(){const v=$('#vid');return !!v&&!v.ended&&(!v.paused||v.currentTime>0.2)}
async function neuLaden(){$('#neuBtn').innerHTML='';await load({voll:true})}

document.addEventListener('keydown',e=>{if(tippt()||!KLIPS.includes(tab)||e.ctrlKey||e.metaKey||e.altKey)return;
 const l=liste(),i=l.findIndex(c=>c.id==sel),v=$('#vid'),k=e.key.toLowerCase();
 if(k=='g'&&sel)setze(sel,'gut');else if(k=='w'&&sel)setze(sel,'weg');
 else if(k=='arrowdown'||k=='j'){e.preventDefault();if(l[i+1])waehle(l[i+1].id)}
 else if(k=='arrowup'||k=='k'){e.preventDefault();if(i>0)waehle(l[i-1].id)}
 else if(k==' '&&v&&document.activeElement!==v){e.preventDefault();v.paused?v.play():v.pause()}});
async function updateLaden(){const u=await api('/api/update');window.updInfo=u;
 $('#upd').innerHTML=u.info&&u.info.neu?`<div class="update"><span><b>Update verfügbar</b> <span class="klein">Version ${esc(u.info.kurz)} · Clips und Einstellungen bleiben erhalten</span></span><button class="btn pri" onclick="updateStart()">Jetzt aktualisieren</button></div>`:(u.fehler?`<div class="warn">${esc(u.fehler)}</div>`:'')}
function uploadPanel(c){const u=ups[c.id];return `<div class="box" style="margin-top:8px"><b>Upload zu YouTube</b><div class="hinweis">Nichts wird automatisch hochgeladen. Vorschau siehst du links im Video. Titel und Beschreibung prüfen, dann bewusst bestätigen.</div>
<input id="ut_${c.id}" value="${esc(u.titel)}" maxlength="100" style="margin-bottom:6px"><textarea id="ub_${c.id}">${esc(u.beschreibung)}</textarea>
<select id="us_${c.id}" style="margin:6px 0"><option value="private">privat (Standard)</option><option value="unlisted">nicht gelistet</option><option value="public">öffentlich</option></select>
<div class="zeile"><button class="btn pri" onclick="uploadGo('${c.id}')">Ja, jetzt hochladen</button><button class="btn" onclick="delete ups['${c.id}'];teil()">Abbrechen</button></div></div>`}
async function uploadOeffnen(id){const r=await api('/api/caption?id='+encodeURIComponent(id));if(r.fehler)return alert(r.fehler);ups[id]={titel:r.titel,beschreibung:r.beschreibung};teil()}
async function uploadGo(id){const sv=$('[id="us_'+id+'"]').value;if(sv!='private'&&!confirm('Sichtbarkeit „'+sv+'“ wirklich? Standard ist privat.'))return;
 const r=await api('/api/upload',{id,bestaetigt:true,sichtbarkeit:sv,titel:$('[id="ut_'+id+'"]').value,beschreibung:$('[id="ub_'+id+'"]').value});
 if(r.fehler)return alert(r.fehler);delete ups[id];alert('Hochgeladen ('+sv+'). Link wurde in die Liste eingetragen.');load({voll:true})}
async function updatePruefen(){const r=await api('/api/update/pruefen',{});if(r.fehler)alert(r.fehler);else if(!(await api('/api/update')).info.neu)alert('Du hast die neueste Version.');updateLaden()}
async function updateStart(){const u=window.updInfo||{};
 if(!confirm((u.aufnahme_aktiv?'ACHTUNG: Es läuft gerade eine Aufnahme, ein laufender Clip kann verloren gehen.\\n\\n':'')+'Update installieren? Clips, Einstellungen und Token bleiben erhalten. Die App startet kurz neu.'))return;
 const r=await api('/api/update/installieren',{});if(r.fehler)return alert(r.fehler);
 $('#upd').innerHTML='<div class="hinweis">Update eingespielt ('+r.geaendert.length+' Dateien). App startet neu …'+(u.neustart_automatisch?'':' Falls die Seite nicht zurückkommt: starten.bat einfach neu öffnen.')+'</div>';
 for(let i=0;i<40;i++){await new Promise(x=>setTimeout(x,1500));try{await fetch('/api/status-watcher');if(i>2){location.reload();return}}catch(e){}}}
function kontoBox(){const k=kontoSt;let h='<div class="box"><b>YouTube-Konto (nur lesend)</b> ';
 if(!k.client_da)h+='<div class="hinweis">Zuerst <code>client_secret.json</code> aus der Google Cloud Console laden (Anmeldedaten → OAuth-Client-ID erstellen → Typ „Desktop-App“ → JSON herunterladen) und in den Programmordner legen. Zusätzlich in „OAuth-Zustimmungsbildschirm“ dich selbst als Testnutzer eintragen und die „YouTube Analytics API“ aktivieren.</div>';
 else if(!k.verbunden)h+='<div class="klein">Nicht verbunden. Gelesen werden nur Analytics-Zahlen deiner eigenen Shorts.</div><div class="zeile"><button class="btn pri" onclick="kontoLogin()">Mit Google verbinden</button></div>';
 else h+=`<span class="tag gut">verbunden</span> <button class="btn weg" onclick="kontoTrennen()">Trennen (Token löschen)</button> <button class="btn" onclick="durchNeu()">Durchsehrate jetzt holen</button>`;
 h+=`<div class="zeile" style="align-items:center"><label><input type="checkbox" style="width:auto" ${k.upload_aktiv?'checked':''} onchange="uploadSchalter(this.checked)"> Upload per API erlauben (Standard: aus)</label></div>`;
 if(k.upload_aktiv)h+=`<div class="hinweis">${k.upload_scope?'Upload-Berechtigung vorhanden. Der Knopf „Hochladen …“ erscheint bei Clips mit Status „Gut“.':'Upload ist an, aber die Berechtigung fehlt: Trennen und neu verbinden.'} Stand der YouTube-Doku (geprüft am 18.11.2026): Seit dem 2.11.2026 werden Uploads aus nicht geprüften Projekten nicht mehr automatisch auf privat gesperrt. Ohne Angabe wären sie aber öffentlich, deshalb sendet clip-watcher immer „privat“ als Standard mit. Ein Upload kostet 1.600 Quota-Punkte.</div>`;
 return h+'</div>'}
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
function trackerHtml(r){let h=keyBox(r)+kontoBox();if(r.fehler)h+=`<div class="warn">${esc(r.fehler)}</div>`;
 const t=r.daten,w=t.woche,cl=c=>c?`${esc(c.quelle)} (${zahl(c.aufrufe)})`:'–';
 h+=`<div class="box"><div class="stat"><div><b>${zahl(t.median)}</b>Median Aufrufe</div><div><b>${cl(t.bester)}</b>Bester Clip</div><div><b>${cl(t.schlechtester)}</b>Schlechtester Clip</div></div>
 <div class="klein" style="margin-top:8px">Stand ${t.stand?esc(t.stand.replace('T',' ')):'noch nicht abgerufen'} · ${t.ohne_link} von ${t.gesamt} Clips ohne YouTube-Link <button class="btn" onclick="trackerNeu()">Jetzt aktualisieren</button></div></div>
 <div class="box"><b>Letzte 7 Tage</b><div class="stat" style="margin-top:6px"><div><b>${w.geschnitten}</b>geschnitten</div><div><b>${w.hochgeladen}</b>hochgeladen</div><div><b>${zahl(w.aufrufe)}</b>Aufrufe gesamt</div><div><b>${zahl(w.median)}</b>Median</div></div></div>
 <div class="box" style="overflow:auto"><table><tr><th>Datum</th><th>Clip</th><th>Quelle</th><th class="z">Aufrufe</th><th class="z">Likes</th><th class="z">Komm.</th><th class="z">Durchgesehen %</th><th>Link</th></tr>`+t.zeilen.map(z=>`<tr><td>${esc(z.datum)}</td><td>${esc(z.clip.split('/').slice(1).join('/'))}</td><td>${esc(z.quelle)}</td><td class="z">${zahl(z.aufrufe)}</td><td class="z">${zahl(z.likes)}</td><td class="z">${zahl(z.kommentare)}</td><td class="z">${z.durchgesehen==null?'–':z.durchgesehen+' %'}</td><td>${z.hat_link?`<a href="${esc(z.link)}" target="_blank" rel="noopener">ansehen</a>`:'<span class="klein">kein Link</span>'}</td></tr>`).join('')+'</table></div>'+auswHtml(t.auswertung);return h}
async function schluessel(){const r=await api('/api/schluessel',{schluessel:$('#ak').value});if(r.fehler)alert(r.fehler);load()}
async function trendNeu(){const r=await api('/api/trend/aktualisieren',{});if(r.fehler)alert(r.fehler);render()}
async function trackerNeu(){const r=await api('/api/tracker/aktualisieren',{});if(r.fehler)alert(r.fehler);render()}
async function kanal(){const r=await api('/api/kanal',{name:$('#kn').value,rechte:$('#kr').value,nachweis:$('#kv').value});if(r.fehler)alert(r.fehler);load()}
async function loesche(n){if(!confirm(n+' entfernen?'))return;await api('/api/kanal/loeschen',{name:n});load()}
load();updateLaden();setInterval(load,8000);setInterval(updateLaden,600000);
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
            self._json({"laeuft": watcher_laeuft(), "hinweis": WATCHER["hinweis"], "frei_gb": round(frei, 1),
                        "min_frei_gb": mind, "platz_warnung": frei < mind, "fehler": dict(FEHLER)})
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
                ordner = sicher(parse_qs(urlparse(self.path).query).get("id", [""])[0])
                if not (ordner / "info.txt").is_file():
                    raise ValueError("Clip nicht gefunden")
                self._json(caption.baue(ordner, lade_config()))
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
                laenge = int(d.get("clip_laenge_s", 0))
                if not 15 <= laenge <= 20:
                    raise ValueError("Clip-Länge: 15 bis 20 Sekunden.")
                cfg = lade_config()
                cfg["clip_laenge_s"] = laenge
                speichere_config(cfg)
                if watcher_laeuft():
                    watcher_start()
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
