#!/usr/bin/env python3
"""
Tracker-Modul: holt Aufrufe, Likes und Kommentare der eigenen Shorts (Links aus clips/liste.csv)
und trägt die Aufrufe in die Spalte "Aufrufe" ein. Die Spalten der Liste bleiben unverändert.
"""
import csv, json, os, re, statistics, threading
from datetime import datetime, timedelta
from pathlib import Path

import konto, trend

BASIS = Path(__file__).resolve().parent
LISTE = BASIS / "clips" / "liste.csv"
TRACKER_DATEI = BASIS / "tracker.json"
INTERVALL_S = 3600
CSV_LOCK = threading.Lock()
_ID = re.compile(r"(?:youtu\.be/|/shorts/|/embed/|[?&]v=)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")


def video_id(link):
    link = str(link or "").strip()
    m = _ID.search(link)
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[A-Za-z0-9_-]{11}", link) else ""


def _norm(p):
    return str(p).replace("\\", "/").strip()


def lese_liste():
    """-> (Kopfzeile, Zeilen). Leere Liste, wenn die Datei fehlt."""
    if not LISTE.exists():
        return [], []
    with open(LISTE, encoding="utf-8-sig", newline="") as f:
        z = [r for r in csv.reader(f, delimiter=";")]
    return (z[0], z[1:]) if z else ([], [])


def _schreibe(kopf, zeilen):
    tmp = LISTE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(kopf)
        w.writerows(zeilen)
    os.replace(tmp, LISTE)


def _spalte(kopf, name):
    return kopf.index(name) if name in kopf else None


def link_karte():
    """Clip-Pfad (clips/<datum>/<ordner>) -> Link."""
    kopf, zeilen = lese_liste()
    c, l = _spalte(kopf, "Clip"), _spalte(kopf, "Link")
    if c is None or l is None:
        return {}
    return {_norm(z[c]): z[l] for z in zeilen if len(z) > l and z[l].strip()}


def feld_karte(spalte):
    kopf, zeilen = lese_liste()
    c, i = _spalte(kopf, "Clip"), _spalte(kopf, spalte)
    if c is None or i is None:
        return {}
    return {_norm(z[c]): z[i] for z in zeilen if len(z) > i and z[i].strip()}


def setze_link(clip_rel, link):
    """Trägt den YouTube-Link eines Clips in liste.csv ein. Leerer Link löscht ihn."""
    link = str(link or "").strip()
    if link and not video_id(link):
        raise ValueError("Das sieht nicht nach einem YouTube-Link aus (z. B. https://youtube.com/shorts/XXXXXXXXXXX).")
    ziel = _norm("clips/" + clip_rel)
    with CSV_LOCK:
        kopf, zeilen = lese_liste()
        c, l, p = _spalte(kopf, "Clip"), _spalte(kopf, "Link"), _spalte(kopf, "Plattform")
        if c is None or l is None:
            raise ValueError("clips/liste.csv fehlt oder hat keine Spalte „Link“.")
        for z in zeilen:
            z.extend([""] * (len(kopf) - len(z)))
            if _norm(z[c]) == ziel:
                z[l] = link
                if p is not None and link and not z[p]:
                    z[p] = "YouTube"
                _schreibe(kopf, zeilen)
                return True
    raise ValueError("Clip steht nicht in liste.csv.")


def setze_feld(clip_rel, spalte, wert):
    """Setzt ein Textfeld (z. B. Hook-Typ) in liste.csv."""
    ziel = _norm("clips/" + clip_rel)
    with CSV_LOCK:
        kopf, zeilen = lese_liste()
        c, i = _spalte(kopf, "Clip"), _spalte(kopf, spalte)
        if c is None or i is None:
            raise ValueError(f"liste.csv hat keine Spalte „{spalte}“.")
        for z in zeilen:
            z.extend([""] * (len(kopf) - len(z)))
            if _norm(z[c]) == ziel:
                z[i] = str(wert).strip()[:60]
                _schreibe(kopf, zeilen)
                return True
    raise ValueError("Clip steht nicht in liste.csv.")


def _lade():
    try:
        return json.loads(TRACKER_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"stand": "", "videos": {}}


def aktualisiere(cfg):
    key = trend.schluessel(cfg)
    if not key:
        raise trend.ApiFehler("Kein YouTube-API-Schlüssel eingetragen.")
    with CSV_LOCK:
        kopf, zeilen = lese_liste()
    c, l = _spalte(kopf, "Clip"), _spalte(kopf, "Link")
    if c is None or l is None:
        return 0
    ids = {video_id(z[l]) for z in zeilen if len(z) > l and video_id(z[l])}
    if not ids:
        return 0
    daten = {v["id"]: v for v in trend.video_details(sorted(ids), key)}
    with CSV_LOCK:  # frisch lesen: der Watcher hängt zwischendurch neue Zeilen an
        kopf, zeilen = lese_liste()
        a = _spalte(kopf, "Aufrufe")
        if a is not None:
            for z in zeilen:
                z.extend([""] * (len(kopf) - len(z)))
                v = daten.get(video_id(z[l]))
                if v and v["aufrufe"] is not None:
                    z[a] = str(v["aufrufe"])
            _schreibe(kopf, zeilen)
    t = _lade()
    t["stand"] = datetime.now().isoformat(timespec="seconds")
    for i, v in daten.items():
        t["videos"][i] = {"aufrufe": v["aufrufe"], "likes": v["likes"], "kommentare": v["kommentare"],
                          "titel": v["titel"]}
    TRACKER_DATEI.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(daten)


def aktualisiere_durchsehrate():
    """Analytics (nur lesend): Durchsehrate je eigenem Short -> Spalte "Durchgesehen %" und tracker.json."""
    with CSV_LOCK:
        kopf, zeilen = lese_liste()
    l, d = _spalte(kopf, "Link"), _spalte(kopf, "Datum")
    if l is None:
        return 0
    ids = {video_id(z[l]) for z in zeilen if len(z) > l and video_id(z[l])}
    if not ids:
        return 0
    daten_alt = []
    for z in zeilen:
        try:
            daten_alt.append(datetime.strptime(z[d], "%d.%m.%Y"))
        except (ValueError, IndexError, TypeError):
            pass
    ab = (min(daten_alt) - timedelta(days=1) if daten_alt else datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    erg = konto.durchsehrate(sorted(ids), ab)
    with CSV_LOCK:
        kopf, zeilen = lese_liste()
        sp = _spalte(kopf, "Durchgesehen %")
        if sp is not None:
            for z in zeilen:
                z.extend([""] * (len(kopf) - len(z)))
                e = erg.get(video_id(z[l]))
                if e:
                    z[sp] = str(e["prozent"])
            _schreibe(kopf, zeilen)
    t = _lade()
    for i, e in erg.items():
        t["videos"].setdefault(i, {}).update(durchgesehen=e["prozent"], wiedergabedauer=e["dauer"])
    t["analytics_stand"] = datetime.now().isoformat(timespec="seconds")
    TRACKER_DATEI.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(erg)


MIN_GRUPPE = 10


def _stufe(sek):
    for g, n in ((20, "bis 20 s"), (30, "21–30 s"), (45, "31–45 s")):
        if sek <= g:
            return n
    return "über 45 s"


def auswertung(rows):
    """Median der Durchsehrate je Hook-Typ und je Längengruppe. Unter 10 Clips pro Gruppe: zu wenig Daten."""
    def gruppiere(schluessel):
        g = {}
        for r in rows:
            k = schluessel(r)
            if k and r.get("durchgesehen") is not None:
                g.setdefault(k, []).append(r["durchgesehen"])
        return [{"gruppe": k, "clips": len(v),
                 "median": round(statistics.median(v), 1) if len(v) >= MIN_GRUPPE else None}
                for k, v in sorted(g.items(), key=lambda x: -len(x[1]))]
    def sek(r):
        m = re.match(r"\s*(\d+)", r.get("laenge") or "")
        return _stufe(int(m.group(1))) if m else ""
    return {"hook": gruppiere(lambda r: (r.get("hook_typ") or "").strip().lower()), "laenge": gruppiere(sek),
            "min": MIN_GRUPPE}


def durchsehrate_faellig():
    s = _lade().get("analytics_stand")
    try:
        return not s or (datetime.now() - datetime.fromisoformat(s)).total_seconds() >= INTERVALL_S
    except ValueError:
        return True


def faellig():
    s = _lade().get("stand")
    try:
        return not s or (datetime.now() - datetime.fromisoformat(s)).total_seconds() >= INTERVALL_S
    except ValueError:
        return True


def _int(x):
    try:
        return int(str(x).replace(".", "").strip())
    except ValueError:
        return None


def _fl(x):
    try:
        return float(str(x).replace(",", ".").strip())
    except ValueError:
        return None


def uebersicht():
    kopf, zeilen = lese_liste()
    t = _lade()
    g = lambda z, n: z[kopf.index(n)] if n in kopf and len(z) > kopf.index(n) else ""
    rows = []
    for z in zeilen:
        vid = video_id(g(z, "Link"))
        st = t["videos"].get(vid, {})
        aufr = st.get("aufrufe") if st.get("aufrufe") is not None else _int(g(z, "Aufrufe"))
        rows.append({"clip": _norm(g(z, "Clip")), "quelle": g(z, "Quelle"), "datum": g(z, "Datum"),
                     "link": g(z, "Link"), "hat_link": bool(vid), "aufrufe": aufr, "likes": st.get("likes"),
                     "kommentare": st.get("kommentare"), "hook_typ": g(z, "Hook-Typ"), "laenge": g(z, "Länge"),
                     "durchgesehen": st.get("durchgesehen") if st.get("durchgesehen") is not None else _fl(g(z, "Durchgesehen %"))})
    mit = [r for r in rows if r["hat_link"] and r["aufrufe"] is not None]
    erg = {"stand": t.get("stand", ""), "zeilen": rows, "gesamt": len(rows),
           "ohne_link": sum(1 for r in rows if not r["hat_link"]), "median": None, "bester": None, "schlechtester": None}
    if mit:
        erg["median"] = statistics.median(r["aufrufe"] for r in mit)
        erg["bester"] = max(mit, key=lambda r: r["aufrufe"])
        erg["schlechtester"] = min(mit, key=lambda r: r["aufrufe"])
    grenze = datetime.now() - timedelta(days=7)
    woche = []
    for r in rows:
        try:
            if datetime.strptime(r["datum"], "%d.%m.%Y") >= grenze:
                woche.append(r)
        except ValueError:
            pass
    wm = [r for r in woche if r["hat_link"] and r["aufrufe"] is not None]
    erg["woche"] = {"geschnitten": len(woche), "hochgeladen": sum(1 for r in woche if r["hat_link"]),
                    "aufrufe": sum(r["aufrufe"] for r in wm),
                    "median": statistics.median(r["aufrufe"] for r in wm) if wm else None}
    erg["auswertung"] = auswertung(rows)
    return erg
