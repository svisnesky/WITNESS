@echo off
REM Teach WITNESS another game's kill popup (experimental: only Marathon ships
REM with a profile). This is the console wizard. It watches while you get a
REM kill, asks which text was the popup, then watches one more kill to verify
REM the profile before saving it. Have the game running and a kill handy.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo.
python main.py --teach
echo.
pause
