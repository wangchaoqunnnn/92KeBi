@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [92K System] checking backend dependencies...
py -m pip install -q -r requirements.txt 2>nul
echo [92K System] starting server at http://127.0.0.1:8720  (Ctrl+C 停止)
py -X utf8 run.py
pause
