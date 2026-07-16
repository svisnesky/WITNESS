@echo off
REM Launches the control-panel window (recommended). Use "START Kill
REM Recorder.bat" instead if you want the raw console version.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
start "" ".venv\Scripts\pythonw.exe" gui.py
