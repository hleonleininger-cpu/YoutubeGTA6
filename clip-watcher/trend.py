#!/usr/bin/env python3
"""
Trend-Modul: lernt aus aktuellen GTA-6-Shorts, welche Muster gerade funktionieren.
Nutzt die YouTube Data API v3 (Schlüssel in config.json, Feld "youtube_api_key").
Es werden keine fremden Videos kopiert, nur Titel, Länge und Zahlen ausgewertet.

  python trend.py     -> ruft einmal ab und zeigt die Top 20
"""
import json, os, re, statistics, threading, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
API = os.environ.get("YT_API_BASE", "https://www.googleapis.com/youtube/v3")
TREND_DATEI = BASIS / "trend.json"
QUOTA_DATEI = BASIS / "quota.json"
MAX_SUCHEN_TAG = 20          # search.list kostet 100 Punkte, Tageslimit 10.000
INTERVALL_S = 3 * 3600
MIN_KANAELE = 3              # ein Muster zählt nur, wenn mindestens so viele Kanäle es zeigen
LOCK = threading.Lock()


class ApiFehler(Exception):
    pass


def schluessel(cfg):
    return str((cfg or {}).get("youtube_api_key", "") or "").strip()


def api_get(pfad, params, key):
    q = urllib.parse.urlencode(dict(params, key=key))
    try:
        with urllib.request.urlopen(f"{API}/{pfad}?{q}", timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # die URL (mit Schlüssel) wird nie in die Meldung übernommen
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", {})
            grund = (err.get("errors") or [{}])[0].get("reason", "")
            msg = err.get("message", "")
        except Exception:
            grund, msg = "", ""
        raise ApiFehler(f"YouTube-API Fehler {e.code} {grund}: {msg}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ApiFehler("Keine Verbindung zur YouTube-API.") from None


def iso_dauer_s(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _zahl(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def video_details(ids, key):
    """videos.list (1 Punkt pro Aufruf, bis 50 IDs). Gibt Liste einfacher Dicts zurück."""
    out = []
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        r = api_get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]),
                               "maxResults": 50}, key)
        for v in r.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            out.append({"id": v["id"], "titel": sn.get("title", ""), "kanal": sn.get("channelTitle", ""),
                        "kanal_id": sn.get("channelId", ""), "veroeffentlicht": sn.get("publishedAt", ""),
                        "aufrufe": _zahl(st.get("viewCount")), "likes": _zahl(st.get("likeCount")),
                        "kommentare": _zahl(st.get("commentCount")),
                        "dauer_s": iso_dauer_s(v.get("contentDetails", {}).get("duration", ""))})
    return out


# ------------------------------------------------------------ Quota

def _quota_lesen():
    heute = datetime.now().strftime("%Y-%m-%d")
    try:
        q = json.loads(QUOTA_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        q = {}
    if q.get("datum") != heute:
        q = {"datum": heute, "suchen": 0}
    return q


def suchen_heute():
    return _quota_lesen()["suchen"]


def _suche_zaehlen():
    """Zählt eine Suche. Wirft ApiFehler, wenn das Tageslimit von 20 Suchen erreicht ist."""
    with LOCK:
        q = _quota_lesen()
        if q["suchen"] >= MAX_SUCHEN_TAG:
            raise ApiFehler(f"Tageslimit erreicht: höchstens {MAX_SUCHEN_TAG} Suchen pro Tag (je 100 Quota-Punkte).")
        q["suchen"] += 1
        QUOTA_DATEI.write_text(json.dumps(q), encoding="utf-8")


# ------------------------------------------------------------ Muster

STOPP = set("""und der die das ein eine einer einen mit von für auf ist sind wird nicht noch auch aber den dem des
im in an zu zum zur bei nach wie was wer wo wann warum dass diese dieser dieses alle alles nur mehr schon jetzt
neue neuen the and for with this that you are was from your have will shorts short video videos gta vi""".split())
_GTA = re.compile(r"gta[\s\-]*(?:6|vi)\b|gta\b", re.I)
_FRAGE = re.compile(r"^(was|wie|warum|wann|wer|wo|wird|ist|kommt|hat|gibt|kann|wieso|weshalb)\b", re.I)
_ZAHL = re.compile(r"(?<![\w.,#])(\d{1,3})\s+([a-zäöüß]{3,})", re.I)
_ZAHL_AUS = {"fps", "gb", "uhr", "hz", "mal"}


def _kanaele(videos, test):
    return {v["kanal_id"] or v["kanal"] for v in videos if test(v)}


def muster(videos):
    """Muster nur, wenn mindestens 3 Videos VERSCHIEDENER Kanäle sie zeigen."""
    res = []
    # häufige Titelwörter
    wort_k = {}
    for v in videos:
        t = _GTA.sub(" ", v["titel"].lower())
        for w in set(re.findall(r"[a-zäöüß]{3,}", t)):
            if w not in STOPP:
                wort_k.setdefault(w, set()).add(v["kanal_id"] or v["kanal"])
    häufig = sorted(((w, len(k)) for w, k in wort_k.items() if len(k) >= MIN_KANAELE), key=lambda x: (-x[1], x[0]))[:10]
    if häufig:
        res.append({"art": "Titelwörter", "kanaele": häufig[0][1],
                    "text": "Häufige Titelwörter: " + ", ".join(f"{w} ({n} Kanäle)" for w, n in häufig)})
    # typische Länge
    stufen = [(15, "bis 15 s"), (30, "16–30 s"), (45, "31–45 s"), (60, "46–60 s"), (120, "61–120 s"), (10 ** 9, "über 2 min")]
    beste = None
    for grenze, name in stufen:
        vorher = [g for g, _ in stufen if g < grenze]
        unten = vorher[-1] if vorher else -1
        gruppe = [v for v in videos if unten < v["dauer_s"] <= grenze]
        n = len(_kanaele(gruppe, lambda v: True))
        if n >= MIN_KANAELE and (not beste or n > beste[0]):
            beste = (n, name, statistics.median(v["dauer_s"] for v in gruppe))
    if beste:
        res.append({"art": "Länge", "kanaele": beste[0],
                    "text": f"Typische Länge: {beste[1]} (Median {beste[2]:.0f} s, {beste[0]} Kanäle)"})
    # Fragen im Titel
    fr = [v for v in videos if "?" in v["titel"] or _FRAGE.match(_GTA.sub("", v["titel"]).strip())]
    n = len(_kanaele(fr, lambda v: True))
    if n >= MIN_KANAELE:
        res.append({"art": "Fragen", "kanaele": n,
                    "text": f"Fragen im Titel: {len(fr)} von {len(videos)} Videos, {n} Kanäle. Beispiel: „{fr[0]['titel']}“"})
    # Zahlen im Titel ("5 Dinge")
    zl = []
    for v in videos:
        t = _GTA.sub(" ", v["titel"])
        for z, w in _ZAHL.findall(t):
            if w.lower() not in _ZAHL_AUS and not (2000 <= int(z) <= 2099):
                zl.append((v, f"{z} {w}"))
                break
    n = len({v["kanal_id"] or v["kanal"] for v, _ in zl})
    if n >= MIN_KANAELE:
        res.append({"art": "Zahlen", "kanaele": n,
                    "text": f"Zahlen im Titel: {len(zl)} Videos, {n} Kanäle. Beispiele: " + ", ".join(f"„{b}“" for _, b in zl[:3])})
    return res


# ------------------------------------------------------------ Abruf

def lade():
    try:
        return json.loads(TREND_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def faellig():
    d = lade()
    if not d:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(d["abgerufen"])).total_seconds() >= INTERVALL_S
    except (KeyError, ValueError):
        return True


def abrufen(cfg):
    key = schluessel(cfg)
    if not key:
        raise ApiFehler("Kein YouTube-API-Schlüssel eingetragen.")
    _suche_zaehlen()
    jetzt = datetime.now(timezone.utc)
    vor72 = (jetzt - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = api_get("search", {"part": "snippet", "q": "GTA 6", "type": "video", "regionCode": "DE",
                           "relevanceLanguage": "de", "videoDuration": "short", "order": "viewCount",
                           "publishedAfter": vor72, "maxResults": 50}, key)
    ids = [i["id"]["videoId"] for i in s.get("items", []) if i.get("id", {}).get("videoId")]
    videos = video_details(ids, key) if ids else []
    for v in videos:
        try:
            pub = datetime.fromisoformat(v["veroeffentlicht"].replace("Z", "+00:00"))
            stunden = max((jetzt - pub).total_seconds() / 3600, 1.0)  # mindestens 1 h, sonst Ausreißer
        except ValueError:
            stunden = 1.0
        v["pro_stunde"] = round((v["aufrufe"] or 0) / stunden, 1)
        v["link"] = f"https://www.youtube.com/watch?v={v['id']}"
    videos.sort(key=lambda v: v["pro_stunde"], reverse=True)
    erg = {"abgerufen": datetime.now().isoformat(timespec="seconds"), "analysiert": len(videos),
           "videos": videos[:20], "muster": muster(videos)}
    TREND_DATEI.write_text(json.dumps(erg, ensure_ascii=False, indent=1), encoding="utf-8")
    return erg


if __name__ == "__main__":
    cfg = json.loads((BASIS / "config.json").read_text(encoding="utf-8"))
    try:
        d = abrufen(cfg)
    except ApiFehler as e:
        raise SystemExit(str(e))
    for i, v in enumerate(d["videos"], 1):
        print(f"{i:2}. {v['pro_stunde']:>9} Aufr./h  {v['aufrufe']:>9}  {v['kanal']}: {v['titel']}")
    print("\n".join(m["text"] for m in d["muster"]) or "Noch keine Muster.")
