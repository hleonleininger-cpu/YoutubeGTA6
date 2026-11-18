@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Am Handy im selben WLAN: http://DEINE-PC-IP:8765   (IP findest du mit ipconfig)
python app.py --lan
pause
