@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0finde-python.bat"
if not defined PY (echo Python fehlt. Bitte zuerst installieren.bat starten, siehe dort. & pause & exit /b 1)
echo Am Handy im selben WLAN: http://DEINE-PC-IP:8765   (IP findest du mit ipconfig)
set CLIPWATCHER_LOOP=1
set "ARG="
:start
%PY% app.py --lan %ARG%
if errorlevel 3 (set "ARG=--neustart" & goto start)
pause
