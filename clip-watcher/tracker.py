#!/usr/bin/env python3
"""
Tracker-Modul: holt Aufrufe, Likes und Kommentare der eigenen Shorts (Links aus clips/liste.csv)
und trägt die Aufrufe in die Spalte "Aufrufe" ein. Die Spalten der Liste bleiben unverändert.
"""
import csv, json, os, re, statistics, threading
from datetime import datetime, timedelta
from pathlib import Path

import trend

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
                     "kommentare": st.get("kommentare"), "hook_typ": g(z, "Hook-Typ"), "laenge": g(z, "Länge")})
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
    return erg
