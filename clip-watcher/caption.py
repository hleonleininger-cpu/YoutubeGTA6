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


def baue(ordner, cfg):
    f = _info(ordner)
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
    block = f"TITEL:\n{titel}\n\nBESCHREIBUNG:\n" + "\n".join(zeilen) + "\n\n" + " ".join(tags)
    return {"titel": titel, "laenge": len(titel), "block": block, "kampagne": kampagne,
            "hinweis": nachweis if kampagne else ""}
