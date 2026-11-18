"""
Selbst-Update aus GitHub. Ersetzt nur Programmdateien (.py, .md, .txt, .command).
Nie angefasst: config.json, clips/, aufnahmen/, token.json, client_secret.json, *.csv, *.json, *.bat.
Vor dem Austausch wird jede .py-Datei auf Syntaxfehler geprüft; die alten Dateien landen in update_backup/.
"""
import io, json, os, shutil, time, urllib.error, urllib.request, zipfile
from pathlib import Path

BASIS = Path(__file__).resolve().parent
REPO = "hleonleininger-cpu/YoutubeGTA6"
STANDARD_BRANCH = "claude/ecstatic-fermat-h288vl"
API = os.environ.get("UPDATE_API_BASE", "https://api.github.com")
ZIP_URL = os.environ.get("UPDATE_ZIP_URL", "")
VERSION = BASIS / "version.json"
ENDUNGEN = {".py", ".md", ".txt", ".command"}
GESCHUETZT = {"config.json", "dauerbetrieb.txt"}
UNTERORDNER_VERBOTEN = {"clips", "aufnahmen", "update_backup", "__pycache__"}


class UpdateFehler(Exception):
    pass


def _branch(cfg):
    return str((cfg or {}).get("update_branch") or STANDARD_BRANCH)


def _holen(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "clip-watcher-update", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateFehler("Update-Quelle nicht gefunden. Das GitHub-Repo muss öffentlich sein und der Branch "
                               "existieren (config.json: update_branch).") from None
        raise UpdateFehler(f"GitHub-Fehler {e.code}.") from None
    except (urllib.error.URLError, OSError):
        raise UpdateFehler("Keine Verbindung zu GitHub.") from None


def installiert():
    try:
        return json.loads(VERSION.read_text(encoding="utf-8")).get("sha", "")
    except (OSError, ValueError):
        return ""


def pruefen(cfg):
    b = _branch(cfg)
    try:
        sha = json.loads(_holen(f"{API}/repos/{REPO}/commits/{b}"))["sha"]
    except (ValueError, KeyError):
        raise UpdateFehler("Unerwartete Antwort von GitHub.") from None
    return {"neu": sha != installiert(), "sha": sha, "kurz": sha[:7], "installiert": installiert()[:7]}


def _ziel(name):
    """name: Pfad innerhalb von clip-watcher/. None, wenn die Datei nicht ersetzt werden darf."""
    p = Path(name)
    if len(p.parts) != 1 or p.name in GESCHUETZT or p.suffix not in ENDUNGEN or p.name.startswith("."):
        return None
    return p.name


def installieren(cfg):
    """Lädt den Branch, prüft ihn und tauscht die Programmdateien aus. Gibt die Liste der geänderten Dateien zurück."""
    b = _branch(cfg)
    info = pruefen(cfg)
    url = ZIP_URL or f"https://github.com/{REPO}/archive/refs/heads/{b}.zip"
    try:
        z = zipfile.ZipFile(io.BytesIO(_holen(url, 120)))
    except zipfile.BadZipFile:
        raise UpdateFehler("Heruntergeladene Datei ist keine gültige ZIP.") from None
    neu = {}
    for n in z.namelist():
        teile = n.split("/")
        if len(teile) == 3 and teile[1] == "clip-watcher" and teile[2]:
            ziel = _ziel(teile[2])
            if ziel:
                neu[ziel] = z.read(n)
    if "app.py" not in neu or "watcher.py" not in neu:
        raise UpdateFehler("Update unvollständig (app.py/watcher.py fehlen), nichts geändert.")
    for name, inhalt in neu.items():
        if name.endswith(".py"):
            try:
                compile(inhalt, name, "exec")
            except SyntaxError as e:
                raise UpdateFehler(f"Update fehlerhaft ({name}: {e.msg}), nichts geändert.") from None
    backup = BASIS / "update_backup" / time.strftime("%Y%m%d_%H%M%S")
    geaendert = []
    for name, inhalt in neu.items():
        f = BASIS / name
        if f.exists() and f.read_bytes() == inhalt:
            continue
        if f.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, backup / name)
        tmp = f.with_name(f.name + ".neu")
        tmp.write_bytes(inhalt)
        os.replace(tmp, f)
        geaendert.append(name)
    VERSION.write_text(json.dumps({"sha": info["sha"], "zeit": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    return geaendert

