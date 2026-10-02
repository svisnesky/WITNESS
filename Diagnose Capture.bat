@echo off
REM Double-click WHILE Marathon is on screen (ideally with a RUNNER DOWN /
REM PRECISION DOWN popup showing) to see exactly what WITNESS captures.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
python diagnose.py
REM diagnose.py waits for Enter itself. Pause here only when Python could not
REM run it at all, so the error stays on screen instead of flashing shut.
if errorlevel 1 pause
