#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_NAME="lego_geomag"
APP_FILE="$SCRIPT_DIR/geomag_web_app.py"

echo "========================================"
echo "Geomagnetic Web App launcher"
echo "Project: $SCRIPT_DIR"
echo "========================================"
echo ""

# ---- Try conda first, fall back to venv ----
if command -v conda >/dev/null 2>&1; then
    echo "[conda] Found, setting up environment..."
    CONDA_BASE="$(conda info --base 2>/dev/null || echo "")"

    if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
        source "$CONDA_BASE/etc/profile.d/conda.sh"
    fi

    if ! conda env list 2>/dev/null | grep -q "^${ENV_NAME}\s"; then
        echo "[conda] Creating environment '${ENV_NAME}' with Python 3.11..."
        conda create -y -n "$ENV_NAME" python=3.11 pip
    fi

    conda activate "$ENV_NAME"
    echo "[conda] Environment '${ENV_NAME}' activated."
else
    echo "[venv] conda not found, using Python venv..."
    VENV_DIR="$SCRIPT_DIR/.venv"

    # Find Python 3.11+
    PYTHON_BIN=""
    for p in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$p" >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$p")"
            break
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        echo "ERROR: Python 3.11+ not found."
        echo "Install Miniconda: https://docs.conda.io/en/latest/miniconda.html"
        echo "Or Python: https://www.python.org/downloads/"
        exit 1
    fi

    echo "[venv] Using Python: $PYTHON_BIN"
    "$PYTHON_BIN" --version

    if [ ! -f "$VENV_DIR/bin/python" ]; then
        echo "[venv] Creating virtual environment..."
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi

    source "$VENV_DIR/bin/activate"
    echo "[venv] Virtual environment activated."
fi

echo ""
echo "Installing dependencies..."
python -m pip install --upgrade pip --quiet
python -m pip install numpy pykrige matplotlib bokeh gstools
echo ""

if [ -f "$SCRIPT_DIR/pyproject.toml" ] || [ -d "$SCRIPT_DIR/Geomag" ]; then
    echo "Installing project in editable mode..."
    python -m pip install -e "$SCRIPT_DIR" --quiet
    echo ""
fi

echo "Starting Bokeh web app..."
echo "URL: http://localhost:5006/geomag_web_app"
echo ""
exec python -m bokeh serve --show "$APP_FILE"
