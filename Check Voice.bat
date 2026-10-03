@echo off
REM Double-click to see which voice WITNESS will use (and why).
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
python voice_check.py
REM voice_check.py waits for Enter itself. Pause here only when Python could
REM not run it at all, so the error stays on screen instead of flashing shut.
if errorlevel 1 pause
