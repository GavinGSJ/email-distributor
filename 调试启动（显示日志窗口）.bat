@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m mailer.webapp
if errorlevel 1 pause
