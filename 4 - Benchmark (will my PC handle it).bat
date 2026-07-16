@echo off
REM Answers "will my PC handle this?" with a measurement. Safe to run before
REM any other setup: no OBS needed, nothing recorded, nothing changed.
REM First run installs the OCR engine (big download) - that install is the
REM same one the app itself needs, so nothing is wasted.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
echo.
python main.py --bench
echo.
pause
