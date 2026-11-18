@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0finde-python.bat"
if not defined PY (echo Python fehlt. Bitte zuerst installieren.bat starten, siehe dort. & pause & exit /b 1)
%PY% app.py
pause
