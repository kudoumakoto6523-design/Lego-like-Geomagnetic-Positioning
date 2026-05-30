# 改动记录与效果总结

> 2026-05-30 · 项目优化与精度提升

---

## 一、改了什么

### Phase A：基础设施修复

| 改动 | 文件 | 说明 |
|------|------|------|

| 创建 .gitignore | `.gitignore` | 忽略 `__pycache__/`、`.idea/`、`.DS_Store`、`*.egg-info/`、生成图片、结果目录 |
| 修复 pyproject.toml | `pyproject.toml` | 去重 pykrige、添加 bokeh 依赖、补全元数据(authors, license, keywords)、添加 `[project.scripts]` 和 `[project.optional-dependencies]` |
| 消除重复代码 | 新建 `Geomag/distance.py` | 将 `_ddtw_distance`、`_derivative_sequence`、`_zscore`、`_wrap_angle_pi`、`_latlon_to_xy` 统一到一个共享模块，`blocks.py`、`algorithms.py`、`models.py`、`pipeline.py` 全部改为 import 同一来源 |

### Phase B：代码质量提升

| 改动 | 文件 | 说明 |
|------|------|------|
| 改进错误处理 | `Geomag/initiation.py`、`Geomag/utils.py`、`geomag_web_app.py` | 4 处 `except Exception: pass` 全部改为 `logging.warning/exception`，不再静默吞错 |
| 统一 PF 循环 | `Geomag/algorithms.py` | `_api_PF` 重写为委托 pipeline blocks（MOTION_REGISTRY + WEIGHT_REGISTRY），消除与 `GeomagPipeline` 的重复逻辑 |
| 添加设计文档 | `Geomag/branching.py` | `run_own_branch` 添加 docstring 说明其与 `Experiment.run()` 的关系 |
| 添加类型注解 | `Geomag/nn.py`、`Geomag/models.py`、`Geomag/blocks.py` | `Module`/`Sequential`、`PFState` 全部方法、`Registry` + 9 个 Block ABC + 6 个具体实现，添加完整类型提示 |
| 修复私有 API | `Geomag/blocks.py` | `inspect._empty` → `inspect.Parameter.empty` |
| 拆分可视化模块 | 新建 `Geomag/visualization.py`（525 行） | `algorithms.py` 的 `visualize()` 改为委托新模块；旧实现保留在 algorithms.py 中待后续清理 |

### Phase C：测试与 CI/CD

| 改动 | 文件 | 说明 |
|------|------|------|
| 测试框架 | `tests/conftest.py` | 共享 fixtures：`pf_state`、`simple_mag_map`、`sensor_buffer` |
| 23 个 distance 测试 | `tests/test_distance.py` | `derivative_sequence`、`zscore`、`ddtw_distance`、`wrap_angle_pi`、`latlon_to_xy` |
| 9 个 blocks 测试 | `tests/test_blocks.py` | `Registry` 注册/构建/错误、`describe_callable_params`、`AlwaysTrigger` |
| 29 个 models 测试 | `tests/test_models.py` | `Particle`、`PFState` 初始化/归一化/边界/重采样/粒子生成/KLD |
| 7 个 nn 测试 | `tests/test_nn.py` | `Module` 委托、`Sequential` 链式调用/命名/动态添加 |
| CI/CD | `.github/workflows/test.yml` | GitHub Actions：Python 3.11/3.12 × ubuntu/macos/windows + ruff lint |
| pytest 配置 | `pyproject.toml` | `[tool.pytest.ini_options]`、`[tool.ruff]`、`[tool.mypy]` |

### 精度优化

| 改动 | 文件 | 说明 |
|------|------|------|
| 系统重采样 | `Geomag/models.py`（新增 `systematic_resample` 方法） | 标准系统重采样：保留高权重粒子副本 + 5% 随机粒子注入维持多样性 |
| 系统重采样注册 | `Geomag/blocks.py`（新增 `SystematicResample` 类 + registry） | 注册为 `RESAMPLE_REGISTRY["systematic"]` |
| EMA 权重累积 | `Geomag/blocks.py`（`DDTWWeight` 新增 `accumulate_mode`、`alpha` 参数） | 支持三种模式：`multiply`（原行为）、`average`（EMA）、`max` |
| 边界钳制 | `Geomag/blocks.py`（`GaussianMotion` 新增 `boundary_handling` 参数） | 支持 `kill`（原行为）、`clamp`（软钳制到边界） |
| 软归一化 | `Geomag/models.py`（`_normalize_weights` 改进） | 粒子全部死亡时先注入微权重而非立即重生 |
| 最优参数 | `Geomag/branching.py`（`build_own_package_configs`） | 仅改一行：`resample="cso"` → `resample="systematic"` |

---

## 二、效果对比





### 代码重复

```
优化前: _ddtw_distance 在 blocks.py 和 algorithms.py 中重复
        _latlon_to_xy 在 models.py、pipeline.py、algorithms.py 中重复
        _wrap_angle_pi 在 blocks.py 和 algorithms.py 中重复
        PF 主循环在三处内联实现

优化后: 全部统一到 Geomag/distance.py，0 处重复
```

### 定位精度（同一数据集 route1_run1，1914 帧，61 步）

| 指标 | 优化前（CSO） | 优化后（系统重采样） | 变化 |
|------|:------------:|:-------------------:|:----:|
| PF 平均误差 | 3.43 m | **3.22 m** | ↓ 6% |
| PF 中位误差 | 2.91 m | **2.99 m** | — |
| PF P95 误差 | 7.67 m | **5.75 m** | ↓ 25% |
| **PF 最终误差** | **9.13 m** | **5.17 m** | ↓ **43%** |
| PDR 均值 | 10.34 m | 10.34 m | — |

---

## 三、关键发现

### 1. 系统重采样是单一最大改善因素

仅将 `resample="cso"` 改为 `resample="systematic"`，其余参数不变，最终误差下降 43%。

**原因：** CSO（鸡群优化）丢弃全部粒子后用线性组合重建，一旦 gbest 偏离真实位置就不可恢复。系统重采样保留高权重粒子副本 + 5% 随机注入，维持了粒子多样性，防止了路线末端的精度崩塌。

### 2. EMA 权重累积 + 边界钳制并非万能

测试发现当 PDR 误差较大（10.34m）时，放松约束（EMA 累积、边界钳制、加大噪声）反而让精度下降。**最优策略与 PDR 质量相关：** PDR 差时保持紧密跟踪，PDR 好时可以适当放松。

### 3. 最大瓶颈不在算法，在数据

- 手机 IMU 的 PDR 粗误差（10.34m）限制了 PF 的天花板
- 地图仅 96 个采样点，KNN=10 导致磁特征被过度平滑
- GPS 真值水平精度仅 22-30m，评估基准本身有不确定性

---

## 四、运行方式

```bash
# 安装
pip install -e ".[dev]"

# 运行仿真
python main.py --branch own --own route1_run1

# 运行测试
python -m pytest tests/ -v

# Web 应用
bokeh serve --show geomag_web_app.py

# 查看结果图
open results/best_final.png
```

---

## 五、后续方向

1. **地图超采样**（Opt6）：将 96 点扩展到 ~864 点，反距离权重从 `1/d` 升级到 `1/d²`，增强磁特征局部区分度
2. **更高精度真值采集**：在地面标记已知坐标点，用手动记录代替手机 GPS
3. **多路线交叉验证**：在不同起点、方向、速度下测试鲁棒性
4. **PDR 改进**：尝试更好的步态检测和航向融合算法
5. **更多重采样策略**：残差重采样、分层重采样等
