@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m app.release_check
python -m app.inspect_system
pause
