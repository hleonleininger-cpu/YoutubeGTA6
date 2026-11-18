"""Tägliche Aufräumfunktion und Festplatten-Check. Werte stehen in config.json unter "aufraeumen"."""
import json, shutil, time
from datetime import datetime
from pathlib import Path

BASIS = Path(__file__).resolve().parent
STAND = BASIS / "wartung.json"
STANDARD = {"aktiv": True, "weg_clips_tage": 3, "aufnahmen_tage": 1, "roh_gut_tage": 7}
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


def aufraeumen(cfg, jetzt=None):
    e = einstellungen(cfg)
    jetzt = jetzt or time.time()
    bericht = []
    clips, aufn = BASIS / "clips", BASIS / "aufnahmen"
    for info in list(clips.glob("*/*/info.txt")):
        d = info.parent
        sf = d / "status.txt"
        if not sf.exists():
            continue
        status, alter = sf.read_text(encoding="utf-8").strip(), (jetzt - sf.stat().st_mtime) / TAG
        if status == "weg" and alter > e["weg_clips_tage"]:
            shutil.rmtree(d, ignore_errors=True)
            bericht.append(f"Clip gelöscht (weg, {alter:.0f} Tage): {d.relative_to(BASIS).as_posix()}")
            if not any(d.parent.iterdir()):
                d.parent.rmdir()
        elif status == "gut" and alter > e["roh_gut_tage"] and (d / "roh.mp4").exists() \
                and ((d / "untertitel.fertig").exists() or (d / "untertitel.keine").exists()):
            gb = (d / "roh.mp4").stat().st_size / 1024 ** 3
            (d / "roh.mp4").unlink()
            bericht.append(f"roh.mp4 gelöscht (gut, {alter:.0f} Tage, {gb:.2f} GB): {d.relative_to(BASIS).as_posix()}")
    if aufn.is_dir():
        for d in aufn.iterdir():
            if d.is_dir() and (jetzt - _neueste(d)) / TAG > e["aufnahmen_tage"]:
                shutil.rmtree(d, ignore_errors=True)
                bericht.append(f"Aufnahme gelöscht: aufnahmen/{d.name}")
    STAND.write_text(json.dumps({"zuletzt": datetime.now().isoformat(timespec="seconds"), "bericht": bericht},
                                ensure_ascii=False), encoding="utf-8")
    return bericht


def faellig(cfg):
    if not einstellungen(cfg)["aktiv"]:
        return False
    try:
        z = json.loads(STAND.read_text(encoding="utf-8"))["zuletzt"]
        return (datetime.now() - datetime.fromisoformat(z)).total_seconds() >= TAG
    except (OSError, ValueError, KeyError):
        return True
