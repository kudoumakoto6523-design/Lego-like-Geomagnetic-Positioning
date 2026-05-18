@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>nul
cd /d "%~dp0"

call :MAIN
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================
if "%RC%"=="0" (
    echo Finished. If the Bokeh server is still running, keep this window open.
) else (
    echo Script stopped with error code %RC%.
    echo Please copy the full text above and send it to the developer.
)
echo Press any key to close this window...
pause >nul
exit /b %RC%

:MAIN
set "ENV_NAME=lego_geomag"
set "APP_FILE=%CD%\geomag_web_app.py"

echo ============================================================
echo Geomagnetic Web App one-click launcher for Windows
echo Project directory: %CD%
echo Target conda environment: %ENV_NAME%
echo ============================================================
echo.

if not exist "%APP_FILE%" (
    echo ERROR: geomag_web_app.py was not found in this directory.
    echo Please put this BAT file in the project root directory.
    exit /b 10
)

call :FIND_CONDA
if errorlevel 1 exit /b 11

echo Using conda command: %CONDA_CMD%

set "CONDA_BASE="
for /f "delims=" %%B in ('call "%CONDA_CMD%" info --base 2^>nul') do (
    if not defined CONDA_BASE set "CONDA_BASE=%%B"
)

if not defined CONDA_BASE (
    echo ERROR: Your conda is too old or broken.
    echo The command "conda info --base" failed.
    echo.
    echo Your current Anaconda seems to be an old Python-2-era installation.
    echo Please install a current Miniconda or Anaconda, then run this BAT again.
    echo Download page:
    echo https://docs.conda.io/en/latest/miniconda.html
    exit /b 12
)

if exist "%CONDA_BASE%\condabin\conda.bat" (
    set "CONDA_CMD=%CONDA_BASE%\condabin\conda.bat"
)

echo Conda base: %CONDA_BASE%
echo.

echo Checking conda health...
call "%CONDA_CMD%" --version
if errorlevel 1 (
    echo ERROR: conda --version failed.
    exit /b 13
)

echo.
echo Checking whether environment "%ENV_NAME%" exists...
set "ENV_EXISTS=0"
call "%CONDA_CMD%" env list > "%TEMP%\geomag_conda_envs.txt" 2> "%TEMP%\geomag_conda_err.txt"
if errorlevel 1 (
    echo ERROR: "conda env list" failed.
    echo This usually means the local Conda installation or channel configuration is broken.
    echo.
    echo Conda error output:
    type "%TEMP%\geomag_conda_err.txt"
    echo.
    echo Suggested fix:
    echo   1. Install the latest Miniconda or Anaconda.
    echo   2. Or open Anaconda Prompt and run:
    echo      conda config --remove-key channels
    echo      conda config --add channels defaults
    echo      conda update -n base conda
    exit /b 14
)

findstr /R /C:"^%ENV_NAME%[ ][ ]*" "%TEMP%\geomag_conda_envs.txt" >nul 2>nul
if "%ERRORLEVEL%"=="0" set "ENV_EXISTS=1"

if "%ENV_EXISTS%"=="0" (
    echo Creating environment "%ENV_NAME%"...
    call "%CONDA_CMD%" create -y -n "%ENV_NAME%" python=3.11 pip
    if errorlevel 1 (
        echo ERROR: Failed to create conda environment.
        echo If the error mentions channels, please reinstall or update Conda.
        exit /b 15
    )
) else (
    echo Environment "%ENV_NAME%" already exists.
)

echo.
echo Activating environment "%ENV_NAME%"...
call "%CONDA_CMD%" activate "%ENV_NAME%"
if errorlevel 1 (
    echo ERROR: Failed to activate environment "%ENV_NAME%".
    exit /b 16
)

echo.
echo Installing Python dependencies...
python -m pip install --upgrade pip
if errorlevel 1 exit /b 17

python -m pip install numpy pykrige matplotlib bokeh
if errorlevel 1 exit /b 18

if exist "%CD%\pyproject.toml" (
    echo.
    echo Installing local project in editable mode...
    python -m pip install -e "%CD%"
    if errorlevel 1 exit /b 19
)

echo.
echo Starting Bokeh web app...
echo If it starts successfully, this console must remain open.
echo App URL will usually be:
echo   http://localhost:5006/geomag_web_app
echo.
python -m bokeh serve --show "%APP_FILE%"
if errorlevel 1 (
    echo ERROR: Bokeh failed to start.
    exit /b 20
)

exit /b 0

:FIND_CONDA
set "CONDA_CMD="

if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_CMD=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_CMD=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%LOCALAPPDATA%\anaconda3\condabin\conda.bat" set "CONDA_CMD=%LOCALAPPDATA%\anaconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%LOCALAPPDATA%\miniconda3\condabin\conda.bat" set "CONDA_CMD=%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_CMD=C:\ProgramData\anaconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_CMD=C:\ProgramData\miniconda3\condabin\conda.bat"

if not defined CONDA_CMD (
    for /f "delims=" %%C in ('where conda 2^>nul') do (
        if not defined CONDA_CMD set "CONDA_CMD=%%C"
    )
)

if not defined CONDA_CMD (
    echo ERROR: Conda was not found.
    echo Please install Miniconda or Anaconda first:
    echo https://docs.conda.io/en/latest/miniconda.html
    exit /b 1
)

exit /b 0
