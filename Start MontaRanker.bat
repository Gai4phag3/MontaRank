@echo off
title MontaRanker Server
echo Starting MontaRanker...
echo.
cd /d "%~dp0"
start "" "http://localhost:8000"
py server.py
pause
