@echo off
setlocal
cd /d "%~dp0"
python run_control_hub.py %*
endlocal
