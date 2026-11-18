#!/usr/bin/env python3
"""
Zugriff auf den eigenen YouTube-Account (Google OAuth 2.0, Desktop-App-Ablauf mit Redirect auf localhost).

- client_secret.json legst du selbst ab (aus der Google Cloud Console, Typ "Desktop-App").
- Das Token liegt nur lokal in token.json (Rechte 600). Es wird nie ausgegeben, nie geloggt und steht
  in keiner API-Antwort. token.json und client_secret.json gehören nicht in eine ZIP-Datei.
- Standard: nur lesende Berechtigungen (youtube.readonly, yt-analytics.readonly).
- Der Upload (videos.insert) ist standardmäßig AUS und braucht zusätzlich youtube.upload.
"""
import base64, hashlib, json, os, secrets, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

from trend import ApiFehler

BASIS = Path(__file__).resolve().parent
TOKEN = BASIS / "token.json"
CLIENT = BASIS / "client_secret.json"
SCOPE_LESEN = ["https://www.googleapis.com/auth/youtube.readonly",
               "https://www.googleapis.com/auth/yt-analytics.readonly"]
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = os.environ.get("OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
ANALYTICS = os.environ.get("YT_ANALYTICS_BASE", "https://youtubeanalytics.googleapis.com/v2")
UPLOAD = os.environ.get("YT_UPLOAD_BASE", "https://www.googleapis.com/upload/youtube/v3")
PENDING = {}   # state -> {"verifier", "redirect"}


def client_da():
    return CLIENT.exists()


def _client():
    try:
        d = json.loads(CLIENT.read_text(encoding="utf-8"))
        c = d.get("installed") or d.get("web") or d
        return c["client_id"], c["client_secret"]
    except (OSError, ValueError, KeyError):
        raise ApiFehler("client_secret.json fehlt oder ist ungültig. Aus der Google Cloud Console laden "
                        "(Anmeldedaten > OAuth-Client-ID > Typ „Desktop-App“) und in den Programmordner legen.") from None


def _form(url, daten):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(daten).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            g = json.loads(e.read().decode()).get("error", "")
        except Exception:
            g = ""
        raise ApiFehler("Anmeldung abgelaufen oder widerrufen, bitte neu verbinden." if g == "invalid_grant"
                        else f"Google-Anmeldung Fehler {e.code} {g}".strip()) from None
    except (urllib.error.URLError, OSError):
        raise ApiFehler("Keine Verbindung zu Google.") from None


def _speichern(t):
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(t, f)


def _lade():
    try:
        return json.loads(TOKEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status():
    t = _lade()
    return {"client_da": client_da(), "verbunden": bool(t and t.get("refresh_token")),
            "upload_scope": bool(t and SCOPE_UPLOAD in t.get("scope", ""))}


def start_login(port, mit_upload=False):
    cid, _ = _client()
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    redirect = f"http://127.0.0.1:{port}/oauth/callback"
    PENDING.clear()
    PENDING[state] = {"verifier": verifier, "redirect": redirect}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    scopes = SCOPE_LESEN + ([SCOPE_UPLOAD] if mit_upload else [])
    q = urllib.parse.urlencode({"client_id": cid, "redirect_uri": redirect, "response_type": "code",
                                "scope": " ".join(scopes), "state": state, "access_type": "offline",
                                "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256"})
    return f"{AUTH_URL}?{q}"


def fertig_login(query):
    """query: geparste Parameter des Redirects (dict mit Listen)."""
    p = PENDING.pop((query.get("state") or [""])[0], None)
    if not p:
        raise ApiFehler("Anmeldung nicht erwartet oder abgelaufen. Bitte in der Oberfläche neu starten.")
    if query.get("error"):
        raise ApiFehler("Anmeldung abgebrochen.")
    cid, secret = _client()
    t = _form(TOKEN_URL, {"client_id": cid, "client_secret": secret, "code": (query.get("code") or [""])[0],
                          "code_verifier": p["verifier"], "grant_type": "authorization_code",
                          "redirect_uri": p["redirect"]})
    if not t.get("refresh_token"):
        raise ApiFehler("Google hat kein Refresh-Token geliefert. Zugriff unter myaccount.google.com/permissions "
                        "entfernen und neu verbinden.")
    _speichern({"refresh_token": t["refresh_token"], "access_token": t.get("access_token", ""),
                "expires_at": time.time() + int(t.get("expires_in", 0)), "scope": t.get("scope", "")})


def trennen():
    try:
        TOKEN.unlink()
    except FileNotFoundError:
        pass


def access_token():
    t = _lade()
    if not t or not t.get("refresh_token"):
        raise ApiFehler("Nicht mit YouTube verbunden.")
    if t.get("expires_at", 0) - 60 > time.time() and t.get("access_token"):
        return t["access_token"]
    cid, secret = _client()
    n = _form(TOKEN_URL, {"client_id": cid, "client_secret": secret, "refresh_token": t["refresh_token"],
                          "grant_type": "refresh_token"})
    t.update(access_token=n["access_token"], expires_at=time.time() + int(n.get("expires_in", 0)))
    _speichern(t)
    return t["access_token"]


def _get(url, params):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {access_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            m = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            m = ""
        raise ApiFehler(f"YouTube Analytics Fehler {e.code}: {m}".strip()) from None
    except (urllib.error.URLError, OSError):
        raise ApiFehler("Keine Verbindung zur YouTube-API.") from None


def durchsehrate(ids, ab_datum):
    """Analytics: Aufrufe, durchschnittliche Wiedergabedauer (s), Durchsehrate (%) je eigenem Video."""
    out, ids = {}, list(ids)
    for i in range(0, len(ids), 50):
        r = _get(f"{ANALYTICS}/reports", {
            "ids": "channel==MINE", "startDate": ab_datum, "endDate": time.strftime("%Y-%m-%d"),
            "metrics": "views,averageViewDuration,averageViewPercentage", "dimensions": "video",
            "filters": "video==" + ",".join(ids[i:i + 50]), "maxResults": 200})
        for z in r.get("rows", []):
            out[z[0]] = {"views": z[1], "dauer": z[2], "prozent": round(float(z[3]), 1)}
    return out


def upload(datei, titel, beschreibung, sichtbarkeit="private"):
    """videos.insert (resumable). Sichtbarkeit wird IMMER mitgesendet, sonst wäre das Video öffentlich."""
    if sichtbarkeit not in ("private", "unlisted", "public"):
        raise ApiFehler("Ungültige Sichtbarkeit.")
    if not status()["upload_scope"]:
        raise ApiFehler("Für den Upload fehlt die Berechtigung. Upload aktivieren und neu verbinden.")
    daten = Path(datei).read_bytes()
    meta = json.dumps({"snippet": {"title": titel[:100], "description": beschreibung[:5000], "categoryId": "20"},
                       "status": {"privacyStatus": sichtbarkeit}}).encode()
    tok = access_token()
    try:
        r = urllib.request.Request(f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status", data=meta, method="POST",
                                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                                            "X-Upload-Content-Length": str(len(daten)), "X-Upload-Content-Type": "video/mp4"})
        with urllib.request.urlopen(r, timeout=60) as resp:
            ort = resp.headers["Location"]
        r = urllib.request.Request(ort, data=daten, method="PUT", headers={"Content-Type": "video/mp4"})
        with urllib.request.urlopen(r, timeout=1800) as resp:
            return json.loads(resp.read().decode())["id"]
    except urllib.error.HTTPError as e:
        raise ApiFehler(f"Upload fehlgeschlagen (HTTP {e.code}).") from None
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        raise ApiFehler("Upload fehlgeschlagen (Verbindung oder Antwort).") from None
