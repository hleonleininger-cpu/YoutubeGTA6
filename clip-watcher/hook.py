#!/usr/bin/env python3
"""
Setzt den Hook-Text oben in den ersten 2,5 Sekunden ein.
Aufruf: python hook.py "<Clip-Ordner>" "DEIN HOOK, MAX 8 WÖRTER"
Ergebnis: short_hook.mp4 im selben Ordner (short.mp4 bleibt unverändert).
"""
import platform, shutil, subprocess, sys, textwrap
from pathlib import Path

SCHRIFTEN = {
    "Windows": ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"],
    "Darwin": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf"],
    "Linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    ordner, text = Path(sys.argv[1]), " ".join(sys.argv[2:]).strip()
    if not (ordner / "short.mp4").exists():
        sys.exit(f"Keine short.mp4 in {ordner}")
    if len(text.split()) > 8:
        print("Hinweis: mehr als 8 Wörter. Kürzer wirkt besser.")
    schrift = next((p for p in SCHRIFTEN.get(platform.system(), []) if Path(p).exists()), None)
    if not schrift:
        sys.exit("Keine passende Schrift gefunden.")
    shutil.copy(schrift, ordner / "_hook.ttf")  # relativer Pfad = keine Probleme mit C:\ im Filter

    zeilen = textwrap.wrap(text.upper(), 20)[:2]
    filter_ = []
    for i, z in enumerate(zeilen):
        (ordner / f"_hook{i}.txt").write_text(z, encoding="utf-8")
        y = 330 + i * 90
        filter_.append(f"drawtext=fontfile=_hook.ttf:textfile=_hook{i}.txt:fontsize=72:fontcolor=white:"
                       f"borderw=6:bordercolor=black:x=(w-text_w)/2:y={y}:enable='lt(t,2.5)'")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "short.mp4", "-vf", ",".join(filter_),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", "-movflags", "+faststart", "short_hook.mp4"],
                       cwd=ordner, capture_output=True, text=True)
    for f in ["_hook.ttf"] + [f"_hook{i}.txt" for i in range(len(zeilen))]:
        (ordner / f).unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit(r.stderr[-500:])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", "short_hook.mp4", "-frames:v", "1",
                    "-vf", "scale=540:-2", "vorschau_hook.jpg"], cwd=ordner)
    with open(ordner / "info.txt", "a", encoding="utf-8") as f:
        f.write(f"\nHook: {text}\n")
    print(f"Fertig: {ordner / 'short_hook.mp4'}")


if __name__ == "__main__":
    main()
