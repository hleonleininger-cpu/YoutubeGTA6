@echo off
chcp 65001 >nul
echo === clip-watcher Installation (einmalig) ===
where python >nul 2>nul || (echo Python fehlt. Installiere es von python.org und setze den Haken "Add to PATH". & pause & exit /b 1)
where winget >nul 2>nul && (winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements) || echo Kein winget: FFmpeg bitte von ffmpeg.org selbst installieren.
python -m pip install --upgrade streamlink faster-whisper
echo.
echo Fertig. Schliesse dieses Fenster, oeffne ein neues und starte dann starten.bat
pause
