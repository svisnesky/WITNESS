@echo off
REM Shared setup/activation used by the other .bat launchers. Not meant to be
REM run on its own.
REM   First run: finds Python 3.10-3.12, creates .venv, installs everything.
REM   Later runs: if requirements.txt changed since the last good install, for
REM   example an update added a package, installs what is missing. Otherwise it
REM   only activates .venv, which takes a moment.
REM
REM Rules this file follows on purpose. Keep them when editing:
REM   - No labels and no goto. The auto-updater can save this file with LF
REM     line endings, and cmd.exe mis-jumps to labels in LF files.
REM   - No setlocal. The venv activation has to survive into the caller.
REM   - Inside a parenthesized block, test results with "if errorlevel" and
REM     "if defined", and never read a variable with percent signs that the
REM     same block sets: it still holds the value from before the block.
REM   - Quote every path inside a block. A path with a closing parenthesis in
REM     it would end the block early.
REM   - Never --upgrade a requirement, and never reinstall a torch that imports.
REM   - Offline or a package-server hiccup only warns. The launch is blocked only
REM     when the core packages cannot be imported at all.
REM   - pause only on failures and during first-time setup. The desktop shortcut
REM     runs this minimized, so a pause on a normal launch looks like a hang.

cd /d "%~dp0"

set "WITNESS_FRESH="
set "WITNESS_PY="
set "WITNESS_PYVER="
set "WITNESS_NEEDINSTALL="
set "WITNESS_NOSTAMP="
set "WITNESS_GPU="
set "WITNESS_WANTTORCH="
set "WITNESS_TORCHFAIL="
set "WITNESS_PIPFAIL="
set "WITNESS_PARTIAL="
set "WITNESS_IMPORTFAIL="
set "WITNESS_DIR=%~dp0"

if not exist ".venv\Scripts\python.exe" set "WITNESS_FRESH=1"

REM ===== First-time setup: only when there is no .venv yet. None of this runs
REM ===== on an existing install, so a later system Python change can never
REM ===== stop a working WITNESS from opening.
if defined WITNESS_FRESH (
    echo.
    echo   First-time setup: finding Python, then installing what WITNESS needs.
    echo   This downloads several GB ^(the OCR engine^) and can take a while.
    echo   Please leave this window open until it finishes.
    echo.
)

REM Prefer the py launcher, which python.org installers include: it finds 3.12
REM even when a newer Python is first on PATH. Plain "python" is the last
REM resort. The Microsoft Store shortcut named python exits non-zero here, so
REM it is never picked. PYTHON_MANAGER_AUTOMATIC_INSTALL=false stops the new
REM Python install manager from silently downloading a runtime during the probe.
if defined WITNESS_FRESH set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
if defined WITNESS_FRESH for %%V in (3.12 3.11 3.10) do if not defined WITNESS_PY (
    py -%%V -c "import sys" <nul >nul 2>nul
    if not errorlevel 1 set "WITNESS_PY=py -%%V"
)
if defined WITNESS_FRESH if not defined WITNESS_PY (
    python -c "import sys" <nul >nul 2>nul
    if not errorlevel 1 set "WITNESS_PY=python"
)
if defined WITNESS_FRESH set "PYTHON_MANAGER_AUTOMATIC_INSTALL="

if defined WITNESS_FRESH if not defined WITNESS_PY (
    echo.
    echo   Python 3.12 is not installed, or it is not on your PATH.
    echo   If typing python opens the Microsoft Store, that is only a Windows
    echo   shortcut, not Python.
    echo.
    echo   Install Python 3.12 from this page - pick "Windows installer, 64-bit":
    echo     https://www.python.org/downloads/release/python-31210/
    echo   On the installer's first screen, TICK "Add python.exe to PATH".
    echo   Then run this again.
    echo.
    pause
    exit /b 1
)

if defined WITNESS_FRESH for /f "delims=" %%v in ('%WITNESS_PY% -c "import sys, platform; print(sys.version.split()[0], platform.architecture()[0])"') do set "WITNESS_PYVER=%%v"
if defined WITNESS_FRESH echo   Found Python %WITNESS_PYVER% - using: %WITNESS_PY%
if defined WITNESS_FRESH %WITNESS_PY% -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) and sys.maxsize > 2**32 else 1)" <nul >nul 2>nul
if defined WITNESS_FRESH if errorlevel 1 (
    echo.
    echo   Found Python %WITNESS_PYVER%. WITNESS needs 64-bit Python 3.10 to 3.12:
    echo   the OCR engine does not support newer versions yet.
    echo.
    echo   Install Python 3.12 from this page - pick "Windows installer, 64-bit":
    echo     https://www.python.org/downloads/release/python-31210/
    echo   It can sit alongside Python %WITNESS_PYVER%. TICK "Add python.exe to PATH".
    echo   Then run this again.
    echo.
    pause
    exit /b 1
)

if defined WITNESS_FRESH if /i not "%WITNESS_DIR:\OneDrive=%"=="%WITNESS_DIR%" (
    echo.
    echo   WARNING: this folder is inside OneDrive:
    echo     "%WITNESS_DIR%"
    echo   Setup installs several GB here. OneDrive will try to upload all of it,
    echo   and it can lock files halfway through the install.
    echo   Best: close this window, move the WITNESS folder to a plain local folder
    echo   such as C:\Games\ and run it from there.
    echo   Or press a key to install here anyway.
    echo.
    pause
)

if defined WITNESS_FRESH echo   Creating the Python environment in .venv ...
if defined WITNESS_FRESH %WITNESS_PY% -m venv .venv
if defined WITNESS_FRESH if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Could not create the Python environment ^(.venv^) in this folder.
    echo   Check free disk space and that the folder is not read-only, then run
    echo   this again.
    echo.
    pause
    exit /b 1
)

REM ===== Every launch: activate .venv. The window launcher and the other .bat
REM ===== files rely on "python" meaning the .venv Python after this.
if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo   The Python environment in .venv is incomplete.
    echo   Delete the .venv folder in this WITNESS folder and run START again.
    echo.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

REM ===== Install or repair, only when requirements.txt differs from the copy
REM ===== saved after the last fully good install. fc returns 1 when the files
REM ===== differ and 2 when there is no copy yet, so errorlevel 1 catches both.
REM ===== The copy is written only after every step succeeded, so an interrupted
REM ===== install simply runs again on the next launch.
fc /b requirements.txt ".venv\witness-installed.txt" >nul 2>nul
if errorlevel 1 set "WITNESS_NEEDINSTALL=1"
if not exist ".venv\witness-installed.txt" set "WITNESS_NOSTAMP=1"

if defined WITNESS_NEEDINSTALL if not defined WITNESS_FRESH (
    echo.
    echo   Installing new or missing components. This happens once after an
    echo   update and usually takes under a minute...
    echo.
)

REM Newer pip installs the wheels more reliably. Not fatal: offline it just
REM fails quickly and the install below decides what happens.
if defined WITNESS_NEEDINSTALL python -m pip install --upgrade pip --disable-pip-version-check --retries 1 --timeout 10 >nul 2>nul

REM NVIDIA card: install the CUDA build of PyTorch FIRST, so easyocr does not
REM pull the CPU-only build from PyPI. cu128 covers RTX 20-series through
REM 50-series. Only when torch does not import yet: an install that already
REM has torch keeps its exact build. No --upgrade here, ever.
if defined WITNESS_NEEDINSTALL (
    where nvidia-smi >nul 2>nul
    if not errorlevel 1 set "WITNESS_GPU=1"
)
if defined WITNESS_GPU (
    python -c "import torch" >nul 2>nul
    if errorlevel 1 set "WITNESS_WANTTORCH=1"
)
if defined WITNESS_WANTTORCH (
    echo   NVIDIA GPU detected - installing GPU-accelerated OCR ^(large download^)...
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    if errorlevel 1 set "WITNESS_TORCHFAIL=1"
)
REM A failed GPU download must not fall through to the CPU build: skip the
REM requirements step so easyocr cannot pull CPU torch in its place.
if defined WITNESS_TORCHFAIL (
    echo.
    echo   The GPU-accelerated OCR download did not finish.
    echo.
)

if defined WITNESS_NEEDINSTALL if not defined WITNESS_TORCHFAIL (
    python -m pip install -r requirements.txt
    if errorlevel 1 set "WITNESS_PIPFAIL=1"
)

REM pywebview is left out on purpose: the window app installs it by itself.
if defined WITNESS_NEEDINSTALL (
    python -c "import torch, easyocr, cv2, mss, rapidfuzz, obsws_python, yaml, PIL"
    if errorlevel 1 set "WITNESS_IMPORTFAIL=1"
)
if defined WITNESS_IMPORTFAIL (
    echo.
    echo   Setup did NOT finish. Check your internet connection and free disk
    echo   space, then run this again. If it keeps failing, delete the .venv
    echo   folder in this WITNESS folder and run START again.
    echo.
    pause
    exit /b 1
)

REM The core imports work, so launch even if a download failed: an offline
REM launch of a working install must still open. No copy is saved, so the
REM install is retried on the next launch.
if defined WITNESS_TORCHFAIL set "WITNESS_PARTIAL=1"
if defined WITNESS_PIPFAIL set "WITNESS_PARTIAL=1"
if defined WITNESS_PARTIAL (
    echo.
    echo   WARNING: some components could not be installed - offline, or the
    echo   package server had a hiccup. WITNESS will start anyway and try again
    echo   the next time it is launched.
    echo.
)
if defined WITNESS_NEEDINSTALL if not defined WITNESS_PARTIAL (
    copy /y requirements.txt ".venv\witness-installed.txt" >nul
    echo   Setup complete.
)

REM Setup has never finished on this .venv before, and there is an NVIDIA card:
REM say so if the OCR engine cannot use it, instead of quietly running on CPU.
if defined WITNESS_GPU if defined WITNESS_NOSTAMP (
    python -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo   NOTE: an NVIDIA card was found, but the OCR engine cannot use it, so
        echo   it will run on the CPU - slower, and it can miss fast popups.
        echo   Update the NVIDIA driver and restart the PC. If that does not help,
        echo   delete the .venv folder and run START again.
        echo.
        if defined WITNESS_FRESH pause
    )
)

set "WITNESS_FRESH="
set "WITNESS_PY="
set "WITNESS_PYVER="
set "WITNESS_NEEDINSTALL="
set "WITNESS_NOSTAMP="
set "WITNESS_GPU="
set "WITNESS_WANTTORCH="
set "WITNESS_TORCHFAIL="
set "WITNESS_PIPFAIL="
set "WITNESS_PARTIAL="
set "WITNESS_IMPORTFAIL="
set "WITNESS_DIR="
exit /b 0
