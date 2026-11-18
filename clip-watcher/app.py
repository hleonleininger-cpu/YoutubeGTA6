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
                    "link": links.get(f"clips/{rel}", ""), "hook_typ": hooktypen.get(f"clips/{rel}", ""),
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
:root{--bg:#0c0d12;--s1:#14161e;--s2:#1c1f2b;--line:#272b3a;--txt:#eef0f6;--mut:#8b92a6;--a1:#ff4f8b;--a2:#ff9a3c;--ok:#2fbf7a;--bad:#e5484d;--grad:linear-gradient(135deg,#ff4f8b,#ff9a3c)}
html{color-scheme:dark}body{margin:0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;background:radial-gradient(1200px 500px at 70% -10%,#2a1530 0,transparent 60%),var(--bg);color:var(--txt);line-height:1.45}
header{position:sticky;top:0;z-index:10;background:rgba(12,13,18,.82);backdrop-filter:blur(10px);padding:12px 18px;display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line)}
.logo{width:30px;height:30px;border-radius:9px;background:var(--grad);display:grid;place-items:center;font-weight:800;color:#fff;font-size:15px}
h1{font-size:18px;margin:0;flex:1;letter-spacing:.2px}
#stat{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--mut);background:var(--s1);border:1px solid var(--line);padding:6px 12px;border-radius:999px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad);display:inline-block;box-shadow:0 0 0 3px rgba(229,72,77,.2)}.dot.an{background:var(--ok);box-shadow:0 0 0 3px rgba(47,191,122,.22)}
nav{display:flex;gap:8px;padding:14px 18px 6px;overflow:auto;max-width:1200px;margin:auto;scrollbar-width:none}
nav button{background:var(--s1);color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:8px 16px;font-size:14px;cursor:pointer;white-space:nowrap;transition:.15s}
nav button:hover{color:var(--txt);border-color:#3a3f55}nav button.akt{background:var(--grad);color:#fff;border-color:transparent;font-weight:600}
main{padding:10px 18px 60px;max-width:1200px;margin:auto}
.btn{background:var(--s2);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:9px 14px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block;transition:.15s}
.btn:hover{border-color:#454b64;background:#232738}.btn.pri{background:var(--grad);color:#fff;border-color:transparent;font-weight:600}.btn.pri:hover{filter:brightness(1.08)}
.btn.gut{background:rgba(47,191,122,.16);color:#6fe3ad;border-color:rgba(47,191,122,.4)}.btn.weg{background:rgba(229,72,77,.12);color:#ff8f93;border-color:rgba(229,72,77,.35)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px;margin-top:10px}
.karte{background:var(--s1);border-radius:18px;overflow:hidden;border:1px solid var(--line);box-shadow:0 8px 30px rgba(0,0,0,.35);display:flex;flex-direction:column}
.karte video{width:100%;aspect-ratio:9/16;background:#000;display:block;max-height:540px;object-fit:contain}
.inh{padding:14px 16px 16px;font-size:13px}.kopf{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.kopf b{font-size:16px}
.klein{color:var(--mut);font-size:12.5px}.zitat{margin:10px 0 4px;padding:9px 12px;background:var(--s2);border-left:3px solid var(--a1);border-radius:8px;font-style:italic}
.hk{margin-top:8px}.hk b{color:var(--a2)}
.aktion{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin-top:12px}.aktion .btn{text-align:center;padding:11px 12px}
details{border-top:1px solid var(--line);margin-top:12px;padding-top:2px}summary{cursor:pointer;padding:10px 0;font-weight:600;font-size:13.5px;color:var(--txt);list-style:none;display:flex;justify-content:space-between}
summary::after{content:"+";color:var(--mut)}details[open] summary::after{content:"−"}summary::-webkit-details-marker{display:none}
.zeile{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.zeile input{flex:1;min-width:0}
input,select{background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;width:100%}
input:focus,textarea:focus,select:focus{outline:2px solid rgba(255,79,139,.5);border-color:transparent}
.tag{font-size:11.5px;padding:3px 9px;border-radius:999px;background:var(--s2);color:var(--mut);border:1px solid var(--line)}
.tag.neu{color:#ffc46b;border-color:rgba(255,154,60,.4)}.tag.gut{background:rgba(47,191,122,.15);color:#6fe3ad;border-color:rgba(47,191,122,.4)}.tag.weg{background:rgba(229,72,77,.12);color:#ff8f93;border-color:rgba(229,72,77,.35)}
pre{background:#080910;padding:12px;border-radius:12px;font-size:12px;overflow:auto;max-height:65vh;white-space:pre-wrap;border:1px solid var(--line)}
.box{background:var(--s1);border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.4px}td.z,th.z{text-align:right}tr:hover td{background:rgba(255,255,255,.02)}
textarea{background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:13px;width:100%;min-height:150px;font-family:inherit}
a{color:var(--a2)}.warn{background:rgba(229,72,77,.12);border:1px solid rgba(229,72,77,.35);border-radius:12px;padding:10px 14px;font-size:13px;margin:8px 0}
.hinweis{background:rgba(255,154,60,.1);border:1px solid rgba(255,154,60,.3);border-radius:12px;padding:10px 14px;font-size:13px;margin:10px 0}
.stat{display:flex;gap:12px;flex-wrap:wrap}.stat div{background:var(--s2);border:1px solid var(--line);border-radius:12px;padding:10px 16px;color:var(--mut);font-size:12.5px}.stat b{display:block;font-size:20px;color:var(--txt)}
.leer{color:var(--mut);text-align:center;padding:70px 10px;font-size:15px}.leer::before{content:"🎬";display:block;font-size:40px;margin-bottom:8px}
.band{max-width:1200px;margin:0 auto;padding:0 18px}
#neuBtn{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:20}
@media(max-width:600px){header,nav,main{padding-left:12px;padding-right:12px}.grid{grid-template-columns:1fr}}
</style></head><body>
<header><div class="logo">C</div><h1>clip-watcher</h1><span id="stat"></span></header>
<div id="banner" class="band"></div><div id="upd" class="band"></div><nav id="tabs"></nav><main id="main"></main><div id="neuBtn"></div>
<script>
let tab='neu',clips=[],cfg=null,status={},caps={},offen={},sig='',hookE={},hv={},ups={},kontoSt={},uwahl={};
const $=s=>document.querySelector(s);
let erzwingen=false;
const api=(u,b)=>{if(b)erzwingen=true;return fetch(u,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{}).then(r=>r.json())};
function tabs(){const t=[['neu','Neu'],['gut','Gut'],['weg','Verworfen'],['trends','Trends'],['tracker','Tracker'],['kanaele','Kanäle'],['log','Protokoll']];
$('#tabs').innerHTML=t.map(([k,n])=>`<button class="${tab==k?'akt':''}" onclick="tab='${k}';render()">${n}${['neu','gut','weg'].includes(k)?' ('+clips.filter(c=>c.status==k).length+')':''}</button>`).join('')}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function karte(c){const o=(k)=>(offen[c.id+k]?'open':'');const ot=(k)=>`ontoggle="offen['${c.id}${k}']=this.open"`;
return `<div class="karte"><video src="${c.video}" controls playsinline preload="metadata"></video><div class="inh">
<div class="kopf"><b>${esc(c.quelle)}</b><span class="tag ${c.status}">${c.status=='neu'?'Neu':c.status=='gut'?'Gut':'Verworfen'}</span><span class="tag">${esc(c.rechte.split(' ')[0])}</span>${c.ut=='laeuft'?'<span class="tag neu">Untertitel folgen …</span>':''}</div>
<div class="klein">${esc(c.zeit)} · ${esc(c.ausloeser)}</div>
${c.gesagt?`<div class="zitat">„${esc(c.gesagt)}“</div>`:''}
${c.chat?`<div class="klein">Chat: ${esc(c.chat)}</div>`:''}
${c.hook?`<div class="hk">Hook: <b>${esc(c.hook)}</b></div>`:''}
<div class="aktion"><button class="btn gut" onclick="setze('${c.id}','gut')">&#10003; Gut</button><button class="btn weg" onclick="setze('${c.id}','weg')">&#10005; Weg</button><a class="btn" href="${c.video}" download title="Video laden">&#8681;</a></div>
<details ${o('h')} ${ot('h')}><summary>Hook</summary>
<div class="zeile"><input id="h_${c.id}" placeholder="Hook-Text (max. 8 Wörter)" value="${esc(hookE[c.id]||c.hook)}" maxlength="80"><button class="btn pri" onclick="hook('${c.id}')">Einbauen</button></div>
<div class="zeile"><button class="btn" onclick="vorschlag('${c.id}')">&#10024; Vorschläge</button></div>
${hv[c.id]?`<div class="zeile">${hv[c.id].map(t=>`<button class="btn" onclick="hookWahl('${c.id}',this.dataset.t)" data-t="${esc(t)}">${esc(t)}</button>`).join('')}</div>`:''}
<div class="zeile"><input id="t_${c.id}" placeholder="Hook-Typ (Frage, Zahl, Schock …)" value="${esc(c.hook_typ)}" maxlength="40"><button class="btn" onclick="hooktyp('${c.id}')">Typ speichern</button></div></details>
<details ${o('c')||(caps[c.id]||ups[c.id]?'open':'')} ${ot('c')}><summary>Caption &amp; Upload</summary>
<div class="zeile"><button class="btn pri" onclick="caption('${c.id}')">Caption erstellen</button>${kontoSt.upload_aktiv&&kontoSt.upload_scope&&c.status=='gut'?`<button class="btn" onclick="uploadOeffnen('${c.id}')">Hochladen …</button>`:''}</div>
${ups[c.id]?uploadPanel(c):''}
${caps[c.id]?`<div style="margin-top:8px"><textarea id="cp_${c.id}" readonly>${esc(caps[c.id].block)}</textarea><div class="klein">${caps[c.id].laenge} Zeichen im Titel${caps[c.id].hinweis?' · Nachweis: '+esc(caps[c.id].hinweis):''}</div><div class="zeile"><button class="btn pri" onclick="kopiere('${c.id}')">Kopieren</button><button class="btn" onclick="delete caps['${c.id}'];render()">Schließen</button></div></div>`:''}
</details>
<details ${o('l')} ${ot('l')}><summary>YouTube-Link</summary>
<div class="zeile"><input id="l_${c.id}" placeholder="Link nach dem Hochladen einfügen" value="${esc(c.link)}"><button class="btn" onclick="link('${c.id}')">Speichern</button></div>${c.srt?`<div class="zeile"><a class="btn" href="${c.srt}" download>Untertitel (.srt)</a></div>`:''}</details>
</div></div>`}
function render(){tabs();const m=$('#main');
 if(['neu','gut','weg'].includes(tab)){const l=clips.filter(c=>c.status==tab);
  m.innerHTML=(tab=='neu'?'<div class="hinweis">Vor dem Posten ansehen: kein Spoiler, keine Zwischensequenz allein, kein Leak, keine laute Musik. Bezahlter Clip: „Werbung“ sichtbar.</div>':'')+(l.length?`<div class="grid">${l.map(karte).join('')}</div>`:'<div class="leer">Noch nichts hier.</div>')}
 else if(tab=='kanaele'&&cfg){m.innerHTML=`<div class="box"><b>Überwachte Kanäle</b>`+cfg.kanaele.map(k=>`<div class="zeile" style="align-items:center"><div style="flex:1"><b>${esc(k.name)}</b> <span class="tag">${esc(k.rechte)}</span><div class="klein">${esc(k.nachweis)}</div></div><button class="btn weg" onclick="loesche('${esc(k.name)}')">Entfernen</button></div>`).join('')+`</div>
<div class="box"><b>Kanal hinzufügen</b><div class="hinweis">Fremde Kanäle nur mit Grundlage: Kampagne oder Erlaubnis des Streamers, dazu Link oder Notiz.</div>
<input id="kn" placeholder="Kanalname (ohne twitch.tv/)" style="margin-bottom:8px"><select id="kr" style="margin-bottom:8px"><option value="kampagne">kampagne</option><option value="erlaubnis">erlaubnis</option><option value="eigen">eigen</option></select>
<input id="kv" placeholder="Nachweis: Link oder Notiz" style="margin-bottom:8px"><button class="btn pri" onclick="kanal()">Speichern und Watcher neu starten</button></div>
<div class="box"><button class="btn" onclick="api('/api/watcher',{aktion:'start'}).then(load)">Watcher (neu) starten</button> <button class="btn weg" onclick="api('/api/watcher',{aktion:'stop'}).then(load)">Watcher stoppen</button></div>`}
 else if(tab=='trends'){api('/api/trend').then(r=>{m.innerHTML=trendHtml(r)})}
 else if(tab=='tracker'){api('/api/tracker').then(r=>{m.innerHTML=trackerHtml(r)})}
 else if(tab=='log'){api('/api/log').then(r=>{m.innerHTML='<pre>'+esc(r.log||'(noch nichts)')+'</pre>'})}}
function uploadPanel(c){const u=ups[c.id];return `<div class="box" style="margin-top:8px"><b>Upload zu YouTube</b><div class="hinweis">Nichts wird automatisch hochgeladen. Vorschau siehst du oben im Video. Titel und Beschreibung prüfen, dann bewusst bestätigen.</div>
<input id="ut_${c.id}" value="${esc(u.titel)}" maxlength="100" style="margin-bottom:6px"><textarea id="ub_${c.id}">${esc(u.beschreibung)}</textarea>
<select id="us_${c.id}" style="margin:6px 0"><option value="private">privat (Standard)</option><option value="unlisted">nicht gelistet</option><option value="public">öffentlich</option></select>
<div class="zeile"><button class="btn pri" onclick="uploadGo('${c.id}')">Ja, jetzt hochladen</button><button class="btn" onclick="delete ups['${c.id}'];render()">Abbrechen</button></div></div>`}
async function uploadOeffnen(id){const r=await api('/api/caption?id='+encodeURIComponent(id));if(r.fehler)return alert(r.fehler);ups[id]={titel:r.titel,beschreibung:r.beschreibung};render()}
async function uploadGo(id){const sv=$('[id="us_'+id+'"]').value;if(sv!='private'&&!confirm('Sichtbarkeit „'+sv+'“ wirklich? Standard ist privat.'))return;
 const r=await api('/api/upload',{id,bestaetigt:true,sichtbarkeit:sv,titel:$('[id="ut_'+id+'"]').value,beschreibung:$('[id="ub_'+id+'"]').value});
 if(r.fehler)return alert(r.fehler);delete ups[id];alert('Hochgeladen ('+sv+'). Link wurde in die Liste eingetragen.');load()}
async function updateLaden(){const u=await api('/api/update');window.updInfo=u;
 $('#upd').innerHTML=u.info&&u.info.neu?`<div class="hinweis">Update verfügbar (${esc(u.info.kurz)}). <button class="btn pri" onclick="updateStart()">Jetzt aktualisieren</button></div>`:'<div class="klein" style="margin:4px 0"><a href="#" onclick="updatePruefen();return false">Auf Update prüfen</a></div>'+(u.fehler?`<div class="warn">${esc(u.fehler)}</div>`:'')}
async function updatePruefen(){const r=await api('/api/update/pruefen',{});if(r.fehler)alert(r.fehler);else if(!(await api('/api/update')).info.neu)alert('Du hast die neueste Version.');updateLaden()}
async function updateStart(){const u=window.updInfo||{};
 if(!confirm((u.aufnahme_aktiv?'ACHTUNG: Es läuft gerade eine Aufnahme, ein laufender Clip kann verloren gehen.\\n\\n':'')+'Update installieren? Clips, Einstellungen und Token bleiben erhalten. Die App startet kurz neu.'))return;
 const r=await api('/api/update/installieren',{});if(r.fehler)return alert(r.fehler);
 $('#upd').innerHTML='<div class="hinweis">Update eingespielt ('+r.geaendert.length+' Dateien). App startet neu …'+(u.neustart_automatisch?'':' Falls die Seite nicht zurückkommt: starten.bat einfach neu öffnen.')+'</div>';
 for(let i=0;i<40;i++){await new Promise(x=>setTimeout(x,1500));try{await fetch('/api/status-watcher');if(i>2){location.reload();return}}catch(e){}}}
async function vorschlag(id){const r=await api('/api/hookvorschlag?id='+encodeURIComponent(id));if(r.fehler)return alert(r.fehler);hv[id]=r.vorschlaege;render()}
function hookWahl(id,t){hookE[id]=t;render()}
async function hooktyp(id){const r=await api('/api/feld',{id,spalte:'Hook-Typ',wert:$('[id="t_'+id+'"]').value});if(r.fehler)alert(r.fehler);load()}
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
async function link(id){const r=await api('/api/link',{id,link:$('[id="l_'+id+'"]').value});if(r.fehler)alert(r.fehler);load()}
async function caption(id){const r=await api('/api/caption?id='+encodeURIComponent(id));if(r.fehler)return alert(r.fehler);caps[id]=r;render()}
function kopiere(id){const t=$('[id="cp_'+id+'"]');t.select();let ok=false;try{ok=document.execCommand('copy')}catch(e){}
 if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(t.value).then(()=>alert('Kopiert.'));else alert(ok?'Kopiert.':'Bitte von Hand kopieren (Strg+C).')}
async function setze(id,s){await api('/api/status',{id,status:s});load()}
async function hook(id){const t=$('[id="h_'+id+'"]').value.trim();if(!t)return alert('Bitte Hook-Text eingeben.');const r=await api('/api/hook',{id,text:t});if(r.fehler)alert(r.fehler);load()}
async function kanal(){const r=await api('/api/kanal',{name:$('#kn').value,rechte:$('#kr').value,nachweis:$('#kv').value});if(r.fehler)alert(r.fehler);load()}
async function loesche(n){if(!confirm(n+' entfernen?'))return;await api('/api/kanal/loeschen',{name:n});load()}
async function load(){const [c,cf,s,ko]=await Promise.all([api('/api/clips'),api('/api/config'),api('/api/status-watcher'),api('/api/konto')]);clips=c.clips;cfg=cf;kontoSt=ko;
 $('#stat').innerHTML=`<span class="dot ${s.laeuft?'an':''}"></span>${s.laeuft?'Watcher läuft':'Watcher aus'}`;
 $('#banner').innerHTML=(s.hinweis?`<div class="warn">${esc(s.hinweis)}</div>`:'')+(s.platz_warnung?`<div class="warn">Nur noch ${s.frei_gb} GB Festplatte frei (Warnung unter ${s.min_frei_gb} GB). Clips mit „Weg“ markieren oder alte Aufnahmen löschen.</div>`:'')+Object.entries(s.fehler||{}).map(([k,v])=>`<div class="warn">${esc(k)}: ${esc(v)}</div>`).join('');
 const a=document.activeElement&&document.activeElement.tagName,tippt=(a=='INPUT'||a=='TEXTAREA'||a=='SELECT');
 const neu=JSON.stringify([c.clips,cf,ko]);tabs();
 if(tab=='log'){if(!tippt)render()}
 else if(neu!==sig||erzwingen){if((tippt||videoBenutzt())&&!erzwingen){$('#neuBtn').innerHTML='<button class="btn pri" onclick="neuLaden()">Neue Daten – Anzeige aktualisieren</button>'}else{sig=neu;erzwingen=false;$('#neuBtn').innerHTML='';render()}}}
function videoBenutzt(){return [...document.querySelectorAll('video')].some(v=>!v.ended&&(!v.paused||v.currentTime>0.2))}
async function neuLaden(){sig='';$('#neuBtn').innerHTML='';await load()}
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
