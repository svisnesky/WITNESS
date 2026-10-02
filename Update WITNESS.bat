@echo off
REM Double-click to update WITNESS to the latest version (no typing, no curl).
REM Works even with auto_update: false in config.yaml. Anything new that the
REM update needs is then installed in this window.
echo.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo Checking for updates...
echo.
python -c "import inspect, updater; kw = {'force': True} if 'force' in inspect.signature(updater.check_and_update).parameters else {}; print(updater.check_and_update('.', **kw)[1])"
echo.
REM Run setup again: if the update changed requirements.txt, the new
REM components install now, while this window is open, instead of at the
REM next launch.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo ------------------------------------------------------------
echo Done. Close this window and open WITNESS.
echo ------------------------------------------------------------
pause
