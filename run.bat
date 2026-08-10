@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "PROJECT_DIR=%CD%"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "APP_FILE=%PROJECT_DIR%\geomag_web_app.py"
set "SETUP_ONLY=0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if /I "%~1"=="--setup-only" set "SETUP_ONLY=1"
if not "%~1"=="" if /I not "%~1"=="--setup-only" (
    echo ERROR: Unknown option "%~1".
    echo Usage: run.bat [--setup-only]
    exit /b 2
)

echo ============================================================
echo Geomagnetic Positioning - Windows launcher
echo Project: %PROJECT_DIR%
echo Environment: %VENV_DIR%
echo ============================================================
echo.

if not exist "%APP_FILE%" (
    echo ERROR: geomag_web_app.py was not found.
    echo Keep run.bat in the project root directory.
    exit /b 10
)

if not exist "%VENV_PYTHON%" call :CREATE_ENV
if errorlevel 1 exit /b %ERRORLEVEL%

"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo ERROR: The existing .venv is invalid or uses Python older than 3.11.
    echo Rename or remove "%VENV_DIR%" and run this script again.
    exit /b 12
)

echo Checking the project environment...
"%VENV_PYTHON%" -c "import importlib.metadata as m; import bokeh, gstools, matplotlib, numpy, pandas, pykrige, scipy, Geomag; m.version('geomagnetic-positioning-ddtw')" >nul 2>nul
if errorlevel 1 (
    echo Installing project dependencies ^(first run may take several minutes^)...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check --prefer-binary --retries 5 --timeout 120 --editable "%PROJECT_DIR%"
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency installation failed.
        echo Check the network connection, then run this script again.
        exit /b 13
    )
) else (
    echo Dependencies are already installed.
)

echo.
echo Checking imports...
"%VENV_PYTHON%" -c "import bokeh, gstools, matplotlib, numpy, pandas, pykrige, scipy, Geomag; print('Environment OK - Python', __import__('sys').version.split()[0], '/ Bokeh', bokeh.__version__)"
if errorlevel 1 (
    echo ERROR: The environment verification failed.
    exit /b 14
)

if "%SETUP_ONLY%"=="1" (
    echo.
    echo Setup completed. PyCharm interpreter:
    echo   %VENV_PYTHON%
    exit /b 0
)

echo.
echo Starting the Bokeh web app...
echo URL: http://localhost:5006/geomag_web_app
echo Keep this window open. Press Ctrl+C to stop the server.
echo.
"%VENV_PYTHON%" -m bokeh serve --show "%APP_FILE%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo ERROR: Bokeh stopped with error code %RC%.
)
exit /b %RC%

:CREATE_ENV
echo Creating the project-local Python environment...

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo ERROR: Python launcher could not create the virtual environment.
            exit /b 11
        )
        exit /b 0
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        python -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo ERROR: Python could not create the virtual environment.
            exit /b 11
        )
        exit /b 0
    )
)

echo ERROR: Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo During installation, enable "Python Launcher for Windows".
exit /b 11
