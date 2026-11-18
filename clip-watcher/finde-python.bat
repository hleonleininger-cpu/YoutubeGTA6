@echo off
rem Setzt PY auf einen echten Python-Aufruf (py-Launcher oder python), ignoriert die Microsoft-Store-Verknuepfung.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
