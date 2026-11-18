"""
Entdecken: findet Livestreams, die sich zum Clippen eignen, und sucht Hinweise auf eine Clip-Erlaubnis.

- Twitch: Helix-API mit Client-ID und Client-Secret (kostenlos auf dev.twitch.tv/console, Typ "vertraulich").
- YouTube Live: nutzt den vorhandenen YouTube-API-Schlüssel. Nur zur Ansicht, der Watcher clippt nur Twitch.
- Hinweise auf Erlaubnis sind nur Hinweise. Ob geclippt werden darf, entscheidet immer der Mensch.
"""
import json, math, os, re, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

import trend
from trend import ApiFehler

TWITCH_API = os.environ.get("TWITCH_API_BASE", "https://api.twitch.tv/helix")
TWITCH_ID = os.environ.get("TWITCH_ID_BASE", "https://id.twitch.tv/oauth2")
_TOKEN = {"wert": "", "bis": 0.0}
_CACHE = {}
LOCK = threading.Lock()
CACHE_S = 60

# Stichwörter, die auf eine Clip-Regel oder Kampagne hindeuten
HINWEISE = [r"clip(?:ping|per|s)?\b", r"clips?\s+erlaubt", r"content\s+rewards?", r"creator\s*program", r"kampagne", r"campaign",
            r"whop", r"reach\s*clipping", r"vyro", r"clipping\s+programm", r"erlaubt", r"allowed", r"repost", r"highlights?\b"]


def zugang(cfg):
    return str(cfg.get("twitch_client_id") or "").strip(), str(cfg.get("twitch_client_secret") or "").strip()


def _http(url, headers=None, daten=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {}, data=daten, method="POST" if daten is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise ApiFehler("Twitch lehnt den Zugang ab. Prüf Client-ID und Client-Secret unter Einstellungen.") from None
        if e.code == 429:
            raise ApiFehler("Twitch-Limit erreicht, bitte in einer Minute noch einmal.") from None
        raise ApiFehler(f"Twitch-Fehler {e.code}.") from None
    except (urllib.error.URLError, OSError):
        raise ApiFehler("Keine Verbindung zu Twitch.") from None


def _token(cid, secret):
    with LOCK:
        if _TOKEN["wert"] and _TOKEN["bis"] - 60 > time.time() and _TOKEN.get("cid") == cid:
            return _TOKEN["wert"]
    d = _http(f"{TWITCH_ID}/token", daten=urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                                                  "grant_type": "client_credentials"}).encode())
    with LOCK:
        _TOKEN.update(wert=d["access_token"], bis=time.time() + int(d.get("expires_in", 3600)), cid=cid)
    return _TOKEN["wert"]


def _helix(pfad, params, cfg):
    cid, secret = zugang(cfg)
    if not cid or not secret:
        raise ApiFehler("Twitch-Zugang fehlt. Trag Client-ID und Client-Secret unter Einstellungen ein.")
    q = urllib.parse.urlencode(params, doseq=True)
    return _http(f"{TWITCH_API}/{pfad}?{q}", headers={"Client-Id": cid, "Authorization": f"Bearer {_token(cid, secret)}"})


def _minuten_seit(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - t).total_seconds() / 60))
    except (ValueError, AttributeError):
        return None


def potenzial(zuschauer, sprache_passt, kategorie_passt, minuten):
    """0 bis 100. Zuschauer 45, Sprache 20, Kategorie 20, Dauer 15. Nicht verkaufsfertig, nur eine Orientierung."""
    p = min(1.0, math.log10(zuschauer + 1) / 4.5) * 45
    p += 20 if sprache_passt else 0
    p += 20 if kategorie_passt else 0
    if minuten is not None:  # 10 Minuten bis 3 Stunden ist ideal: Chat und Basiswert sind da, der Stream hat Fahrt
        p += 15 if 10 <= minuten <= 180 else 9 if minuten > 180 else 5
    return int(round(p))


def twitch_streams(cfg, sprache="de", kategorie="", min_zuschauer=50, limit=40, ueberwacht=()):
    schluessel = ("t", sprache, kategorie.lower(), min_zuschauer, limit)
    with LOCK:
        c = _CACHE.get(schluessel)
        if c and time.time() - c[0] < CACHE_S:
            return c[1]
    params = {"first": 100}
    if sprache and sprache != "alle":
        params["language"] = sprache
    kat_name = ""
    if kategorie.strip():
        g = _helix("search/categories", {"query": kategorie.strip(), "first": 1}, cfg).get("data", [])
        if not g:
            raise ApiFehler(f"Kategorie „{kategorie}“ gibt es auf Twitch nicht.")
        params["game_id"] = g[0]["id"]
        kat_name = g[0]["name"]
    daten = _helix("streams", params, cfg).get("data", [])
    out = []
    for s in daten:
        if s.get("viewer_count", 0) < min_zuschauer:
            continue
        minuten = _minuten_seit(s.get("started_at", ""))
        sp = (not sprache or sprache == "alle") or s.get("language") == sprache
        out.append({"login": s["user_login"], "name": s["user_name"], "titel": s.get("title", ""), "kategorie": s.get("game_name", ""),
                    "zuschauer": s.get("viewer_count", 0), "sprache": s.get("language", ""), "minuten": minuten,
                    "bild": (s.get("thumbnail_url") or "").replace("{width}", "440").replace("{height}", "248"),
                    "link": f"https://www.twitch.tv/{s['user_login']}", "tags": s.get("tags") or [],
                    "ab18": bool(s.get("is_mature")),
                    "potenzial": potenzial(s.get("viewer_count", 0), sp, bool(kat_name) or "grand theft" in s.get("game_name", "").lower(), minuten)})
    out.sort(key=lambda x: (-x["potenzial"], -x["zuschauer"]))
    res = {"streams": out[:limit], "kategorie": kat_name, "abgerufen": datetime.now().strftime("%H:%M")}
    with LOCK:
        _CACHE[schluessel] = (time.time(), res)
    return res


def grundlage(cfg, login):
    """Sucht im Profiltext, Streamtitel und in den Tags nach Hinweisen auf eine Clip-Regel. Nur ein Hinweis, keine Erlaubnis."""
    login = re.sub(r"[^\w]", "", login.lower())
    if not login:
        raise ApiFehler("Kanalname fehlt.")
    u = _helix("users", {"login": login}, cfg).get("data", [])
    if not u:
        raise ApiFehler("Kanal nicht gefunden.")
    u = u[0]
    quellen = [("Profiltext", u.get("description") or "")]
    try:
        s = _helix("streams", {"user_login": login}, cfg).get("data", [])
        if s:
            quellen += [("Streamtitel", s[0].get("title", "")), ("Tags", " ".join(s[0].get("tags") or []))]
    except ApiFehler:
        pass
    treffer = []
    for wo, text in quellen:
        for muster in HINWEISE:
            for m in re.finditer(muster, text, re.I):
                a, b = max(0, m.start() - 60), min(len(text), m.end() + 80)
                auszug = ("…" if a else "") + text[a:b].replace("\n", " ").strip() + ("…" if b < len(text) else "")
                if not any(t["auszug"] == auszug for t in treffer):
                    treffer.append({"wo": wo, "begriff": m.group(0), "auszug": auszug})
                break
    return {"login": login, "name": u.get("display_name", login), "partner": u.get("broadcaster_type", ""),
            "profil": (u.get("description") or "")[:400], "treffer": treffer[:5],
            "link": f"https://www.twitch.tv/{login}/about", "hinweis": bool(treffer)}


def youtube_live(cfg, sprache="de", min_zuschauer=0):
    key = trend.schluessel(cfg)
    if not key:
        raise ApiFehler("Kein YouTube-API-Schlüssel eingetragen. Trag ihn unter Einstellungen ein.")
    schluessel = ("y", sprache, min_zuschauer)
    with LOCK:
        c = _CACHE.get(schluessel)
        if c and time.time() - c[0] < 300:
            return c[1]
    trend._suche_zaehlen()   # 100 Punkte, zählt zu den höchstens 20 Suchen pro Tag
    p = {"part": "snippet", "q": "GTA 6", "type": "video", "eventType": "live", "order": "viewCount", "maxResults": 25, "regionCode": "DE"}
    if sprache and sprache != "alle":
        p["relevanceLanguage"] = sprache
    ids = [i["id"]["videoId"] for i in trend.api_get("search", p, key).get("items", []) if i.get("id", {}).get("videoId")]
    out = []
    if ids:
        d = trend.api_get("videos", {"part": "snippet,liveStreamingDetails", "id": ",".join(ids)}, key)
        for v in d.get("items", []):
            det = v.get("liveStreamingDetails", {})
            if det.get("actualEndTime"):
                continue
            z = int(det.get("concurrentViewers") or 0)
            if z < min_zuschauer:
                continue
            sn = v["snippet"]
            out.append({"id": v["id"], "titel": sn.get("title", ""), "kanal": sn.get("channelTitle", ""), "zuschauer": z,
                        "minuten": _minuten_seit(det.get("actualStartTime", "")),
                        "bild": (sn.get("thumbnails", {}).get("medium") or {}).get("url", ""),
                        "link": f"https://www.youtube.com/watch?v={v['id']}"})
    out.sort(key=lambda x: -x["zuschauer"])
    res = {"streams": out, "abgerufen": datetime.now().strftime("%H:%M"), "suchen_heute": trend.suchen_heute(), "max_suchen": trend.MAX_SUCHEN_TAG}
    with LOCK:
        _CACHE[schluessel] = (time.time(), res)
    return res
