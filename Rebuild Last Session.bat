@echo off
REM Rebuild the reels/montage/Shorts for a session whose recap failed.
REM The per-kill clips are the only irreplaceable part — everything else is
REM derived from them, so a failed recap is always recoverable.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe main.py --rebuild %*
) else (
  python main.py --rebuild %*
)
echo.
pause
