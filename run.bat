@echo off
REM -------------------------------------------------------------------------
REM  环境检测和自动启动脚本（Windows）
REM
REM  该批处理脚本用于配置并运行本项目在 Windows 环境下的虚拟环境。
REM  它会：
REM   1. 检测是否安装了 Conda。如果未安装则提示用户手动安装。
REM   2. 检查指定名称的 Conda 环境是否存在，若不存在则创建。
REM   3. 在虚拟环境中安装项目依赖和本地项目。
REM   4. 启动 Bokeh Web 应用。
REM
REM  使用方法：双击此脚本或在命令提示符中运行：
REM    setup_and_run_windows.bat
REM
REM  注意：此脚本假设 Conda 已经通过 `conda init` 初始化以支持 `conda activate`。
REM -------------------------------------------------------------------------

SETLOCAL
SET ENV_NAME=lego_geomag
SET "PROJECT_DIR=%~dp0"

echo Checking if Conda is installed ...
where conda >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo 未检测到 Conda。请从 https://docs.conda.io/en/latest/miniconda.html 下载并安装 Miniconda 或 Anaconda。^& echo.
    echo Conda not found. Please install Miniconda/Anaconda from https://docs.conda.io/en/latest/miniconda.html and rerun this script.
    echo.
    pause
    GOTO :EOF
)

REM Initialize conda for use in batch mode
FOR /F "delims=" %%i IN ('conda info --base') DO SET "CONDA_BASE=%%i"
CALL "%CONDA_BASE%\condabin\conda.bat" activate >nul 2>nul

echo Checking if environment %ENV_NAME% exists ...
conda env list | findstr /C:" %ENV_NAME% " >nul
IF %ERRORLEVEL% NEQ 0 (
    echo Creating environment %ENV_NAME% ...
    conda create -y -n %ENV_NAME% python=3.11
)

echo Activating environment %ENV_NAME% ...
CALL conda activate %ENV_NAME%

echo Upgrading pip and installing dependencies ...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet numpy pykrige matplotlib bokeh

REM Install local project in editable mode if present
IF EXIST "%PROJECT_DIR%pyproject.toml" (
    echo Installing local project ...
    python -m pip install --quiet -e "%PROJECT_DIR%"
)

echo Launching the web application ...
bokeh serve --show "%PROJECT_DIR%geomag_web_app.py"

ENDLOCAL