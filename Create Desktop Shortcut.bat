@echo off
REM Double-click this once to put a WITNESS icon on your desktop.
REM It launches the app (control-panel window) with the skull logo.
setlocal
set "APP=%~dp0"
if "%APP:~-1%"=="\" set "APP=%APP:~0,-1%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $desk = [Environment]::GetFolderPath('Desktop'); $lnk = $w.CreateShortcut([IO.Path]::Combine($desk,'WITNESS.lnk')); $lnk.TargetPath = '%APP%\START Kill Recorder (Window).bat'; $lnk.WorkingDirectory = '%APP%'; $lnk.IconLocation = '%APP%\witness.ico'; $lnk.WindowStyle = 7; $lnk.Description = 'WITNESS - Auto Kill Recorder'; $lnk.Save()"

if errorlevel 1 (
  echo.
  echo   Could not create the shortcut automatically.
  echo   You can make one by hand: right-click START Kill Recorder ^(Window^).bat
  echo   -^> Send to -^> Desktop, then right-click it -^> Properties -^> Change Icon
  echo   -^> browse to witness.ico in this folder.
) else (
  echo.
  echo   Done. A WITNESS icon is on your desktop. Double-click it to play.
)
echo.
pause
