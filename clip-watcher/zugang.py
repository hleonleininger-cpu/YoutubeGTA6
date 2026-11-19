"""
Passwortschutz, Anmelde-Cookie und App-Symbole (PWA) für den Zugriff von unterwegs.

- Das Passwort wird nie im Klartext gespeichert (PBKDF2-SHA256 mit eigenem Salz).
- Das Anmelde-Cookie ist signiert (HMAC), 30 Tage gültig und verliert seine Gültigkeit, sobald das Passwort geändert wird.
- Zu viele falsche Versuche sperren eine Adresse für 5 Minuten.
- Anfragen, die über einen Tunnel oder Proxy kommen (Tailscale, Cloudflare ...), erkennt man an ihren Kopfzeilen.
  Ohne Passwort werden solche Anfragen abgewiesen.
"""
import base64, hashlib, hmac, secrets, struct, threading, time, zlib

ITER = 200_000
MIN_LAENGE = 10
COOKIE = "cw_sitzung"
TAGE = 30
FERN_KOPF = ("x-forwarded-for", "forwarded", "x-real-ip", "tailscale-user-login", "cf-connecting-ip", "x-forwarded-host")


def hash_passwort(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)
    return f"pbkdf2${ITER}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def pruefe_passwort(pw, gespeichert):
    try:
        _, it, salt, h = gespeichert.split("$")
        neu = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(salt), int(it))
        return hmac.compare_digest(neu, base64.b64decode(h))
    except (ValueError, AttributeError, TypeError):
        return False


def neues_geheimnis():
    return secrets.token_hex(32)


def token(geheimnis, tage=TAGE):
    bis = str(int(time.time()) + tage * 86400)
    sig = hmac.new(geheimnis.encode(), bis.encode(), hashlib.sha256).hexdigest()
    return f"{bis}.{sig}"


def token_gueltig(wert, geheimnis):
    try:
        bis, sig = (wert or "").split(".")
        soll = hmac.new(geheimnis.encode(), bis.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, soll) and int(bis) > time.time()
    except (ValueError, AttributeError, TypeError):
        return False


def ist_fern(kopfzeilen):
    """True, wenn die Anfrage über einen Tunnel oder Proxy kam."""
    return any(kopfzeilen.get(k) for k in FERN_KOPF)


class Bremse:
    """Höchstens 5 Fehlversuche je Adresse in 5 Minuten."""
    def __init__(self, max_fehler=5, fenster=300):
        self.m, self.f, self.d, self.l = max_fehler, fenster, {}, threading.Lock()

    def gesperrt(self, ip):
        with self.l:
            jetzt = time.time()
            v = [t for t in self.d.get(ip, []) if jetzt - t < self.f]
            self.d[ip] = v
            return len(v) >= self.m

    def fehler(self, ip):
        with self.l:
            self.d.setdefault(ip, []).append(time.time())

    def ok(self, ip):
        with self.l:
            self.d.pop(ip, None)


# ---------------------------------------------------------------- App-Symbol (PNG ohne Zusatzpakete)

_ICONS = {}


def icon_png(groesse):
    """Abgerundetes Quadrat mit Farbverlauf Rosa nach Orange und weißem Play-Dreieck."""
    if groesse in _ICONS:
        return _ICONS[groesse]
    n, ss = groesse, 2
    r = 0.22 * n
    # Dreieck (relativ): Spitze rechts
    A, B, C = (0.39 * n, 0.27 * n), (0.39 * n, 0.73 * n), (0.76 * n, 0.50 * n)

    def im_dreieck(x, y):
        def vz(p, q, s):
            return (p[0] - s[0]) * (q[1] - s[1]) - (q[0] - s[0]) * (p[1] - s[1])
        p = (x, y)
        d1, d2, d3 = vz(p, A, B), vz(p, B, C), vz(p, C, A)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    def im_quadrat(x, y):
        cx = min(max(x, r), n - r)
        cy = min(max(y, r), n - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    zeilen = []
    for py in range(n):
        z = bytearray([0])
        for px in range(n):
            a = d = 0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = px + (sx + .5) / ss, py + (sy + .5) / ss
                    if im_quadrat(x, y):
                        a += 1
                        if im_dreieck(x, y):
                            d += 1
            t = (px + py) / (2 * n)
            rot, gruen, blau = int(255 + (255 - 255) * t), int(79 + (154 - 79) * t), int(154 + (60 - 154) * t)
            w = d / max(a, 1)
            col = (int(rot * (1 - w) + 255 * w), int(gruen * (1 - w) + 255 * w), int(blau * (1 - w) + 255 * w))
            z += bytes((col[0], col[1], col[2], int(255 * a / (ss * ss))))
        zeilen.append(bytes(z))
    roh = b"".join(zeilen)

    def chunk(t, daten):
        c = struct.pack(">I", len(daten)) + t + daten
        return c + struct.pack(">I", zlib.crc32(t + daten) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(roh, 9)) + chunk(b"IEND", b"")
    _ICONS[groesse] = png
    return png


MANIFEST = """{
  "name": "clip-watcher",
  "short_name": "clip-watcher",
  "description": "Clips aus Twitch-Streams sichten, mit Hook und Caption versehen und hochladen.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#0b0919",
  "theme_color": "#0b0919",
  "lang": "de",
  "icons": [
    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}
  ]
}
"""

SERVICE_WORKER = "self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',()=>{});\n"

LOGIN_HTML = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0919"><link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Anmelden · clip-watcher</title>
<style>
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;display:grid;place-items:center;padding:20px;background:radial-gradient(800px 500px at 10% -10%,rgba(255,79,154,.22),transparent 60%),radial-gradient(800px 500px at 100% 0%,rgba(139,107,255,.26),transparent 60%),#0b0919;color:#f6f3ff;font:15px/1.5 Inter,"Segoe UI",system-ui,-apple-system,sans-serif}
.k{width:min(380px,100%);background:linear-gradient(180deg,rgba(255,255,255,.04),transparent 120px),#14112b;border:1px solid rgba(168,150,255,.2);border-radius:20px;padding:28px 26px;box-shadow:0 30px 80px -30px rgba(0,0,0,.8)}
.logo{width:44px;height:44px;border-radius:13px;background:linear-gradient(135deg,#ff4f9a,#ff9a3c);display:grid;place-items:center;margin-bottom:18px;box-shadow:0 10px 26px -8px rgba(255,79,154,.7)}
h1{margin:0 0 4px;font:600 22px/1.2 "Space Grotesk",Inter,system-ui,sans-serif;letter-spacing:-.02em}
p{margin:0 0 20px;color:#b6aedb;font-size:14px}
label{display:block;font-size:12.5px;font-weight:500;color:#b6aedb;margin-bottom:6px}
input{width:100%;font:inherit;color:inherit;background:#0b0919;border:1px solid rgba(168,150,255,.22);border-radius:11px;padding:12px 14px}
input:focus{outline:none;border-color:#ff4f9a;box-shadow:0 0 0 3px rgba(255,79,154,.2)}
button{width:100%;margin-top:14px;font:inherit;font-weight:700;color:#1b0a14;background:linear-gradient(120deg,#ff4f9a,#ff9a3c);border:0;border-radius:11px;padding:13px;cursor:pointer}
button:disabled{opacity:.6}
.f{min-height:20px;margin-top:12px;font-size:13px;color:#ff7a85}
</style></head><body><form class="k" id="f" autocomplete="on">
<div class="logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="#fff"><path d="M8 5.2v13.6L19 12z"/></svg></div>
<h1>clip-watcher</h1><p>Melde dich an, um deine Clips zu sehen.</p>
<label for="pw">Passwort</label><input id="pw" type="password" autocomplete="current-password" autofocus required>
<button id="b">Anmelden</button><div class="f" id="m" role="alert"></div></form>
<script>
const f=document.getElementById('f'),m=document.getElementById('m'),b=document.getElementById('b');
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;m.textContent='';
 try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passwort:document.getElementById('pw').value})});
  const d=await r.json();if(d.ok){location.href='/'}else{m.textContent=d.fehler||'Anmeldung fehlgeschlagen.';b.disabled=false;document.getElementById('pw').select()}}
 catch(x){m.textContent='Keine Verbindung zu clip-watcher.';b.disabled=false}});
</script></body></html>
"""

GESPERRT_HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fernzugriff gesperrt</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:#0b0919;color:#f6f3ff;font:15px/1.6 system-ui,sans-serif}
.k{max-width:440px;background:#14112b;border:1px solid rgba(255,122,133,.4);border-radius:18px;padding:26px}h1{margin:0 0 8px;font-size:20px}p{margin:8px 0;color:#b6aedb}</style></head>
<body><div class="k"><h1>Fernzugriff gesperrt</h1><p>Diese Seite wird über einen Tunnel oder Proxy aufgerufen, aber am PC ist noch <b>kein Passwort</b> gesetzt. Ohne Passwort darf niemand von außen auf clip-watcher.</p>
<p>Setz am PC in clip-watcher unter <b>Einstellungen → Fernzugriff</b> ein Passwort und lade diese Seite danach neu.</p></div></body></html>
"""
