@echo off
cd /d "%~dp0"
echo Starting RASPBOT-V2 Web Console...
echo.
python app.py
echo.
echo Server stopped. Press any key to close this window.
pause >nul
