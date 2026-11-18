"""Caption-Block pro Clip: YouTube-Titel (max. 100 Zeichen), Beschreibung mit Credit, Hashtags."""
import re
from pathlib import Path


def _info(ordner):
    f = {}
    for z in (Path(ordner) / "info.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(Quelle|Rechte|Hook|Gesagt):\s*(.*)$", z)
        if m:
            f[m.group(1)] = m.group(2).strip()
        m = re.match(r"^\s*\d\.\s*Credit in die Beschreibung:\s*(.*)$", z)
        if m:
            f["Credit"] = m.group(1).strip()
    return f


def _kanal(cfg, quelle):
    for k in cfg.get("kanaele", []):
        if str(k.get("name", "")).lower() == quelle.lower():
            return k
    return {}


def baue(ordner, cfg, hook_entwurf=None):
    """hook_entwurf: noch nicht eingebauter Hook aus der Oberfläche, ersetzt den gespeicherten für die Vorschau."""
    f = _info(ordner)
    if hook_entwurf and hook_entwurf.strip():
        f["Hook"] = hook_entwurf.strip()[:100]
    quelle = f.get("Quelle", "")
    k = _kanal(cfg, quelle)
    r = f.get("Rechte", "").split()
    rechte = str(k.get("rechte") or (r[0] if r else "")).lower()
    kampagne = rechte == "kampagne"
    nachweis = k.get("nachweis") or " ".join(f.get("Rechte", "").split()[1:])

    hook = f.get("Hook", "").strip()
    gesagt = f.get("Gesagt", "").strip()
    if gesagt.startswith("("):  # Platzhalter wie "(Untertitel folgen …)"
        gesagt = ""
    kern = hook or gesagt[:60] or "Das musst du sehen"
    praefix, suffix = "GTA 6: ", " (Werbung)" if kampagne else ""
    platz = 100 - len(praefix) - len(suffix)
    if len(kern) > platz:
        kern = kern[:platz - 1].rstrip() + "…"
    titel = f"{praefix}{kern}{suffix}"

    credit = f.get("Credit") or (f"Clip aus dem Stream von twitch.tv/{quelle}" if quelle else "")
    zeilen = []
    if kampagne:
        zeilen.append("Werbung")
    zeilen.append(hook or gesagt[:200] or kern)
    if credit:
        zeilen.append(f"Quelle: {credit}")

    tags = ["#GTA6", "#Shorts", "#Gaming"]
    pflicht = []
    if kampagne:
        # Pflichtangaben wörtlich aus dem Nachweis: Hashtags und "<Name> nennen"
        pflicht_tags = re.findall(r"#\w+", nachweis)
        namen = re.findall(r"([\w.\-]+)\s+nennen", nachweis, re.I)
        pflicht = namen + pflicht_tags
        if namen:
            zeilen.append("Kampagne: " + ", ".join(namen))
        tags = ["#GTA6", "#Shorts"]
        for t in pflicht_tags:
            if t.lower() not in (x.lower() for x in tags):
                tags.append(t)
    beschreibung = "\n".join(zeilen) + "\n\n" + " ".join(tags)
    block = f"TITEL:\n{titel}\n\nBESCHREIBUNG:\n{beschreibung}"
    return {"titel": titel, "beschreibung": beschreibung, "laenge": len(titel), "block": block, "kampagne": kampagne,
            "hinweis": nachweis if kampagne else ""}


# ------------------------------------------------------------ Hook-Vorschläge (ohne KI, aus info.txt)

_REAKTION = {"lul": "Chat kann nicht mehr", "lulw": "Chat kann nicht mehr", "kekw": "Chat kann nicht mehr",
             "omg": "Das hat keiner kommen sehen", "omegalul": "Chat kann nicht mehr", "pog": "Das war absurd gut",
             "poggers": "Das war absurd gut", "wtf": "Was ist hier gerade passiert?", "w": "Der Chat feiert das komplett",
             "l": "Das ging komplett schief", "rip": "Das ging komplett schief", "clip": "Der Chat will genau das clippen"}


def _kuerzen(text, max_w=8):
    w = text.split()
    return " ".join(w[:max_w]) + ("…" if len(w) > max_w else "")


def hook_vorschlaege(ordner):
    f = {}
    for z in (Path(ordner) / "info.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(Gesagt|Chat-Wörter|Auslöser):\s*(.*)$", z)
        if m:
            f[m.group(1)] = m.group(2).strip()
    out = []
    # 1) was gesagt wurde: kurzer Satz als Zitat, dazu eine Frage
    gesagt = f.get("Gesagt", "")
    if gesagt and not gesagt.startswith("("):
        satz = [s.strip() for s in re.split(r"[.!?…]+", gesagt) if 2 <= len(s.split()) <= 12]
        if satz:
            beste = max(satz, key=lambda s: sum(1 for c in s if c.isalpha() and c.isupper()) + len(s) / 10)
            out.append("„" + _kuerzen(beste) + "“")
        out.append("Hat er das wirklich gesagt?")
    # 2) Chat-Reaktion
    for w in re.split(r"[,\s]+", f.get("Chat-Wörter", "").lower()):
        if w in _REAKTION and _REAKTION[w] not in out:
            out.append(_REAKTION[w])
    # 3) immer passende Standardhooks
    out += ["Das hat keiner kommen sehen", "Warte bis zum Ende", "Chat dreht komplett durch", "Das glaubt dir keiner"]
    res = []
    for h in out:
        if h not in res and len(h) <= 70:
            res.append(h)
    return res[:5]
