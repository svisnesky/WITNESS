@echo off
REM Double-click to update WITNESS to the latest version.
REM Works even when config.yaml says auto_update: false.
REM Anything new installs in this window.
REM Lines above are byte-counted: see below.
@REM
echo.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo Checking for updates...
echo.
REM Byte count: the 1bd0911 copy of this file is overwritten by the update
REM it runs, and cmd then resumes the new copy at the old offset, 217 if the
REM old copy was LF, 223 if CRLF. The 1bd0911 updater writes the new copy as
REM LF, the fixed one as CRLF. Only while the five lines above @REM total 207
REM characters do all four cases resume on a line start (@REM, echo. or the
REM call) instead of mid-line ("'w.' is not recognized").
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
