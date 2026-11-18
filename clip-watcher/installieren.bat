@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === clip-watcher Installation (einmalig) ===
call "%~dp0finde-python.bat"
if not defined PY (
  echo.
  echo Python ist nicht installiert. Windows hat nur eine Store-Verknuepfung, die nicht zaehlt.
  echo   1. Von https://www.python.org/downloads/ installieren
  echo   2. Im Installer unten den Haken bei "Add python.exe to PATH" setzen
  echo   3. Dieses Fenster schliessen und installieren.bat erneut starten
  pause
  exit /b 1
)
echo Python gefunden: %PY%
where winget >nul 2>nul && (winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements) || echo Kein winget: FFmpeg bitte von ffmpeg.org selbst installieren.
%PY% -m pip install --upgrade streamlink faster-whisper
echo.
echo Fertig. Schliesse dieses Fenster, oeffne ein neues und starte dann starten.bat
pause
