@echo off
rem Setzt PY auf ein passendes Python. Bevorzugt 3.12/3.13/3.11 (faster-whisper hat dafuer fertige Pakete), ignoriert die Store-Verknuepfung.
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do if not defined PY py -%%V --version >nul 2>nul && set "PY=py -%%V"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
