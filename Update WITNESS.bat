@echo off
REM Double-click to update WITNESS to the latest version (no typing, no curl).
REM Works even with auto_update: false in config.yaml. Anything new that the
REM update needs is then installed in this window.
echo.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo Checking for updates...
echo.
REM The update below can rewrite this very file. cmd reads a whole
REM parenthesized block before running any of it, and the exit at the end
REM of the block means it never reads this file again, so a rewritten copy
REM is never resumed halfway through a line. Setup then runs again: if the
REM update changed requirements.txt, the new components install now, in
REM this window, instead of at the next launch.
(
    python -c "import inspect, updater; kw = {'force': True} if 'force' in inspect.signature(updater.check_and_update).parameters else {}; print(updater.check_and_update('.', **kw)[1])"
    echo.
    call "%~dp0_env.bat"
    if errorlevel 1 exit /b 1
    echo ------------------------------------------------------------
    echo Done. Close this window and open WITNESS.
    echo ------------------------------------------------------------
    pause
    exit /b 0
)
