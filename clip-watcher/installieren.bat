@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === clip-watcher Installation (einmalig) ===
call "%~dp0finde-python.bat"
if not defined PY (
  echo.
  echo Python ist nicht installiert. Windows hat nur eine Store-Verknuepfung, die nicht zaehlt.
  echo   1. Python 3.12 von https://www.python.org/downloads/release/python-31211/ installieren
  echo   2. Im Installer den Haken bei "Add python.exe to PATH" setzen
  echo   3. Dieses Fenster schliessen und installieren.bat erneut starten
  pause
  exit /b 1
)
echo Python gefunden: %PY%
%PY% --version
echo %PY% | findstr /C:"3.12" /C:"3.13" /C:"3.11" /C:"3.10" >nul || (
  echo.
  echo HINWEIS: Das ist nicht Python 3.12. Fuer Untertitel ^(faster-whisper^) gibt es bei sehr neuen Python-Versionen
  echo oft keine fertigen Pakete. Empfohlen: Python 3.12 zusaetzlich installieren, dann dieses Skript neu starten.
  echo.
)
where ffmpeg >nul 2>nul && (echo FFmpeg ist schon da.) || (
  where winget >nul 2>nul && winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements || echo FFmpeg bitte von ffmpeg.org selbst installieren.
)
echo.
echo --- streamlink ---
%PY% -m pip install --upgrade streamlink || (echo FEHLER bei streamlink. & pause & exit /b 1)
echo.
echo --- faster-whisper ^(Untertitel^) ---
%PY% -m pip install --upgrade faster-whisper || (
  echo.
  echo faster-whisper liess sich nicht installieren. Der Watcher laeuft trotzdem, Clips entstehen dann OHNE Untertitel.
  echo Fuer Untertitel: Python 3.12 installieren und dieses Skript neu starten.
)
echo.
echo Fertig. Schliesse dieses Fenster, oeffne ein neues und starte dann starten.bat
pause
