@echo off
REM Verify the YouTube upload setup without playing a session.
REM   no args    read-only checks; nothing is put on your channel
REM   --upload   also uploads a 3-second PRIVATE test video (conclusive)
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe youtube_check.py %*
) else (
  python youtube_check.py %*
)
echo.
pause
