"""Automatisches Aufräumen und Festplatten-Check. Werte stehen in config.json unter "aufraeumen"."""
import json, shutil, time
from datetime import datetime
from pathlib import Path

BASIS = Path(__file__).resolve().parent
STAND = BASIS / "wartung.json"
STANDARD = {"aktiv": True,
            "weg_minuten": 30,         # verworfene Clips löschen, so lange nach dem Verwerfen
            "neu_stunden": 24,         # nie angesehene Clips nach so vielen Stunden löschen
            "gut_online_stunden": 24,  # Clips mit YouTube-Link löschen, so lange nach dem Schnitt
            "roh_stunden": 24,         # roh.mp4 (die große Rohdatei) aller übrigen Clips löschen
            "aufnahmen_tage": 1}       # Ordner "aufnahmen" löschen, wenn so lange nichts mehr hineingeschrieben wurde
INTERVALL_S = 120
TAG = 86400


def einstellungen(cfg):
    e = dict(STANDARD)
    e.update((cfg or {}).get("aufraeumen") or {})
    return e


def frei_gb():
    return shutil.disk_usage(BASIS).free / 1024 ** 3


def _neueste(pfad):
    """Letzte Änderung im Ordner (auch Inhalt), damit eine laufende Aufnahme nie als alt gilt."""
    m = pfad.stat().st_mtime
    for p in pfad.rglob("*"):
        try:
            m = max(m, p.stat().st_mtime)
        except OSError:
            pass
    return m


def _entstanden(d):
    """Zeitpunkt des Schnitts: vorschau.jpg entsteht einmal, Hook und Untertitel ändern sie nicht."""
    for n in ("vorschau.jpg", "info.txt"):
        try:
            return (d / n).stat().st_mtime
        except OSError:
            pass
    return d.stat().st_mtime


def _loesche(d, bericht, grund):
    gb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1024 ** 3
    shutil.rmtree(d, ignore_errors=True)
    bericht.append(f"Clip gelöscht ({grund}, {gb:.2f} GB): {d.relative_to(BASIS).as_posix()}")
    try:
        if not any(d.parent.iterdir()):
            d.parent.rmdir()
    except OSError:
        pass


def aufraeumen(cfg, jetzt=None):
    """Löscht nach festen Fristen. Läuft alle paar Minuten. Zeilen in clips/liste.csv und tracker.json bleiben erhalten."""
    import tracker
    e = einstellungen(cfg)
    jetzt = jetzt or time.time()
    bericht = []
    clips, aufn = BASIS / "clips", BASIS / "aufnahmen"
    mit_link = tracker.link_karte()
    for info in list(clips.glob("*/*/info.txt")):
        d = info.parent
        if not d.is_dir():
            continue
        sf = d / "status.txt"
        status = sf.read_text(encoding="utf-8").strip() if sf.exists() else "neu"
        alter_h = (jetzt - _entstanden(d)) / 3600
        # Untertitel-Auftrag darf nicht mittendrin gestört werden (roh.mp4 wird dafür noch gebraucht)
        fertig = (d / "untertitel.fertig").exists() or (d / "untertitel.keine").exists() or alter_h > 6
        if not fertig:
            continue
        if status == "weg":
            minuten = (jetzt - sf.stat().st_mtime) / 60
            if minuten >= e["weg_minuten"]:
                _loesche(d, bericht, f"verworfen vor {minuten:.0f} Min")
            continue
        if status == "neu" and alter_h >= e["neu_stunden"]:
            _loesche(d, bericht, f"nie angesehen, {alter_h:.0f} Std alt")
            continue
        if status == "gut" and mit_link.get(d.relative_to(BASIS).as_posix()) and alter_h >= e["gut_online_stunden"]:
            _loesche(d, bericht, f"hochgeladen, {alter_h:.0f} Std alt")
            continue
        roh = d / "roh.mp4"
        if roh.exists() and alter_h >= e["roh_stunden"]:
            gb = roh.stat().st_size / 1024 ** 3
            roh.unlink()
            bericht.append(f"roh.mp4 gelöscht ({status}, {alter_h:.0f} Std, {gb:.2f} GB): {d.relative_to(BASIS).as_posix()}")
    if aufn.is_dir():
        for d in aufn.iterdir():
            if d.is_dir() and (jetzt - _neueste(d)) / TAG > e["aufnahmen_tage"]:
                shutil.rmtree(d, ignore_errors=True)
                bericht.append(f"Aufnahme gelöscht: aufnahmen/{d.name}")
    STAND.write_text(json.dumps({"zuletzt": datetime.now().isoformat(timespec="seconds"), "bericht": bericht,
                                 "frei_gb": round(frei_gb(), 1)}, ensure_ascii=False), encoding="utf-8")
    return bericht


def letzter_lauf():
    try:
        return json.loads(STAND.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def faellig(cfg):
    if not einstellungen(cfg)["aktiv"]:
        return False
    try:
        z = json.loads(STAND.read_text(encoding="utf-8"))["zuletzt"]
        return (datetime.now() - datetime.fromisoformat(z)).total_seconds() >= INTERVALL_S
    except (OSError, ValueError, KeyError):
        return True
