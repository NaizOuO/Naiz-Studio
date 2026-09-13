@echo off
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0naiz_studio.py"
) else (
    start "" pyw "%~dp0naiz_studio.py"
)
