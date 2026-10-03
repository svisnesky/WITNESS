@echo off
REM Step 2: check detection on a saved screenshot.
REM TIP: you can drag a screenshot (.png) directly onto this .bat file.
call "%~dp0_env.bat"
if errorlevel 1 exit /b 1
set "IMG=%~1"
if "%IMG%"=="" set /p "IMG=Drag a screenshot onto this file, or type its full path here: "
REM A file dragged into the prompt arrives already quoted when its path has a
REM space in it; quoted again below, the path splits into two arguments.
if defined IMG set "IMG=%IMG:"=%"
echo.
python main.py --test-image "%IMG%"
pause
