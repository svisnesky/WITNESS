@echo off
REM Shared setup/activation used by the other .bat launchers.
REM First run: creates the Python environment and installs dependencies.
REM Later runs: just activates it. Not meant to be run on its own.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python is not installed, or not on your PATH.
    echo   Install Python 3.12 from https://www.python.org/downloads/windows/
    echo   and CHECK the box "Add python.exe to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM Torch doesn't always support the newest Python yet - warn early instead of
REM failing halfway through a multi-GB download.
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] <= (3,12) else 1)" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Your Python is newer than the OCR engine supports.
    echo   Install Python 3.12 from https://www.python.org/downloads/windows/
    echo   ^(it can live alongside your current version^).
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo   First-time setup: creating the environment and installing dependencies.
    echo   This downloads a lot ^(the OCR engine^) and can take several minutes.
    echo   Please leave this window open until it finishes.
    echo.
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip

    REM NVIDIA GPU? Install CUDA-enabled PyTorch FIRST so OCR runs on the GPU
    REM (much better detection). cu128 covers RTX 20-series through 50-series.
    where nvidia-smi >nul 2>nul
    if not errorlevel 1 (
        echo   NVIDIA GPU detected - installing GPU-accelerated OCR ^(large download^)...
        python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    )

    python -m pip install -r requirements.txt
    echo.
    echo   Setup complete.
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)
