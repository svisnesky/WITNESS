@echo off
REM Pick where the kill popup (RUNNER DOWN / +XP) sits on your screen. Only
REM needed if kills are missed: the shipped region works on 16:9 screens.
REM Uses capture_source from config.yaml (screen by default). Drag a kill-popup
REM screenshot onto this file, or run it and switch to the game during the
REM countdown.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo.
echo   Uses capture_source from config.yaml ^(screen by default^).
echo   Drag a kill-popup screenshot onto this file, or run it and switch to the
echo   game during the countdown. Then drag a box around the RUNNER DOWN / +XP
echo   popup area and press ENTER.
echo.
python calibrate.py %*
pause
