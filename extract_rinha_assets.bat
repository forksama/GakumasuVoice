@echo off
setlocal
cd /d "%~dp0"
python "%~dp0gakumasu_voice.py" extract-rinha %*
exit /b %ERRORLEVEL%
