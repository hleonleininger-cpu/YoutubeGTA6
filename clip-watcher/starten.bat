@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0finde-python.bat"
if not defined PY (echo Python fehlt. Bitte zuerst installieren.bat starten, siehe dort. & pause & exit /b 1)
set CLIPWATCHER_LOOP=1
set "ARG="
:start
%PY% app.py %ARG%
rem Exit-Code 3 = Update eingespielt, App startet sich neu
if errorlevel 3 (set "ARG=--neustart" & goto start)
pause
