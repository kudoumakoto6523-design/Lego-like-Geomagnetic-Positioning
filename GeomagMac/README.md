# GeomagMac

GeomagMac 是 `Lego-like-Geomagnetic-Positioning` 的原生 macOS 路径查看器。
当前版本把 `xuml-v7-optimization` 分支的修改版定位算法、Python 运行时、依赖和自采数据一起打包进 App，运行时不需要另外安装 Python，也不依赖原项目目录。

> 实时计算和参数预设依赖 `xuml-v7-optimization` 分支新增的算法参数。仓库的 `main` 分支也保留 App 源码和示例查看功能，但构建内置实时后端前应先切换到 `xuml-v7-optimization`。

![GeomagMac 路径查看界面](Docs/GeomagMac-preview.jpeg)

## 运行

1. 使用 Xcode 打开 `GeomagMac.xcodeproj`。
2. 选择 `GeomagMac` scheme 和 `My Mac`。
3. 点击 Run，或按 `Command-R`。

应用内置三组演示结果，也可以通过“打开其他 JSON…”读取 Python 项目生成的结果。

## 当前功能

- 白色真实路线、青色 PDR 路线、黄色虚线 PF 路线
- 三条路线独立显示/隐藏
- 路径逐步播放与进度拖动
- 触控板缩放、鼠标拖动、双击复位
- PF/PDR 平均、P95、中位数和终点误差
- 支持读取现有结果 JSON
- 在 App 内选择 `route1_run2`、`route2_run1` 或 `route2_run2` 并运行 Python 算法
- 默认运行 App 内置的修改版算法后端；缺少内置后端时自动回退到外部 Python 开发模式
- 显示实时进度、日志，支持取消计算
- 提供算法参数预设、自定义参数和自动持久化
- 计算结果保存在 `~/Library/Application Support/GeomagMac/Runs/` 并自动加载

## 算法参数与预设

侧边栏“算法参数”包含三个预设和一个自定义模式：

- **当前优化基线**：默认选项，与现有三组基准结果保持一致，航向不做网格约束。
- **90° 受控路线**：将航向约束到 90° 网格，只适合当前沿砖缝直线行走、直角转弯的精度测试，不应直接用于一般室内路线。
- **原始 PF（不平滑）**：关闭显示轨迹平滑，用于观察粒子滤波原始抖动。
- **自定义**：可以调整 PF 平滑方式与权重、航向网格约束、步长比例、PF 联合校准及实验性三轴矢量地图。

修改任一参数后会自动切换到“自定义”。预设和自定义参数都会保存在本机，下次启动继续使用；点击“恢复当前优化基线”可以回到安全默认值。计算开始后参数会锁定，实际使用的参数同时写入运行日志和结果 JSON。

在 `route2_run2` 上的验证结果：

| 预设 | PF 平均误差 | PF 终点误差 | PDR 平均误差 |
| --- | ---: | ---: | ---: |
| 当前优化基线 | 0.893 m | 1.136 m | 0.732 m |
| 90° 受控路线 | 0.854 m | 1.390 m | 0.688 m |

90°约束降低了这组数据的平均误差，但终点误差反而增大，因此它作为实验预设保留，不替代默认基线。

## 构建内置算法后端

首次构建或算法代码变化后，在 `GeomagMac` 目录运行：

```bash
../.venv/bin/python -m pip install -r Backend/requirements-build.txt
./Scripts/build_backend.sh
```

上面的路径适用于仓库内的 `GeomagMac/` 目录。脚本也支持将
`GeomagMac` 和 Python 仓库并列放置；如果使用其他布局，可通过下方环境变量指定路径。

脚本会生成 `BackendDist/GeomagBackend/`。Xcode 将整个目录复制进 `GeomagMac.app/Contents/Resources/GeomagBackend/`。当前 arm64 后端约 108 MB，包含：

- `xuml-v7-optimization` 分支的当前修改版算法
- Python 解释器与 NumPy、SciPy、Matplotlib 等依赖
- `route1_run2`、`route2_run1`、`route2_run2` 所需数据和地图

如果原算法项目不在相邻目录，可设置：

```bash
GEOMAG_PYTHON_PROJECT=/path/to/Lego-like-Geomagnetic-Positioning \
GEOMAG_PYTHON_BIN=/path/to/python \
./Scripts/build_backend.sh
```

## 外部 Python 开发模式

如果尚未生成 `BackendDist/GeomagBackend`，App 会回退到外部 Python。默认项目目录是 `~/dachuang/Lego-like-Geomagnetic-Positioning`，也可以在侧边栏选择包含 `main.py` 的目录。

点击“开始计算”后，App 等价执行：

```bash
python3 -u main.py --branch own --own route1_run2 --no-show \
  --output-json <App运行目录>/result.json \
  --output-png <App运行目录>/result.png
```

`run.sh` 和 Bokeh 网页入口仍然保留，但原生 App 的正式运行路径不再经过它们。

## 后续阶段

- 增加结果 PNG/CSV 导出
- 对 App 和内置后端做 Developer ID 签名、公证与发布打包
