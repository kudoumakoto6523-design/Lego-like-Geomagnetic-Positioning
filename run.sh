#!/usr/bin/env bash

# ----------------------------------------------------------------------------
# 环境检测和自动启动脚本（macOS/Linux）
#
# 该脚本用于检查并配置运行项目所需的 Conda 虚拟环境，然后启动
# Bokeh Web 应用。它会：
#  1. 检测是否已安装 Conda，如果未安装则提示用户安装（中英文提醒）。
#  2. 检查指定名称的虚拟环境是否存在，若不存在则创建。
#  3. 在虚拟环境中安装本项目及其依赖包。
#  4. 启动 geomag_web_app.py 提供的可视化界面。
#
# 使用方法：在项目根目录下运行：
#   bash run.sh
#
# 如果看到 "permission denied" 的错误，请先赋予脚本执行权限：
#   chmod +x run.sh
# 然后使用 Bash 运行脚本。
#
# 注：若希望自动下载 Conda，可修改此脚本，在检测失败时添加下载命令。
# ----------------------------------------------------------------------------

set -e

# 如果脚本不是通过 bash 执行，则给出提示。使用 sh 执行会导致
# “source” 或 “conda activate” 等命令找不到的错误。
if [ -z "$BASH_VERSION" ]; then
    echo "检测到当前 shell 不是 Bash。请使用 bash 来运行此脚本，例如："
    echo "  bash run.sh"
    echo "Detected that current shell is not Bash. Please run this script with Bash, e.g.:"
    echo "  bash run.sh"
    exit 1
fi

# 环境名称可以根据需求修改
ENV_NAME="lego_geomag"

# 确定脚本所在目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "检测 Conda 是否安装 / Checking if Conda is installed ..."
if ! command -v conda >/dev/null 2>&1; then
    echo "未检测到 Conda。请访问 https://docs.conda.io/en/latest/miniconda.html 下载并安装 Miniconda 或 Anaconda，然后重新运行此脚本。"
    echo "Conda not found. Please install Miniconda/Anaconda from https://docs.conda.io/en/latest/miniconda.html and rerun this script."
    exit 1
fi

# 加载 conda 环境脚本
# 这使得我们可以调用 `conda activate`
CONDA_BASE="$(conda info --base)"
source "$CONDA_BASE/etc/profile.d/conda.sh"

echo "检查虚拟环境 ${ENV_NAME} 是否存在 / Checking if environment ${ENV_NAME} exists ..."
if conda env list | grep -q "^${ENV_NAME}\s"; then
    echo "环境 ${ENV_NAME} 已存在 / Environment ${ENV_NAME} already exists."
else
    echo "创建虚拟环境 ${ENV_NAME} / Creating environment ${ENV_NAME} ..."
    conda create -y -n "$ENV_NAME" python=3.11
fi

echo "激活环境 ${ENV_NAME} / Activating environment ${ENV_NAME} ..."
conda activate "$ENV_NAME"

echo "升级 pip 并安装依赖 / Upgrading pip and installing dependencies ..."
# 升级 pip
python -m pip install --upgrade pip
# 安装项目所需的第三方包，这里列出了项目 pyproject.toml 中声明的依赖，以及可视化所需的 bokeh
python -m pip install --quiet numpy pykrige matplotlib bokeh

# 如果项目文件存在，使用编辑模式安装本地包以便开发
if [ -f "$SCRIPT_DIR/pyproject.toml" ] || [ -d "$SCRIPT_DIR/Geomag" ]; then
    echo "安装本地项目 / Installing local project in editable mode ..."
    python -m pip install --quiet -e "$SCRIPT_DIR"
fi

echo "启动 Web 应用 / Launching the web application ..."
exec bokeh serve --show "$SCRIPT_DIR/geomag_web_app.py"