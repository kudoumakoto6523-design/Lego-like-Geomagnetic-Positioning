#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "${GEOMAG_PYTHON_PROJECT:-}" ]; then
    PYTHON_PROJECT="$GEOMAG_PYTHON_PROJECT"
elif [ -f "$APP_ROOT/../main.py" ]; then
    # GeomagMac is checked out inside the Python repository.
    PYTHON_PROJECT="$APP_ROOT/.."
else
    # Standalone Xcode checkout next to the Python repository.
    PYTHON_PROJECT="$APP_ROOT/../Lego-like-Geomagnetic-Positioning"
fi
PYTHON_BIN="${GEOMAG_PYTHON_BIN:-$PYTHON_PROJECT/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "Python environment not found: $PYTHON_BIN" >&2
    echo "Set GEOMAG_PYTHON_BIN or create the original project's .venv." >&2
    exit 1
fi

if [ ! -f "$PYTHON_PROJECT/main.py" ]; then
    echo "Geomagnetic project not found: $PYTHON_PROJECT" >&2
    exit 1
fi

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
    echo "PyInstaller is missing from $PYTHON_BIN" >&2
    echo "Install the pinned build dependency from Backend/requirements-build.txt." >&2
    exit 1
fi

rm -rf "$APP_ROOT/BackendDist/GeomagBackend" "$APP_ROOT/.backendBuild"
mkdir -p "$APP_ROOT/BackendDist" "$APP_ROOT/.backendBuild"

"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --name GeomagBackend \
    --distpath "$APP_ROOT/BackendDist" \
    --workpath "$APP_ROOT/.backendBuild/work" \
    --specpath "$APP_ROOT/.backendBuild" \
    --paths "$PYTHON_PROJECT" \
    --add-data "$PYTHON_PROJECT/data/own_data:data/own_data" \
    --add-data "$PYTHON_PROJECT/data/own_data_package:data/own_data_package" \
    --add-data "$PYTHON_PROJECT/data/processed:data/processed" \
    --add-data "$PYTHON_PROJECT/pyproject.toml:." \
    --hidden-import main \
    "$APP_ROOT/Backend/geomag_backend.py"

echo "Bundled backend: $APP_ROOT/BackendDist/GeomagBackend/GeomagBackend"
