@echo off
cd /d "%~dp0"

set "PYW=C:\Users\User\AppData\Local\Programs\Python\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"

start "" "%PYW%" "%~dp0pdf_studio.py"
